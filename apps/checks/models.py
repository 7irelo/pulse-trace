import re
from zoneinfo import available_timezones

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, validate_email
from django.db import models
from django.utils import timezone

from apps.checks.validators import validate_check_target, validate_webhook_url

# E.164: a leading + then 1-15 digits, first of which is non-zero.
E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")


class Check(models.Model):
    class Type(models.TextChoices):
        DNS = "dns", "DNS"
        TCP = "tcp", "TCP"
        TLS = "tls", "TLS"
        HTTP = "http", "HTTP"

    name = models.CharField(max_length=120, unique=True)
    type = models.CharField(max_length=10, choices=Type.choices)
    target = models.CharField(max_length=255)
    port = models.PositiveIntegerField(null=True, blank=True)
    frequency_seconds = models.PositiveIntegerField(
        default=60,
        validators=[MinValueValidator(30), MaxValueValidator(86400)],
    )
    timeout_seconds = models.PositiveIntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(120)],
    )
    retries = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
    )
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def clean(self):
        validate_check_target(self.type, self.target, self.port)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.type})"


class CheckResult(models.Model):
    class Status(models.TextChoices):
        OK = "ok", "OK"
        FAIL = "fail", "Fail"

    check_ref = models.ForeignKey(Check, related_name="results", on_delete=models.CASCADE)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    status = models.CharField(max_length=10, choices=Status.choices)
    timings_json = models.JSONField(default=dict, blank=True)
    details_json = models.JSONField(default=dict, blank=True)
    error_message = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["-started_at", "-id"]
        indexes = [
            models.Index(
                fields=["check_ref", "-started_at"],
                name="chkres_chkref_started_idx",
            ),
            models.Index(fields=["status"], name="chkres_status_idx"),
        ]

    def __str__(self):
        return f"{self.check_ref.name}: {self.status} @ {self.started_at.isoformat()}"


class AlertRule(models.Model):
    class Mode(models.TextChoices):
        CONSECUTIVE_FAILURES = "consecutive_failures", "Consecutive failures"
        LATENCY_THRESHOLD = "latency_threshold", "Latency threshold"

    check_ref = models.ForeignKey(Check, related_name="alert_rules", on_delete=models.CASCADE)
    mode = models.CharField(max_length=32, choices=Mode.choices)
    consecutive_failures_count = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(2), MaxValueValidator(20)],
    )
    latency_ms_threshold = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(600000)],
    )
    latency_run_count = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(20)],
    )
    enabled = models.BooleanField(default=True)
    webhook_url = models.URLField(blank=True, default="")
    # Additional delivery destinations. webhook_url above is kept so existing
    # rules keep working without migration of their data.
    channels = models.ManyToManyField("NotificationChannel", related_name="rules", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def clean(self):
        if self.mode == self.Mode.CONSECUTIVE_FAILURES:
            if not self.consecutive_failures_count:
                raise ValidationError(
                    {"consecutive_failures_count": "Required for consecutive_failures mode."}
                )
        elif self.mode == self.Mode.LATENCY_THRESHOLD:
            missing = {}
            if not self.latency_ms_threshold:
                missing["latency_ms_threshold"] = "Required for latency_threshold mode."
            if not self.latency_run_count:
                missing["latency_run_count"] = "Required for latency_threshold mode."
            if missing:
                raise ValidationError(missing)
        else:
            raise ValidationError({"mode": "Invalid mode."})

        if self.webhook_url:
            validate_webhook_url(self.webhook_url)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Rule {self.id}: {self.mode}"


class AlertEvent(models.Model):
    class State(models.TextChoices):
        TRIGGERED = "triggered", "Triggered"
        RESOLVED = "resolved", "Resolved"

    check_ref = models.ForeignKey(Check, related_name="alert_events", on_delete=models.CASCADE)
    rule = models.ForeignKey(AlertRule, related_name="events", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    message = models.CharField(max_length=500)
    state = models.CharField(max_length=16, choices=State.choices)
    details_json = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["rule", "-created_at"],
                name="alertev_rule_created_idx",
            ),
            models.Index(
                fields=["check_ref", "-created_at"],
                name="alertev_chkref_created_idx",
            ),
        ]

    def __str__(self):
        return f"{self.state}: {self.message}"


