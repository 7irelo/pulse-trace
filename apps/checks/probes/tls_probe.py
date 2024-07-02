import datetime as dt
import socket
import ssl
from time import perf_counter

from apps.checks.probes.common import ProbeExecutionError, ProbeValidationError, elapsed_ms
from apps.checks.validators import validate_hostname_or_ip, validate_port


def _name_to_string(name_parts):
    output = []
    for item in name_parts or []:
        for key, value in item:
            output.append(f"{key}={value}")
    return ", ".join(output)


def _parse_not_after(raw):
    if not raw:
        return None
    parsed = dt.datetime.strptime(raw, "%b %d %H:%M:%S %Y %Z")
    return parsed.replace(tzinfo=dt.UTC)


def run_tls_probe(target, port=None, timeout=5.0):
    try:
        validate_hostname_or_ip(target)
    except Exception as exc:
        raise ProbeValidationError(str(exc)) from exc

    effective_port = int(port or 443)
    try:
        validate_port(effective_port)
    except Exception as exc:
        raise ProbeValidationError(str(exc)) from exc

    total_start = perf_counter()
    context = ssl.create_default_context()
    try:
        tcp_start = perf_counter()
        with socket.create_connection((target, effective_port), timeout=float(timeout)) as sock:
            tcp_ms = elapsed_ms(tcp_start)
            tls_start = perf_counter()
            with context.wrap_socket(sock, server_hostname=target) as tls_sock:
                handshake_ms = elapsed_ms(tls_start)
                cert = tls_sock.getpeercert()
    except TimeoutError as exc:
        raise ProbeExecutionError("TLS connect timed out.") from exc
    except ssl.SSLError as exc:
        raise ProbeExecutionError("TLS handshake failed.") from exc
    except OSError as exc:
        raise ProbeExecutionError("TLS connection failed.") from exc

    not_after = _parse_not_after(cert.get("notAfter"))
    now = dt.datetime.now(dt.UTC)
    days_until_expiry = None
    if not_after is not None:
        days_until_expiry = (not_after - now).days

    return {
        "timings": {
            "tcp_connect_ms": tcp_ms,
            "tls_handshake_ms": handshake_ms,
            "total_ms": elapsed_ms(total_start),
        },
        "details": {
            "remote": f"{target}:{effective_port}",
            "certificate": {
                "subject": _name_to_string(cert.get("subject", [])),
                "issuer": _name_to_string(cert.get("issuer", [])),
                "not_after": not_after.isoformat() if not_after else None,
                "days_until_expiry": days_until_expiry,
            },
        },
    }
