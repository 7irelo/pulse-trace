from django.contrib import admin
from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token

from apps.checks.ui_views import (
    ChannelListView,
    DashboardView,
    IncidentListView,
    LogsView,
    PublicStatusPageView,
    SettingsView,
    StatusPageListView,
    incident_resolve,
)
from apps.checks.views import CheckDetailPageView, CheckListPageView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/token", obtain_auth_token, name="api-token"),
    path("api/", include("apps.checks.urls")),
    path("", DashboardView.as_view(), name="ui-dashboard"),
    path("checks", CheckListPageView.as_view(), name="ui-check-list"),
    path("checks/<int:pk>", CheckDetailPageView.as_view(), name="ui-check-detail"),
    path("incidents", IncidentListView.as_view(), name="ui-incidents"),
    path("incidents/<int:pk>/resolve", incident_resolve, name="ui-incident-resolve"),
    path("alerts", ChannelListView.as_view(), name="ui-alerts"),
    path("logs", LogsView.as_view(), name="ui-logs"),
    path("status-pages", StatusPageListView.as_view(), name="ui-status-pages"),
    path("settings", SettingsView.as_view(), name="ui-settings"),
    path("status/<slug:slug>", PublicStatusPageView.as_view(), name="ui-status-public"),
    path("accounts/", include("django.contrib.auth.urls")),
]
