from django.contrib import admin
from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token

from apps.checks.views import CheckDetailPageView, CheckListPageView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/token", obtain_auth_token, name="api-token"),
    path("api/", include("apps.checks.urls")),
    path("", CheckListPageView.as_view(), name="ui-check-list"),
    path("checks/<int:pk>", CheckDetailPageView.as_view(), name="ui-check-detail"),
    path("accounts/", include("django.contrib.auth.urls")),
]
