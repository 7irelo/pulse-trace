import logging
from urllib.parse import urlparse

import httpx
from django.conf import settings

from apps.checks.models import AlertEvent, AlertRule, CheckResult
from apps.checks.validators import is_webhook_allowed

logger = logging.getLogger(__name__)


def evaluate_alerts_for_result(result):
    check = result.check_ref
    rules = check.alert_rules.filter(enabled=True).order_by("id")
    for rule in rules:
        should_trigger = _rule_triggered(check.id, rule)
        last_event = rule.events.order_by("-created_at", "-id").first()
        currently_triggered = bool(last_event and last_event.state == AlertEvent.State.TRIGGERED)

        if should_trigger and not currently_triggered:
            message = _build_trigger_message(rule)
            event = AlertEvent.objects.create(
                check_ref=check,
                rule=rule,
                state=AlertEvent.State.TRIGGERED,
                message=message,
                details_json={"latest_result_id": result.id},
            )
            _send_webhook_if_enabled(rule, event)

        if not should_trigger and currently_triggered:
            event = AlertEvent.objects.create(
                check_ref=check,
                rule=rule,
                state=AlertEvent.State.RESOLVED,
                message=f"Alert resolved for rule {rule.id}.",
                details_json={"latest_result_id": result.id},
            )
            _send_webhook_if_enabled(rule, event)


def _rule_triggered(check_id, rule):
    if rule.mode == AlertRule.Mode.CONSECUTIVE_FAILURES:
        limit = int(rule.consecutive_failures_count or 0)
        recent = list(
            CheckResult.objects.filter(check_ref_id=check_id)
            .order_by("-started_at", "-id")
            .values_list("status", flat=True)[:limit]
        )
        return len(recent) == limit and all(status == CheckResult.Status.FAIL for status in recent)

    if rule.mode == AlertRule.Mode.LATENCY_THRESHOLD:
        runs = int(rule.latency_run_count or 0)
        threshold = float(rule.latency_ms_threshold or 0)
        recent = list(
            CheckResult.objects.filter(check_ref_id=check_id, status=CheckResult.Status.OK)
            .order_by("-started_at", "-id")
            .values_list("timings_json", flat=True)[:runs]
        )
        if len(recent) < runs:
            return False
        for timings in recent:
            total = timings.get("total_ms") if isinstance(timings, dict) else None
            if not isinstance(total, (int, float)) or float(total) <= threshold:
                return False
        return True

    return False


def _build_trigger_message(rule):
    if rule.mode == AlertRule.Mode.CONSECUTIVE_FAILURES:
        return f"Triggered: {rule.consecutive_failures_count} consecutive failures."
    if rule.mode == AlertRule.Mode.LATENCY_THRESHOLD:
        return (
            "Triggered: total latency above "
            f"{rule.latency_ms_threshold}ms for {rule.latency_run_count} runs."
        )
    return "Triggered."


def _send_webhook_if_enabled(rule, event):
    if not rule.webhook_url:
        return
    if not is_webhook_allowed(rule.webhook_url):
        logger.warning(
            "Webhook skipped due to allowlist policy",
            extra={"rule_id": rule.id, "host": urlparse(rule.webhook_url).hostname},
        )
        return

    payload = {
        "event_id": event.id,
        "state": event.state,
        "message": event.message,
        "check_id": event.check_ref_id,
        "rule_id": event.rule_id,
        "created_at": event.created_at.isoformat(),
    }

    try:
        httpx.post(
            rule.webhook_url,
            json=payload,
            timeout=settings.ALERT_WEBHOOK_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.exception("Failed to send alert webhook", extra={"rule_id": rule.id})
