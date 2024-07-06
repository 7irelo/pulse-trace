import ipaddress
import re
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError

HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-zA-Z0-9-]{1,63}(?<!-)(\.(?!-)[a-zA-Z0-9-]{1,63}(?<!-))*\.?$"
)


def sanitize_error_message(error, max_length=300):
    raw = str(error) if error else "Unknown error."
    clean = re.sub(r"[\r\n\t]+", " ", raw).strip()
    clean = re.sub(r"\s{2,}", " ", clean)
    if not clean:
        clean = "Unknown error."
    return clean[:max_length]


def is_valid_hostname_or_ip(value):
    if not value:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return bool(HOSTNAME_RE.match(value))


def validate_hostname_or_ip(value):
    if not is_valid_hostname_or_ip(value):
        raise ValidationError("Must be a valid hostname or IP address.")


def validate_port(port):
    if port is None:
        return
    if not isinstance(port, int):
        raise ValidationError("Port must be an integer.")
    if port < 1 or port > 65535:
        raise ValidationError("Port must be between 1 and 65535.")


def validate_http_url(value):
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError("URL scheme must be http or https.")
    if not parsed.netloc:
        raise ValidationError("URL must include a host.")
    if parsed.username or parsed.password:
        raise ValidationError("URL must not include credentials.")
    return parsed


def is_webhook_allowed(webhook_url):
    parsed = urlparse(webhook_url)
    host = (parsed.hostname or "").lower()
    allowlist = [d.lower() for d in settings.ALERT_WEBHOOK_ALLOWLIST]
    if not allowlist or not host:
        return False

    for allowed in allowlist:
        if host == allowed or host.endswith(f".{allowed}"):
            return True
    return False


def validate_webhook_url(webhook_url):
    parsed = urlparse(webhook_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError("Webhook URL must use http or https.")
    if not parsed.netloc:
        raise ValidationError("Webhook URL must include a host.")
    if not is_webhook_allowed(webhook_url):
        raise ValidationError("Webhook domain is not in ALERT_WEBHOOK_ALLOWLIST.")


def validate_check_target(check_type, target, port):
    check_type = (check_type or "").lower()
    if check_type not in {"dns", "tcp", "tls", "http"}:
        raise ValidationError("Invalid check type.")

    if check_type in {"dns", "tcp", "tls"}:
        validate_hostname_or_ip(target)
    elif check_type == "http":
        validate_http_url(target)

    if check_type == "dns" and port is not None:
        raise ValidationError("DNS checks do not accept a separate port.")
    if check_type == "http" and port is not None:
        raise ValidationError("HTTP checks must include port in URL, not in the port field.")

    if check_type == "tcp" and port is None:
        raise ValidationError("TCP checks require a port.")
    validate_port(port)
