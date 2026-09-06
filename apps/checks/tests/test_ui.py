from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.checks.models import (
    AppSettings,
    Check,
    CheckResult,
    Incident,
    NotificationChannel,
    StatusPage,
    StatusPageItem,
)
from apps.checks.services.incidents import open_or_extend_incident, resolve_incident


def make_check(name="api", check_type=Check.Type.HTTP, target="https://example.com", port=None):
    return Check.objects.create(name=name, type=check_type, target=target, port=port)


def make_result(check, status=CheckResult.Status.OK, minutes_ago=1, total_ms=120):
    started = timezone.now() - timedelta(minutes=minutes_ago)
    return CheckResult.objects.create(
        check_ref=check,
        started_at=started,
        finished_at=started + timedelta(milliseconds=total_ms),
        status=status,
        timings_json={"total_ms": total_ms},
    )


class UIPagesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("operator", password="pw-for-tests")
        self.client.force_login(self.user)
        self.check = make_check()

    def test_dashboard_reports_up_and_down_counts(self):
        make_result(self.check, CheckResult.Status.OK)
        down = make_check(name="worker", check_type=Check.Type.TCP, target="10.0.0.5", port=8080)
        make_result(down, CheckResult.Status.FAIL)

        response = self.client.get(reverse("ui-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_checks"], 2)
        self.assertEqual(response.context["up_count"], 1)
        self.assertEqual(response.context["down_count"], 1)
        self.assertEqual(response.context["uptime"], 50.0)

    def test_dashboard_uptime_is_none_without_results(self):
        response = self.client.get(reverse("ui-dashboard"))
        self.assertIsNone(response.context["uptime"])

    def test_incident_list_filters_by_state(self):
        open_incident = Incident.objects.create(
            check_ref=self.check,
            state=Incident.State.OPEN,
            message="Service is down",
            started_at=timezone.now(),
        )
        other = make_check(name="db", check_type=Check.Type.TCP, target="10.0.0.9", port=5432)
        Incident.objects.create(
            check_ref=other,
            state=Incident.State.RESOLVED,
            message="Recovered",
            started_at=timezone.now() - timedelta(hours=2),
            resolved_at=timezone.now() - timedelta(hours=1),
        )

        response = self.client.get(reverse("ui-incidents"), {"state": "open"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["incidents"]), [open_incident])
        self.assertEqual(response.context["counts"]["all"], 2)
        self.assertEqual(response.context["counts"]["open"], 1)

    def test_incident_list_searches_by_check_name(self):
        Incident.objects.create(
            check_ref=self.check,
            message="Service is down",
            started_at=timezone.now(),
        )
        response = self.client.get(reverse("ui-incidents"), {"q": "nomatch"})
        self.assertEqual(list(response.context["incidents"]), [])

        response = self.client.get(reverse("ui-incidents"), {"q": "api"})
        self.assertEqual(len(response.context["incidents"]), 1)

    def test_logs_filter_by_level(self):
        make_result(self.check, CheckResult.Status.OK)
        make_result(self.check, CheckResult.Status.FAIL, minutes_ago=2)

        response = self.client.get(reverse("ui-logs"), {"level": "fail"})

        self.assertEqual(response.status_code, 200)
        statuses = {r.status for r in response.context["results"]}
        self.assertEqual(statuses, {CheckResult.Status.FAIL})

    def test_alerting_page_lists_channels(self):
        NotificationChannel.objects.create(
            name="Ops email",
            kind=NotificationChannel.Kind.EMAIL,
            config_json={"recipients": ["ops@example.com"]},
        )
        response = self.client.get(reverse("ui-alerts"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["channels"]), 1)

    def test_settings_page_saves_changes(self):
        response = self.client.post(
            reverse("ui-settings"),
            {
                "site_name": "Euphoria Status",
                "site_url": "https://status.example.com",
                "timezone_name": "Africa/Johannesburg",
                "default_check_interval_seconds": "120",
            },
        )
        self.assertEqual(response.status_code, 302)

        saved = AppSettings.load()
        self.assertEqual(saved.site_name, "Euphoria Status")
        self.assertEqual(saved.timezone_name, "Africa/Johannesburg")
        self.assertEqual(saved.default_check_interval_seconds, 120)

    def test_settings_rejects_unknown_timezone(self):
        response = self.client.post(
            reverse("ui-settings"),
            {
                "site_name": "PulseTrace",
                "site_url": "",
                "timezone_name": "Mars/Olympus_Mons",
                "default_check_interval_seconds": "60",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("timezone_name", response.context["errors"])

    def test_ui_requires_login(self):
        self.client.logout()
        for name in ["ui-dashboard", "ui-incidents", "ui-alerts", "ui-logs", "ui-settings"]:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)


class StatusPageTests(TestCase):
    def setUp(self):
        self.check = make_check()
        self.page = StatusPage.objects.create(name="Public", slug="public", published=True)
        StatusPageItem.objects.create(page=self.page, check_ref=self.check)

    def test_published_page_is_reachable_without_login(self):
        make_result(self.check, CheckResult.Status.OK)

        response = self.client.get(reverse("ui-status-public", args=["public"]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["all_operational"])

    def test_unpublished_page_is_404(self):
        self.page.published = False
        self.page.save()
        response = self.client.get(reverse("ui-status-public", args=["public"]))
        self.assertEqual(response.status_code, 404)

    def test_failing_check_clears_all_operational(self):
        make_result(self.check, CheckResult.Status.FAIL)
        response = self.client.get(reverse("ui-status-public", args=["public"]))
        self.assertFalse(response.context["all_operational"])


class IncidentServiceTests(TestCase):
    def setUp(self):
        self.check = make_check()

    def test_open_then_extend_keeps_one_incident(self):
        first = open_or_extend_incident(self.check, "Down")
        second = open_or_extend_incident(self.check, "Still down")

        self.assertEqual(first.id, second.id)
        self.assertEqual(Incident.objects.count(), 1)
        self.assertEqual(second.message, "Still down")
        # The start time is the original one, not the latest failure.
        self.assertEqual(first.started_at, second.started_at)

    def test_resolve_sets_resolved_at(self):
        open_or_extend_incident(self.check, "Down")
        resolved = resolve_incident(self.check)

        self.assertEqual(resolved.state, Incident.State.RESOLVED)
        self.assertIsNotNone(resolved.resolved_at)

    def test_resolving_without_open_incident_is_a_noop(self):
        self.assertIsNone(resolve_incident(self.check))

    def test_new_incident_can_open_after_resolution(self):
        open_or_extend_incident(self.check, "Down")
        resolve_incident(self.check)
        reopened = open_or_extend_incident(self.check, "Down again")

        self.assertEqual(Incident.objects.count(), 2)
        self.assertEqual(reopened.state, Incident.State.OPEN)


@override_settings(ALERT_WEBHOOK_ALLOWLIST=["hooks.example.com"])
class NotificationChannelTests(TestCase):
    def test_email_channel_requires_recipients(self):
        channel = NotificationChannel(
            name="bad email", kind=NotificationChannel.Kind.EMAIL, config_json={}
        )
        with self.assertRaises(ValidationError):
            channel.save()

    def test_email_channel_validates_addresses(self):
        channel = NotificationChannel(
            name="bad address",
            kind=NotificationChannel.Kind.EMAIL,
            config_json={"recipients": ["not-an-email"]},
        )
        with self.assertRaises(ValidationError):
            channel.save()

    def test_slack_channel_respects_webhook_allowlist(self):
        blocked = NotificationChannel(
            name="blocked slack",
            kind=NotificationChannel.Kind.SLACK,
            config_json={"webhook_url": "https://evil.example.net/hook"},
        )
        with self.assertRaises(ValidationError):
            blocked.save()

        allowed = NotificationChannel(
            name="ok slack",
            kind=NotificationChannel.Kind.SLACK,
            config_json={"webhook_url": "https://hooks.example.com/abc"},
        )
        allowed.save()
        self.assertIsNotNone(allowed.pk)

    def test_sms_channel_validates_e164(self):
        bad = NotificationChannel(
            name="bad sms",
            kind=NotificationChannel.Kind.SMS,
            config_json={"numbers": ["0711234567"]},
        )
        with self.assertRaises(ValidationError):
            bad.save()

        good = NotificationChannel(
            name="ok sms",
            kind=NotificationChannel.Kind.SMS,
            config_json={"numbers": ["+27711234567"]},
        )
        good.save()
        self.assertIsNotNone(good.pk)
