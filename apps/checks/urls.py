from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.checks.views import CheckViewSet, SummaryAPIView

router = DefaultRouter(trailing_slash=False)
router.register("checks", CheckViewSet, basename="check")

urlpatterns = [
    path("", include(router.urls)),
    path("summary", SummaryAPIView.as_view(), name="api-summary"),
]
