import socket
from time import perf_counter

from apps.checks.probes.common import ProbeExecutionError, ProbeValidationError, elapsed_ms
from apps.checks.validators import validate_hostname_or_ip, validate_port


def run_tcp_probe(target, port=None, timeout=5.0):
    try:
        validate_hostname_or_ip(target)
    except Exception as exc:
        raise ProbeValidationError(str(exc)) from exc

    if port is None:
        raise ProbeValidationError("TCP check requires a port.")
    try:
        validate_port(port)
    except Exception as exc:
        raise ProbeValidationError(str(exc)) from exc

    connect_start = perf_counter()
    try:
        with socket.create_connection((target, int(port)), timeout=float(timeout)):
            pass
    except TimeoutError as exc:
        raise ProbeExecutionError("TCP connect timed out.") from exc
    except OSError as exc:
        raise ProbeExecutionError("TCP connect failed.") from exc

    connect_ms = elapsed_ms(connect_start)
    return {
        "timings": {"connect_ms": connect_ms, "total_ms": connect_ms},
        "details": {"remote": f"{target}:{port}"},
    }
