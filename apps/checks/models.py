from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.checks.validators import validate_check_target, validate_webhook_url


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