class NotificationChannel(models.Model):
    """A destination that alert notifications are delivered to.

    The per-kind settings live in ``config_json`` rather than in separate
    columns, so adding a delivery kind does not require a schema change. What
    each kind expects is documented on :meth:`clean`, which is also what
    validates it.
    """

    class Kind(models.TextChoices):
        EMAIL = "email", "Email"
        SLACK = "slack", "Slack"
        WEBHOOK = "webhook", "Webhook"
        SMS = "sms", "SMS"

    name = models.CharField(max_length=120, unique=True)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    enabled = models.BooleanField(default=True)
    config_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def clean(self):
        config = self.config_json or {}
        if not isinstance(config, dict):
            raise ValidationError({"config_json": "Must be a JSON object."})

        if self.kind == self.Kind.EMAIL:
            recipients = config.get("recipients")
            if not isinstance(recipients, list) or not recipients:
                raise ValidationError(
                    {"config_json": "Email channels require a non-empty 'recipients' list."}
                )
            for address in recipients:
                if not isinstance(address, str):
                    raise ValidationError({"config_json": "Each recipient must be a string."})
                validate_email(address)

        elif self.kind == self.Kind.SLACK:
            webhook_url = config.get("webhook_url")
            if not webhook_url:
                raise ValidationError({"config_json": "Slack channels require a 'webhook_url'."})
            validate_webhook_url(webhook_url)

        elif self.kind == self.Kind.WEBHOOK:
            webhook_url = config.get("url")
            if not webhook_url:
                raise ValidationError({"config_json": "Webhook channels require a 'url'."})
            validate_webhook_url(webhook_url)

        elif self.kind == self.Kind.SMS:
            numbers = config.get("numbers")
            if not isinstance(numbers, list) or not numbers:
                raise ValidationError(
                    {"config_json": "SMS channels require a non-empty 'numbers' list."}
                )
            for number in numbers:
                if not isinstance(number, str) or not E164_RE.match(number):
                    raise ValidationError(
                        {"config_json": f"'{number}' is not a valid E.164 phone number."}
                    )

        else:
            raise ValidationError({"kind": "Invalid channel kind."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.kind})"


class Incident(models.Model):
    """An operator-visible outage record for a check.

    An :class:`AlertEvent` is one firing of one rule; an incident is the
    user-facing grouping of that. A check has at most one open incident at a
    time, so repeated failures extend the existing incident rather than
    creating a new one for every failed probe.
    """

    class State(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"

    class Severity(models.TextChoices):
        CRITICAL = "critical", "Critical"
        WARNING = "warning", "Warning"

    check_ref = models.ForeignKey(Check, related_name="incidents", on_delete=models.CASCADE)
    state = models.CharField(max_length=16, choices=State.choices, default=State.OPEN)
    severity = models.CharField(max_length=16, choices=Severity.choices, default=Severity.CRITICAL)
    message = models.CharField(max_length=500)
    started_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    details_json = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        indexes = [
            models.Index(fields=["state", "-started_at"], name="incident_state_started_idx"),
            models.Index(fields=["check_ref", "-started_at"], name="incident_chk_started_idx"),
        ]
        constraints = [
            # At most one open incident per check.
            models.UniqueConstraint(
                fields=["check_ref"],
                condition=models.Q(state="open"),
                name="unique_open_incident_per_check",
            )
        ]

    @property
    def duration(self):
        """How long the incident lasted, or has lasted so far."""
        end = self.resolved_at or timezone.now()
        return end - self.started_at

    def __str__(self):
        return f"{self.state}: {self.check_ref_id} {self.message}"


class StatusPage(models.Model):
    """A publicly shareable summary of a chosen set of checks."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=120, unique=True)
    title = models.CharField(max_length=200, default="System Status")
    description = models.CharField(max_length=500, blank=True, default="")
    published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.name


class StatusPageItem(models.Model):
    """One check as it appears on a status page."""

    page = models.ForeignKey(StatusPage, related_name="items", on_delete=models.CASCADE)
    check_ref = models.ForeignKey(Check, related_name="status_page_items", on_delete=models.CASCADE)
    display_name = models.CharField(max_length=120, blank=True, default="")
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["page", "check_ref"], name="unique_check_per_status_page"
            )
        ]

    @property
    def label(self):
        return self.display_name or self.check_ref.name

    def __str__(self):
        return f"{self.page_id}: {self.label}"


class AppSettings(models.Model):
    """Singleton holding instance-wide preferences.

    Enforced as a singleton by pinning the primary key in :meth:`save`, so
    ``AppSettings.load()`` always returns the same row.
    """

    site_name = models.CharField(max_length=120, default="PulseTrace")
    site_url = models.URLField(blank=True, default="")
    timezone_name = models.CharField(max_length=64, default="UTC")
    default_check_interval_seconds = models.PositiveIntegerField(
        default=60,
        validators=[MinValueValidator(30), MaxValueValidator(86400)],
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "application settings"
        verbose_name_plural = "application settings"

    def clean(self):
        if self.timezone_name not in available_timezones():
            raise ValidationError({"timezone_name": "Unknown time zone."})

    def save(self, *args, **kwargs):
        self.pk = 1
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Application settings cannot be deleted.")

    @classmethod
    def load(cls):
        settings_row, _ = cls.objects.get_or_create(pk=1)
        return settings_row

    def __str__(self):
        return self.site_name
