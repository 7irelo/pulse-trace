"""Server-rendered operator UI.

The DRF viewsets in :mod:`apps.checks.views` stay the machine-facing API; these
are the pages a human uses. They are deliberately plain Django class-based
views reading the same models, so there is no second source of truth about what
a check's state is.
"""

import datetime as dt

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import DetailView, ListView, TemplateView, View

from apps.checks.models import (
    AppSettings,
    Check,
    CheckResult,
    Incident,
    NotificationChannel,
    StatusPage,
)
from apps.checks.views import checks_with_latest_queryset

#: Window used for the "last 24 hours" figures across the UI.
UPTIME_WINDOW = dt.timedelta(hours=24)


def uptime_percentage(queryset, window=UPTIME_WINDOW):
    """Share of results in ``window`` that succeeded, as a percentage.

    Returns ``None`` rather than 100 when there is nothing to measure, so the
    templates can distinguish "perfect" from "no data yet".
    """
    since = timezone.now() - window
    counts = queryset.filter(started_at__gte=since).aggregate(
        total=Count("id"),
        ok=Count("id", filter=Q(status=CheckResult.Status.OK)),
    )
    if not counts["total"]:
        return None
    return round(counts["ok"] * 100.0 / counts["total"], 2)


def latest_status_counts():
    """Up/down/total across all checks, based on each check's newest result."""
    checks = list(checks_with_latest_queryset())
    up = sum(1 for c in checks if c.latest_status == CheckResult.Status.OK)
    down = sum(1 for c in checks if c.latest_status == CheckResult.Status.FAIL)
    return {"total": len(checks), "up": up, "down": down, "checks": checks}


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "checks/dashboard.html"
    extra_context = {"nav": "dashboard"}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        counts = latest_status_counts()

        context["total_checks"] = counts["total"]
        context["up_count"] = counts["up"]
        context["down_count"] = counts["down"]
        context["uptime"] = uptime_percentage(CheckResult.objects.all())
        context["recent_incidents"] = Incident.objects.select_related("check_ref").all()[:5]
        context["open_incident_count"] = Incident.objects.filter(state=Incident.State.OPEN).count()

        # "Upcoming checks": enabled checks ordered by how soon they are due,
        # derived from their last run plus their configured frequency.
        upcoming = []
        now = timezone.now()
        for check in counts["checks"]:
            if not check.enabled:
                continue
            last = check.latest_started_at
            due = last + dt.timedelta(seconds=check.frequency_seconds) if last else now
            upcoming.append({"check": check, "due_at": due})
        upcoming.sort(key=lambda item: item["due_at"])
        context["upcoming_checks"] = upcoming[:6]

        # A coarse hourly series for the uptime sparkline.
        context["uptime_series"] = self._hourly_uptime()
        return context

    def _hourly_uptime(self):
        now = timezone.now()
        series = []
        for hours_ago in range(23, -1, -1):
            end = now - dt.timedelta(hours=hours_ago)
            start = end - dt.timedelta(hours=1)
            bucket = CheckResult.objects.filter(
                started_at__gte=start, started_at__lt=end
            ).aggregate(
                total=Count("id"),
                ok=Count("id", filter=Q(status=CheckResult.Status.OK)),
            )
            value = round(bucket["ok"] * 100.0 / bucket["total"], 2) if bucket["total"] else None
            series.append({"hour": end, "uptime": value})
        return series


class IncidentListView(LoginRequiredMixin, ListView):
    template_name = "checks/incidents.html"
    extra_context = {"nav": "incidents"}
    context_object_name = "incidents"
    paginate_by = 25

    def get_queryset(self):
        queryset = Incident.objects.select_related("check_ref")

        state = self.request.GET.get("state")
        if state in {Incident.State.OPEN, Incident.State.RESOLVED}:
            queryset = queryset.filter(state=state)

        search = (self.request.GET.get("q") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(message__icontains=search) | Q(check_ref__name__icontains=search)
            )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["state"] = self.request.GET.get("state", "")
        context["q"] = self.request.GET.get("q", "")
        context["counts"] = {
            "all": Incident.objects.count(),
            "open": Incident.objects.filter(state=Incident.State.OPEN).count(),
            "resolved": Incident.objects.filter(state=Incident.State.RESOLVED).count(),
        }
        return context


