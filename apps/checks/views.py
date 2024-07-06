import logging
from statistics import mean

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import OuterRef, Subquery
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.generic import DetailView, ListView
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.checks.models import Check, CheckResult
from apps.checks.serializers import CheckResultSerializer, CheckSerializer
from apps.checks.tasks import run_check
from apps.checks.throttles import RunNowUserThrottle

logger = logging.getLogger(__name__)


def checks_with_latest_queryset():
    latest = CheckResult.objects.filter(check_ref=OuterRef("pk")).order_by("-started_at", "-id")
    return (
        Check.objects.all()
        .prefetch_related("alert_rules")
        .annotate(
            latest_status=Subquery(latest.values("status")[:1]),
            latest_started_at=Subquery(latest.values("started_at")[:1]),
            latest_result_id=Subquery(latest.values("id")[:1]),
        )
    )


class CheckViewSet(viewsets.ModelViewSet):
    serializer_class = CheckSerializer
    queryset = checks_with_latest_queryset()

    @action(
        detail=True,
        methods=["post"],
        url_path="run-now",
        throttle_classes=[RunNowUserThrottle],
    )
    def run_now(self, request, pk=None):
        check = self.get_object()
        try:
            result = run_check.delay(check.id)
        except Exception:
            logger.exception("Failed to queue run-now request", extra={"check_id": check.id})
            return Response(
                {"detail": "Unable to queue check execution right now."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                "status": "queued",
                "check_id": check.id,
                "task_id": result.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["get"], url_path="results")
    def results(self, request, pk=None):
        check = self.get_object()
        queryset = check.results.all().order_by("-started_at", "-id")
        since = request.query_params.get("since")
        if since:
            parsed = parse_datetime(since)
            if parsed is None:
                return Response(
                    {"since": "Expected ISO-8601 datetime string."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            queryset = queryset.filter(started_at__gte=parsed)

        serializer = CheckResultSerializer(queryset[:200], many=True)
        return Response(serializer.data)


class SummaryAPIView(APIView):
    def get(self, request):
        checks = list(checks_with_latest_queryset().order_by("id"))
        latest_ids = [c.latest_result_id for c in checks if c.latest_result_id]
        latest_results = CheckResult.objects.filter(id__in=latest_ids)
        latest_by_id = {result.id: result for result in latest_results}

        response_checks = []
        latest_total_ms = []
        ok_count = 0
        fail_count = 0
        for check in checks:
            latest = latest_by_id.get(check.latest_result_id)
            status_value = latest.status if latest else None
            total_ms = None
            if latest:
                maybe_total = latest.timings_json.get("total_ms")
                if isinstance(maybe_total, (int, float)):
                    total_ms = float(maybe_total)
                    latest_total_ms.append(total_ms)
                if latest.status == CheckResult.Status.OK:
                    ok_count += 1
                elif latest.status == CheckResult.Status.FAIL:
                    fail_count += 1

            response_checks.append(
                {
                    "id": check.id,
                    "name": check.name,
                    "type": check.type,
                    "target": check.target,
                    "enabled": check.enabled,
                    "latest_status": status_value,
                    "latest_started_at": latest.started_at if latest else None,
                    "latest_total_ms": total_ms,
                }
            )

        aggregates = {
            "total_checks": len(checks),
            "enabled_checks": len([c for c in checks if c.enabled]),
            "checks_with_results": len(latest_ids),
            "ok_latest_count": ok_count,
            "fail_latest_count": fail_count,
            "avg_latest_total_ms": mean(latest_total_ms) if latest_total_ms else None,
        }
        return Response({"checks": response_checks, "aggregates": aggregates})


class CheckListPageView(LoginRequiredMixin, ListView):
    model = Check
    template_name = "checks/list.html"
    context_object_name = "checks"

    def get_queryset(self):
        return checks_with_latest_queryset().order_by("id")


class CheckDetailPageView(LoginRequiredMixin, DetailView):
    model = Check
    template_name = "checks/detail.html"
    context_object_name = "check"

    def get_object(self, queryset=None):
        return get_object_or_404(Check, pk=self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["results"] = self.object.results.all().order_by("-started_at", "-id")[:50]
        context["rules"] = self.object.alert_rules.all()
        return context
