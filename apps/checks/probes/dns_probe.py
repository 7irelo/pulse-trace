from time import perf_counter

import dns.exception
import dns.resolver

from apps.checks.probes.common import ProbeExecutionError, ProbeValidationError, elapsed_ms
from apps.checks.validators import validate_hostname_or_ip


def run_dns_probe(target, port=None, timeout=5.0):
    del port
    try:
        validate_hostname_or_ip(target)
    except Exception as exc:
        raise ProbeValidationError(str(exc)) from exc

    resolver = dns.resolver.Resolver(configure=True)
    resolver.timeout = min(timeout, 2.0)
    resolver.lifetime = timeout
    record_types = ("A", "AAAA", "CNAME")
    records = {}
    timings = {}
    total_start = perf_counter()

    for record_type in record_types:
        lookup_start = perf_counter()
        key = record_type.lower()
        try:
            answer = resolver.resolve(
                target,
                record_type,
                lifetime=timeout,
                raise_on_no_answer=False,
            )
            records[key] = [r.to_text() for r in answer] if answer else []
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            records[key] = []
        except dns.exception.Timeout as exc:
            raise ProbeExecutionError("DNS lookup timed out.") from exc
        except dns.exception.DNSException as exc:
            raise ProbeExecutionError(f"DNS lookup failed ({exc.__class__.__name__}).") from exc
        timings[f"{key}_lookup_ms"] = elapsed_ms(lookup_start)

    timings["total_ms"] = elapsed_ms(total_start)
    return {
        "timings": timings,
        "details": {"records": records},
    }
