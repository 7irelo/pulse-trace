import logging

from celery import shared_task
from django.utils import timezone
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_random_exponential

from apps.checks.models import Check, CheckResult
from apps.checks.probes import run_dns_probe, run_http_probe, run_tcp_probe, run_tls_probe
from apps.checks.probes.common import ProbeError
from apps.checks.services.alerting import evaluate_alerts_for_result
from apps.checks.services.scheduler import get_due_checks
from apps.checks.validators import sanitize_error_message

logger = logging.getLogger(__name__)

PROBE_HANDLERS = {
    Check.Type.DNS: run_dns_probe,
    Check.Type.TCP: run_tcp_probe,
    Check.Type.TLS: run_tls_probe,
    Check.Type.HTTP: run_http_probe,
}


def _should_retry(exc):
    return isinstance(exc, ProbeError) and bool(getattr(exc, "retryable", False))


def _execute_with_retries(check):
    probe = PROBE_HANDLERS.get(check.type)
    if not probe:
        raise ValueError(f"No probe handler for check type: {check.type}")

    attempts = max(1, int(check.retries) + 1)

    @retry(
        stop=stop_after_attempt(attempts),
        wait=wait_random_exponential(multiplier=0.2, max=2.0),
        retry=retry_if_exception(_should_retry),
        reraise=True,
    )
    def _run():
        return probe(target=check.target, port=check.port, timeout=float(check.timeout_seconds))

    return _run()


@shared_task(name="apps.checks.tasks.run_check")
def run_check(check_id):
    try:
        check = Check.objects.get(id=check_id)
    except Check.DoesNotExist:
        logger.warning("Skipping run: missing check", extra={"check_id": check_id})
        return {"status": "missing"}

    if not check.enabled:
        logger.info("Skipping run: check disabled", extra={"check_id": check.id})
        return {"status": "disabled"}

    started_at = timezone.now()
    status_value = CheckResult.Status.FAIL
    timings = {}
    details = {}
    error_message = ""
    try:
        payload = _execute_with_retries(check)
        timings = payload.get("timings", {})
        details = payload.get("details", {})
        status_value = CheckResult.Status.OK
    except Exception as exc:
        error_message = sanitize_error_message(exc)
        details = {"error_type": exc.__class__.__name__}
        logger.warning(
            "Check run failed",
            extra={
                "check_id": check.id,
                "check_type": check.type,
                "target": check.target,
                "error": error_message,
            },
        )
    finished_at = timezone.now()

    result = CheckResult.objects.create(
        check_ref=check,
        started_at=started_at,
        finished_at=finished_at,
        status=status_value,
        timings_json=timings,
        details_json=details,
        error_message=error_message,
    )
    evaluate_alerts_for_result(result)
    return {"status": status_value, "result_id": result.id}


@shared_task(name="apps.checks.tasks.enqueue_due_checks")
def enqueue_due_checks():
    due = get_due_checks(timezone.now())
    for check in due:
        run_check.delay(check.id)
    logger.info("Enqueued due checks", extra={"count": len(due)})
    return {"enqueued": len(due)}
