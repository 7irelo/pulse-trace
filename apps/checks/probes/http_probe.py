from time import perf_counter

import httpx

from apps.checks.probes.common import ProbeExecutionError, ProbeValidationError, elapsed_ms
from apps.checks.validators import validate_http_url


def run_http_probe(target, port=None, timeout=5.0):
    del port
    try:
        validate_http_url(target)
    except Exception as exc:
        raise ProbeValidationError(str(exc)) from exc

    start = perf_counter()
    timeout_config = httpx.Timeout(timeout=float(timeout), connect=float(timeout))
    status_code = None
    final_url = target
    bytes_read = 0
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout_config) as client:
            with client.stream("GET", target) as response:
                ttfb_ms = elapsed_ms(start)
                status_code = response.status_code
                final_url = str(response.url)
                for chunk in response.iter_bytes():
                    bytes_read += len(chunk)
    except httpx.TimeoutException as exc:
        raise ProbeExecutionError("HTTP request timed out.") from exc
    except httpx.HTTPError as exc:
        raise ProbeExecutionError("HTTP request failed.") from exc

    return {
        "timings": {
            "ttfb_ms": ttfb_ms,
            "total_ms": elapsed_ms(start),
        },
        "details": {
            "status_code": status_code,
            "final_url": final_url,
            "bytes_read": bytes_read,
        },
    }
