from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.checks.probes.dns_probe import run_dns_probe
from apps.checks.probes.http_probe import run_http_probe
from apps.checks.probes.tcp_probe import run_tcp_probe
from apps.checks.probes.tls_probe import run_tls_probe


class ProbeTests(SimpleTestCase):
    @patch("apps.checks.probes.dns_probe.dns.resolver.Resolver")
    def test_dns_probe_returns_records_and_timings(self, resolver_cls):
        resolver = resolver_cls.return_value

        def side_effect(target, record_type, lifetime=None, raise_on_no_answer=False):
            del target, lifetime, raise_on_no_answer
            if record_type == "A":
                rec = MagicMock()
                rec.to_text.return_value = "93.184.216.34"
                return [rec]
            if record_type == "AAAA":
                rec = MagicMock()
                rec.to_text.return_value = "2606:2800:220:1:248:1893:25c8:1946"
                return [rec]
            return []

        resolver.resolve.side_effect = side_effect
        payload = run_dns_probe("example.com", timeout=2.0)
        self.assertEqual(payload["details"]["records"]["a"], ["93.184.216.34"])
        self.assertIn("total_ms", payload["timings"])

    @patch("apps.checks.probes.tcp_probe.socket.create_connection")
    def test_tcp_probe_success(self, create_connection):
        cm = MagicMock()
        cm.__enter__.return_value = cm
        cm.__exit__.return_value = False
        create_connection.return_value = cm

        payload = run_tcp_probe("example.com", port=443, timeout=2.0)
        self.assertEqual(payload["details"]["remote"], "example.com:443")
        self.assertIn("connect_ms", payload["timings"])

    @patch("apps.checks.probes.tls_probe.ssl.create_default_context")
    @patch("apps.checks.probes.tls_probe.socket.create_connection")
    def test_tls_probe_collects_certificate_fields(self, create_connection, create_default_context):
        raw_socket = MagicMock()
        raw_cm = MagicMock()
        raw_cm.__enter__.return_value = raw_socket
        raw_cm.__exit__.return_value = False
        create_connection.return_value = raw_cm

        tls_socket = MagicMock()
        tls_socket.getpeercert.return_value = {
            "subject": ((("commonName", "example.com"),),),
            "issuer": ((("commonName", "Example CA"),),),
            "notAfter": "Jun 15 12:00:00 2030 GMT",
        }

        tls_cm = MagicMock()
        tls_cm.__enter__.return_value = tls_socket
        tls_cm.__exit__.return_value = False
        context = MagicMock()
        context.wrap_socket.return_value = tls_cm
        create_default_context.return_value = context

        payload = run_tls_probe("example.com", port=443, timeout=2.0)
        cert = payload["details"]["certificate"]
        self.assertEqual(payload["details"]["remote"], "example.com:443")
        self.assertIn("not_after", cert)
        self.assertIn("tls_handshake_ms", payload["timings"])

    @patch("apps.checks.probes.http_probe.httpx.Client")
    def test_http_probe_total_and_ttfb(self, client_cls):
        response = MagicMock()
        response.status_code = 200
        response.url = "https://example.com/health"
        response.iter_bytes.return_value = [b"abc", b"123"]

        stream_cm = MagicMock()
        stream_cm.__enter__.return_value = response
        stream_cm.__exit__.return_value = False

        client = client_cls.return_value.__enter__.return_value
        client.stream.return_value = stream_cm

        payload = run_http_probe("https://example.com/health", timeout=2.0)
        self.assertEqual(payload["details"]["status_code"], 200)
        self.assertEqual(payload["details"]["bytes_read"], 6)
        self.assertIn("ttfb_ms", payload["timings"])
        self.assertIn("total_ms", payload["timings"])
