"""Delivery of alert events to notification channels.

Each channel kind has a small sender function. Every sender is best-effort: a
channel that fails is logged and skipped so that one broken Slack webhook
cannot stop an email from going out, and so that a delivery failure never
prevents the alert event itself from being recorded.

Outbound URLs are constrained by ``ALERT_WEBHOOK_ALLOWLIST`` exactly as the
pre-existing per-rule webhook is, so adding channels does not widen the
server-side request forgery surface.
"""

import logging
from urllib.parse import urlparse

import httpx
from django.conf import settings
from django.core.mail import send_mail

from apps.checks.models import NotificationChannel
from apps.checks.validators import is_webhook_allowed

logger = logging.getLogger(__name__)


def build_payload(event):
    """The canonical JSON body describing an alert event."""
    return {
        "event_id": event.id,
        "state": event.state,
        "message": event.message,
        "check_id": event.check_ref_id,
        "check_name": event.check_ref.name,
        "rule_id": event.rule_id,
        "created_at": event.created_at.isoformat(),
    }


def notify_channels(rule, event):
    """Deliver ``event`` to every enabled channel attached to ``rule``."""
    for channel in rule.channels.filter(enabled=True).order_by("id"):
        try:
            send_to_channel(channel, event)
        except Exception:
            # Never let one channel's failure affect the others.
            logger.exception(
                "Failed to deliver alert to channel",
                extra={"channel_id": channel.id, "kind": channel.kind},
            )


def send_to_channel(channel, event):
    sender = _SENDERS.get(channel.kind)
    if sender is None:
        logger.warning("No sender for channel kind", extra={"kind": channel.kind})
        return
    sender(channel, event)


def _send_email(channel, event):
    recipients = channel.config_json.get("recipients") or []
    if not recipients:
        return

    subject = f"[PulseTrace] {event.state}: {event.check_ref.name}"
    body = (
        f"{event.message}\n\n"
        f"Check: {event.check_ref.name} ({event.check_ref.type})\n"
        f"Target: {event.check_ref.target}\n"
        f"State: {event.state}\n"
        f"Time: {event.created_at.isoformat()}\n"
    )

    send_mail(
        subject=subject,
        message=body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=recipients,
        fail_silently=False,
    )


def _send_slack(channel, event):
    webhook_url = channel.config_json.get("webhook_url")
    if not webhook_url or not _allowed(webhook_url, channel):
        return

    emoji = ":white_check_mark:" if event.state == "resolved" else ":rotating_light:"
    text = f"{emoji} *{event.check_ref.name}* — {event.message}"

    payload = {"text": text}
    # Slack incoming webhooks ignore "channel" unless the hook allows an
    # override, but passing it through is harmless and useful when it does.
    if channel.config_json.get("channel"):
        payload["channel"] = channel.config_json["channel"]

    httpx.post(
        webhook_url,
        json=payload,
        timeout=settings.ALERT_WEBHOOK_TIMEOUT_SECONDS,
    )


def _send_webhook(channel, event):
    url = channel.config_json.get("url")
    if not url or not _allowed(url, channel):
        return

    httpx.post(
        url,
        json=build_payload(event),
        timeout=settings.ALERT_WEBHOOK_TIMEOUT_SECONDS,
    )


def _send_sms(channel, event):
    """Deliver an SMS through a generic HTTP gateway.

    There is no bundled SMS provider. A channel supplies ``gateway_url`` and
    the numbers to notify; the gateway receives the same JSON payload as a
    webhook plus a rendered ``text`` and the recipient list. Without a gateway
    the channel is inert and says so, rather than failing silently.
    """
    numbers = channel.config_json.get("numbers") or []
    gateway_url = channel.config_json.get("gateway_url")

    if not gateway_url:
        logger.warning(
            "SMS channel has no gateway_url configured; nothing sent",
            extra={"channel_id": channel.id},
        )
        return
    if not numbers or not _allowed(gateway_url, channel):
        return

    payload = build_payload(event)
    payload["to"] = numbers
    payload["text"] = f"PulseTrace {event.state}: {event.check_ref.name} - {event.message}"

    httpx.post(
        gateway_url,
        json=payload,
        timeout=settings.ALERT_WEBHOOK_TIMEOUT_SECONDS,
    )


def _allowed(url, channel):
    if is_webhook_allowed(url):
        return True
    logger.warning(
        "Channel URL skipped due to allowlist policy",
        extra={"channel_id": channel.id, "host": urlparse(url).hostname},
    )
    return False


_SENDERS = {
    NotificationChannel.Kind.EMAIL: _send_email,
    NotificationChannel.Kind.SLACK: _send_slack,
    NotificationChannel.Kind.WEBHOOK: _send_webhook,
    NotificationChannel.Kind.SMS: _send_sms,
}
