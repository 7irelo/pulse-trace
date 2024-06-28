from django.contrib import admin

from apps.checks.models import AlertEvent, AlertRule, Check, CheckResult


@admin.register(Check)
class CheckAdmin(admin.ModelAdmin):
    list_display = (
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
    )
    list_filter = ("type", "enabled")
    search_fields = ("name", "target")


@admin.register(CheckResult)
class CheckResultAdmin(admin.ModelAdmin):
    list_display = ("id", "check_ref", "status", "started_at", "finished_at")
    list_filter = ("status", "check_ref")
    search_fields = ("check_ref__name", "error_message")
    readonly_fields = ("timings_json", "details_json")


@admin.register(AlertRule)
class AlertRuleAdmin(admin.ModelAdmin):
    list_display = ("id", "check_ref", "mode", "enabled", "webhook_url", "created_at")
    list_filter = ("mode", "enabled")
    search_fields = ("check_ref__name", "webhook_url")


@admin.register(AlertEvent)
class AlertEventAdmin(admin.ModelAdmin):
    list_display = ("id", "check_ref", "rule", "state", "created_at")
    list_filter = ("state", "check_ref")
    search_fields = ("check_ref__name", "message")
    readonly_fields = ("details_json",)