class ChannelListView(LoginRequiredMixin, ListView):
    template_name = "checks/alerting.html"
    extra_context = {"nav": "alerts"}
    context_object_name = "channels"

    def get_queryset(self):
        return NotificationChannel.objects.prefetch_related("rules").order_by("id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["kinds"] = NotificationChannel.Kind.choices
        return context


class LogsView(LoginRequiredMixin, TemplateView):
    """A live-ish view of recent probe results, newest first.

    This is the closest honest analogue of an application log: every line is a
    real recorded probe, so an operator can watch a check succeed or fail in
    near real time by refreshing.
    """

    template_name = "checks/logs.html"
    extra_context = {"nav": "logs"}
    page_size = 200

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queryset = CheckResult.objects.select_related("check_ref").order_by("-started_at", "-id")

        check_id = self.request.GET.get("check")
        if check_id and check_id.isdigit():
            queryset = queryset.filter(check_ref_id=int(check_id))

        level = self.request.GET.get("level")
        if level in {CheckResult.Status.OK, CheckResult.Status.FAIL}:
            queryset = queryset.filter(status=level)

        context["results"] = queryset[: self.page_size]
        context["checks"] = Check.objects.order_by("name")
        context["selected_check"] = check_id or ""
        context["level"] = level or ""
        return context


class StatusPageListView(LoginRequiredMixin, ListView):
    template_name = "checks/status_pages.html"
    extra_context = {"nav": "status"}
    context_object_name = "pages"

    def get_queryset(self):
        return StatusPage.objects.prefetch_related("items__check_ref").order_by("id")


class PublicStatusPageView(DetailView):
    """The publicly reachable status page. Deliberately not login-gated."""

    template_name = "checks/status_page_public.html"
    context_object_name = "page"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return StatusPage.objects.filter(published=True).prefetch_related("items__check_ref")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        latest_by_check = {check.id: check for check in checks_with_latest_queryset()}

        rows = []
        all_ok = True
        for item in self.object.items.all():
            annotated = latest_by_check.get(item.check_ref_id)
            status = annotated.latest_status if annotated else None
            if status != CheckResult.Status.OK:
                all_ok = False
            rows.append(
                {
                    "label": item.label,
                    "status": status,
                    "uptime": uptime_percentage(
                        CheckResult.objects.filter(check_ref_id=item.check_ref_id)
                    ),
                }
            )

        context["rows"] = rows
        context["all_operational"] = all_ok and bool(rows)
        context["updated_at"] = timezone.now()
        return context


class SettingsView(LoginRequiredMixin, View):
    template_name = "checks/settings.html"

    def get(self, request):
        from django.shortcuts import render

        return render(
            request,
            self.template_name,
            {
                "settings_row": AppSettings.load(),
                "timezones": _common_timezones(),
                "nav": "settings",
            },
        )

    def post(self, request):
        from django.core.exceptions import ValidationError
        from django.shortcuts import render

        settings_row = AppSettings.load()
        settings_row.site_name = request.POST.get("site_name", settings_row.site_name)
        settings_row.site_url = request.POST.get("site_url", settings_row.site_url)
        settings_row.timezone_name = request.POST.get("timezone_name", settings_row.timezone_name)

        interval = request.POST.get("default_check_interval_seconds")
        if interval and interval.isdigit():
            settings_row.default_check_interval_seconds = int(interval)

        try:
            settings_row.save()
        except ValidationError as exc:
            return render(
                request,
                self.template_name,
                {
                    "settings_row": settings_row,
                    "timezones": _common_timezones(),
                    "nav": "settings",
                    "errors": exc.message_dict,
                },
                status=400,
            )

        messages.success(request, "Settings saved.")
        return redirect(reverse("ui-settings"))


def _common_timezones():
    """A short, ordered list of time zones for the settings dropdown."""
    from zoneinfo import available_timezones

    preferred = [
        "UTC",
        "Africa/Johannesburg",
        "Europe/London",
        "Europe/Berlin",
        "America/New_York",
        "America/Los_Angeles",
        "Asia/Singapore",
        "Australia/Sydney",
    ]
    known = available_timezones()
    return [name for name in preferred if name in known]


def incident_resolve(request, pk):
    """Manually resolve an incident from the UI."""
    from apps.checks.services.incidents import resolve_incident

    if request.method != "POST":
        return redirect(reverse("ui-incidents"))

    incident = get_object_or_404(Incident, pk=pk)
    resolve_incident(incident.check_ref, details={"resolved_by": "manual"})
    messages.success(request, f"Incident for {incident.check_ref.name} resolved.")
    return redirect(reverse("ui-incidents"))
