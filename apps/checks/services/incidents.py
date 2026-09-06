"""Translation of alert events into operator-visible incidents.

An :class:`~apps.checks.models.AlertEvent` records one firing of one rule.
An :class:`~apps.checks.models.Incident` is the coarser thing a human wants to
see: this check is down, since this time, for this long. A check has at most
one open incident, so a rule that keeps firing extends the existing incident
rather than creating a new one each time.
"""

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.checks.models import AlertEvent, Incident

logger = logging.getLogger(__name__)


def open_or_extend_incident(check, message, *, severity=None, details=None):
    """Open an incident for ``check``, or extend the open one.

    Returns the open incident. Concurrent probe workers can race here, so the
    unique constraint on (check, state=open) is caught and treated as "someone
    else opened it first".
    """
    severity = severity or Incident.Severity.CRITICAL
    details = details or {}

    existing = Incident.objects.filter(check_ref=check, state=Incident.State.OPEN).first()
    if existing is not None:
        # Keep the newest explanation, but preserve the original start time:
        # the incident began when the check first went down.
        existing.message = message
        existing.details_json = {**existing.details_json, **details}
        existing.save(update_fields=["message", "details_json"])
        return existing

    try:
        with transaction.atomic():
            return Incident.objects.create(
                check_ref=check,
                state=Incident.State.OPEN,
                severity=severity,
                message=message,
                started_at=timezone.now(),
                details_json=details,
            )
    except IntegrityError:
        # Another worker won the race; use theirs.
        logger.debug("Incident already opened concurrently", extra={"check_id": check.id})
        return Incident.objects.filter(check_ref=check, state=Incident.State.OPEN).first()


def resolve_incident(check, *, details=None):
    """Resolve the open incident for ``check``, if there is one."""
    incident = Incident.objects.filter(check_ref=check, state=Incident.State.OPEN).first()
    if incident is None:
        return None

    incident.state = Incident.State.RESOLVED
    incident.resolved_at = timezone.now()
    if details:
        incident.details_json = {**incident.details_json, **details}
    incident.save(update_fields=["state", "resolved_at", "details_json"])
    return incident


def sync_incident_for_event(event):
    """Open or resolve an incident in response to an alert event."""
    if event.state == AlertEvent.State.TRIGGERED:
        return open_or_extend_incident(
            event.check_ref,
            event.message,
            details={"alert_event_id": event.id, "rule_id": event.rule_id},
        )

    return resolve_incident(
        event.check_ref,
        details={"resolved_by_alert_event_id": event.id},
    )
