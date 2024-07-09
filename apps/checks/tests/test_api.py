from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.checks.models import Check, CheckResult
from apps.checks.throttles import RunNowUserThrottle


class CheckAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="tester", password="tester-pass")
        self.token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def _payload(self, name="API HTTP"):
        return {
            "name": name,
            "type": "http",
            "target": "https://example.com/health",
            "frequency_seconds": 60,
            "timeout_seconds": 5,
            "retries": 1,
            "enabled": True,
            "alert_rules": [
                {
                    "mode": "consecutive_failures",
                    "consecutive_failures_count": 3,
                    "enabled": True,
                }
            ],
        }

    def test_auth_required(self):
        anon = APIClient()
        response = anon.get("/api/checks")
        self.assertEqual(response.status_code, 401)

    def test_create_and_list_checks(self):
        create = self.client.post("/api/checks", self._payload(), format="json")
        self.assertEqual(create.status_code, 201)
        self.assertEqual(create.data["name"], "API HTTP")
        self.assertEqual(len(create.data["alert_rules"]), 1)

        listing = self.client.get("/api/checks")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.data), 1)

    @patch("apps.checks.views.run_check.delay")
    def test_run_now_endpoint_queues_task(self, delay):
        delay.return_value = SimpleNamespace(id="task-123")
        check = Check.objects.create(
            name="Queue me",
            type=Check.Type.HTTP,
            target="https://example.com",
            frequency_seconds=60,
            timeout_seconds=5,
            retries=1,
        )
        response = self.client.post(f"/api/checks/{check.id}/run-now", format="json")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], "queued")
        delay.assert_called_once_with(check.id)

    def test_results_since_filter(self):
        check = Check.objects.create(
            name="Result filter",
            type=Check.Type.TCP,
            target="example.com",
            port=443,
            frequency_seconds=60,
            timeout_seconds=5,
            retries=1,
        )
        now = timezone.now()
        CheckResult.objects.create(
            check_ref=check,
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=2) + timedelta(seconds=1),
            status=CheckResult.Status.OK,
            timings_json={"total_ms": 12.5},
            details_json={},
        )
        CheckResult.objects.create(
            check_ref=check,
            started_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=10) + timedelta(seconds=1),
            status=CheckResult.Status.FAIL,
            timings_json={"total_ms": 1000},
            details_json={},
            error_message="failed",
        )

        since = (now - timedelta(minutes=30)).isoformat()
        response = self.client.get(f"/api/checks/{check.id}/results", {"since": since})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["status"], "fail")

    def test_summary_endpoint(self):
        ok_check = Check.objects.create(
            name="Summary OK",
            type=Check.Type.DNS,
            target="example.com",
            frequency_seconds=60,
            timeout_seconds=5,
            retries=1,
        )
        fail_check = Check.objects.create(
            name="Summary FAIL",
            type=Check.Type.TLS,
            target="example.com",
            frequency_seconds=60,
            timeout_seconds=5,
            retries=1,
        )
        now = timezone.now()
        CheckResult.objects.create(
            check_ref=ok_check,
            started_at=now - timedelta(minutes=1),
            finished_at=now - timedelta(minutes=1) + timedelta(seconds=1),
            status=CheckResult.Status.OK,
            timings_json={"total_ms": 50},
            details_json={},
        )
        CheckResult.objects.create(
            check_ref=fail_check,
            started_at=now - timedelta(minutes=1),
            finished_at=now - timedelta(minutes=1) + timedelta(seconds=1),
            status=CheckResult.Status.FAIL,
            timings_json={"total_ms": 700},
            details_json={},
            error_message="oops",
        )

        response = self.client.get("/api/summary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["aggregates"]["total_checks"], 2)
        self.assertEqual(response.data["aggregates"]["ok_latest_count"], 1)
        self.assertEqual(response.data["aggregates"]["fail_latest_count"], 1)

    @patch("apps.checks.views.run_check.delay")
    def test_run_now_user_throttle(self, delay):
        delay.return_value = SimpleNamespace(id="task-123")
        check = Check.objects.create(
            name="Throttle",
            type=Check.Type.HTTP,
            target="https://example.com",
            frequency_seconds=60,
            timeout_seconds=5,
            retries=1,
        )

        original_rate = getattr(RunNowUserThrottle, "rate", None)
        original_num = getattr(RunNowUserThrottle, "num_requests", None)
        original_duration = getattr(RunNowUserThrottle, "duration", None)
        RunNowUserThrottle.rate = "2/minute"

        cache.clear()
        try:
            first = self.client.post(f"/api/checks/{check.id}/run-now", format="json")
            second = self.client.post(f"/api/checks/{check.id}/run-now", format="json")
            third = self.client.post(f"/api/checks/{check.id}/run-now", format="json")
            self.assertEqual(first.status_code, 202)
            self.assertEqual(second.status_code, 202)
            self.assertEqual(third.status_code, 429)
        finally:
            RunNowUserThrottle.rate = original_rate
            if original_num is None:
                if hasattr(RunNowUserThrottle, "num_requests"):
                    delattr(RunNowUserThrottle, "num_requests")
            else:
                RunNowUserThrottle.num_requests = original_num
            if original_duration is None:
                if hasattr(RunNowUserThrottle, "duration"):
                    delattr(RunNowUserThrottle, "duration")
            else:
                RunNowUserThrottle.duration = original_duration
            cache.clear()
