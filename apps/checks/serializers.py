from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from apps.checks.models import AlertRule, Check, CheckResult


def _raise_drf_validation_error(exception):
    if hasattr(exception, "message_dict"):
        raise serializers.ValidationError(exception.message_dict)
    raise serializers.ValidationError(exception.messages)


class AlertRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertRule
        fields = [
            "id",
            "mode",
            "consecutive_failures_count",
            "latency_ms_threshold",
            "latency_run_count",
            "enabled",
            "webhook_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        instance = AlertRule(
            mode=attrs.get("mode", getattr(self.instance, "mode", None)),
            consecutive_failures_count=attrs.get(
                "consecutive_failures_count",
                getattr(self.instance, "consecutive_failures_count", None),
            ),
            latency_ms_threshold=attrs.get(
                "latency_ms_threshold",
                getattr(self.instance, "latency_ms_threshold", None),
            ),
            latency_run_count=attrs.get(
                "latency_run_count",
                getattr(self.instance, "latency_run_count", None),
            ),
            enabled=attrs.get("enabled", getattr(self.instance, "enabled", True)),
            webhook_url=attrs.get("webhook_url", getattr(self.instance, "webhook_url", "")),
        )
        try:
            instance.full_clean(exclude=["check_ref"])
        except DjangoValidationError as exc:
            _raise_drf_validation_error(exc)
        return attrs


class CheckSerializer(serializers.ModelSerializer):
    alert_rules = AlertRuleSerializer(many=True, required=False)
    latest_status = serializers.SerializerMethodField(read_only=True)
    latest_started_at = serializers.SerializerMethodField(read_only=True)
    latest_total_ms = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Check
        fields = [
            "id",
            "name",
            "type",
            "target",
            "port",
            "frequency_seconds",
            "timeout_seconds",
            "retries",
            "enabled",
            "created_at",
            "updated_at",
            "latest_status",
            "latest_started_at",
            "latest_total_ms",
            "alert_rules",
        ]
        read_only_fields = ("id", "created_at", "updated_at", "latest_status", "latest_started_at")

    CHECK_FIELDS = {
        "name",
        "type",
        "target",
        "port",
        "frequency_seconds",
        "timeout_seconds",
        "retries",
        "enabled",
    }

    def _latest_result(self, obj):
        if hasattr(obj, "_latest_result_cache"):
            return obj._latest_result_cache
        latest = obj.results.order_by("-started_at", "-id").first()
        obj._latest_result_cache = latest
        return latest

    def get_latest_status(self, obj):
        annotated = getattr(obj, "latest_status", None)
        if annotated:
            return annotated
        latest = self._latest_result(obj)
        return latest.status if latest else None

    def get_latest_started_at(self, obj):
        annotated = getattr(obj, "latest_started_at", None)
        if annotated:
            return annotated
        latest = self._latest_result(obj)
        return latest.started_at if latest else None

    def get_latest_total_ms(self, obj):
        latest = self._latest_result(obj)
        if not latest:
            return None
        total = latest.timings_json.get("total_ms")
        if isinstance(total, (int, float)):
            return float(total)
        return None

    def _build_check_instance(self, attrs):
        if self.instance is None:
            return Check(**attrs)

        merged = {
            "name": attrs.get("name", self.instance.name),
            "type": attrs.get("type", self.instance.type),
            "target": attrs.get("target", self.instance.target),
            "port": attrs.get("port", self.instance.port),
            "frequency_seconds": attrs.get("frequency_seconds", self.instance.frequency_seconds),
            "timeout_seconds": attrs.get("timeout_seconds", self.instance.timeout_seconds),
            "retries": attrs.get("retries", self.instance.retries),
            "enabled": attrs.get("enabled", self.instance.enabled),
        }
        return Check(**merged)

    def validate(self, attrs):
        check_attrs = {key: value for key, value in attrs.items() if key in self.CHECK_FIELDS}
        instance = self._build_check_instance(check_attrs)
        try:
            instance.full_clean(exclude=["id", "created_at", "updated_at"])
        except DjangoValidationError as exc:
            _raise_drf_validation_error(exc)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        rules_data = validated_data.pop("alert_rules", [])
        check = Check.objects.create(**validated_data)
        self._sync_rules(check, rules_data)
        return check

    @transaction.atomic
    def update(self, instance, validated_data):
        rules_data = validated_data.pop("alert_rules", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if rules_data is not None:
            self._sync_rules(instance, rules_data)
        return instance

    def _sync_rules(self, check, rules_data):
        check.alert_rules.all().delete()
        for rule_data in rules_data:
            rule = AlertRule(check_ref=check, **rule_data)
            rule.full_clean()
            rule.save()


class CheckResultSerializer(serializers.ModelSerializer):
    check = serializers.PrimaryKeyRelatedField(source="check_ref", read_only=True)

    class Meta:
        model = CheckResult
        fields = [
            "id",
            "check",
            "started_at",
            "finished_at",
            "status",
            "timings_json",
            "details_json",
            "error_message",
        ]
        read_only_fields = fields
