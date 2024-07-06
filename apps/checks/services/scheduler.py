from django.db.models import OuterRef, Subquery
from django.utils import timezone

from apps.checks.models import Check, CheckResult


def get_due_checks(reference_time=None):
    reference_time = reference_time or timezone.now()
    latest = CheckResult.objects.filter(check_ref=OuterRef("pk")).order_by("-started_at", "-id")
    checks = Check.objects.filter(enabled=True).annotate(
        latest_started_at=Subquery(latest.values("started_at")[:1])
    )

    due = []
    for check in checks:
        if check.latest_started_at is None:
            due.append(check)
            continue
        elapsed = (reference_time - check.latest_started_at).total_seconds()
        if elapsed >= check.frequency_seconds:
            due.append(check)
    return due
