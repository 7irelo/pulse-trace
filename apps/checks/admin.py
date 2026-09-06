from django.contrib import admin

from apps.checks.models import (
    AlertEvent,
    AlertRule,
    AppSettings,
    Check,
    CheckResult,
    Incident,
    NotificationChannel,
    StatusPage,
    StatusPageItem,
)


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
    filter_horizontal = ("channels",)


@admin.register(AlertEvent)
class AlertEventAdmin(admin.ModelAdmin):
    list_display = ("id", "check_ref", "rule", "state", "created_at")
    list_filter = ("state", "check_ref")
    search_fields = ("check_ref__name", "message")
    readonly_fields = ("details_json",)


@admin.register(NotificationChannel)
class NotificationChannelAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "kind", "enabled", "updated_at")
    list_filter = ("kind", "enabled")
    search_fields = ("name",)


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ("id", "check_ref", "state", "severity", "started_at", "resolved_at")
    list_filter = ("state", "severity", "check_ref")
    search_fields = ("check_ref__name", "message")
    readonly_fields = ("details_json",)


class StatusPageItemInline(admin.TabularInline):
    model = StatusPageItem
    extra = 1


@admin.register(StatusPage)
class StatusPageAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug", "published", "updated_at")
    list_filter = ("published",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [StatusPageItemInline]


@admin.register(AppSettings)
class AppSettingsAdmin(admin.ModelAdmin):
    list_display = ("site_name", "site_url", "timezone_name", "updated_at")

    def has_add_permission(self, request):
        # Singleton: the row is created on demand by AppSettings.load().
        return not AppSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
