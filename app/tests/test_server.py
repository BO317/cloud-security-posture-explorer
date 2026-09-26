import json
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import urlopen
from wsgiref.simple_server import make_server

from app.aws_provider import Scan
from app.config import ConfigurationError
from app.server import create_application, dashboard, ThreadedWSGIServer
from app.sample_data import sample_resources


def fixture_scan():
    return Scan("us-east-1", "test-scan", "2026-09-25T12:00:00Z", "2026-09-25T12:00:01Z", sample_resources())


def request(app, path="/", method="GET"):
    response = {}

    def start(status, headers):
        response.update(status=status, headers=dict(headers))

    response["body"] = b"".join(app({"PATH_INFO": path, "REQUEST_METHOD": method}, start)).decode()
    return response


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.collector = Mock(return_value=fixture_scan())
        self.app = create_application(self.collector)

    def test_dashboard_has_evidence_and_honest_scope(self):
        response = request(self.app)
        self.assertEqual(response["status"], "200 OK")
        for label in ("PASS", "REVIEW", "UNKNOWN", "INCOMPLETE", "2026-09-25T12:00:00Z", "us-east-1", "test-scan", "effective exposure remains UNKNOWN"):
            self.assertIn(label, response["body"])
        self.assertNotIn("SYNTHETIC DATA", response["body"])
        self.assertEqual(response["body"].count("<tr>"), 9)
        self.assertEqual(response["headers"]["Cache-Control"], "no-store")

    def test_health_independent_of_broken_scan(self):
        for failure in (RuntimeError("private error"), ConfigurationError("Missing scope.")):
            self.collector.reset_mock()
            self.collector.side_effect = failure
            health = request(self.app, "/healthz")
            self.assertEqual(health["status"], "200 OK")
            self.assertEqual(json.loads(health["body"]), {"liveness": "ok"})
            self.collector.assert_not_called()
            page = request(self.app)
            self.assertEqual(page["status"], "503 Service Unavailable")
            self.assertIn("UNKNOWN", page["body"])
            self.assertNotIn("private error", page["body"])

    def test_health_does_not_initialize_aws_or_configuration(self):
        with patch("app.aws_provider.boto3.Session", side_effect=AssertionError("AWS touched")), patch.dict("os.environ", {}, clear=True):
            response = request(create_application(), "/healthz")
            self.assertEqual(json.loads(response["body"]), {"liveness": "ok"})
            self.assertEqual(request(create_application())["status"], "503 Service Unavailable")

    def test_read_only_and_no_inventory_route(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = request(self.app, method=method)
            self.assertEqual(response["status"], "405 Method Not Allowed")
            self.assertEqual(response["headers"]["Allow"], "GET, HEAD")
        self.assertEqual(request(self.app, "/api/inventory")["status"], "404 Not Found")
        self.collector.assert_not_called()
        self.assertEqual(request(self.app, method="HEAD")["body"], "")
        self.assertEqual(request(self.app, "/healthz", "HEAD")["body"], "")

    def test_html_is_escaped(self):
        resources = sample_resources()
        resources[0]["name"] = "<script>alert(1)</script>"
        body = dashboard(resources, region="<script>")
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_empty_scan_is_unknown_not_complete(self):
        body = dashboard([])
        self.assertIn("INCOMPLETE", body)
        self.assertIn("UNKNOWN: no resources", body)

    def test_failed_request_releases_scan_lock(self):
        self.collector.side_effect = [RuntimeError(), fixture_scan()]
        self.assertEqual(request(self.app)["status"], "503 Service Unavailable")
        self.assertEqual(request(self.app)["status"], "200 OK")

    def test_http_health_responds_while_collection_is_blocked(self):
        entered, release = Event(), Event()
        failures = []

        def collect():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test release timeout")
            return fixture_scan()

        server = make_server("127.0.0.1", 0, create_application(collect), server_class=ThreadedWSGIServer)
        serving = Thread(target=server.serve_forever, daemon=True)
        serving.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def fetch_page():
            try:
                with urlopen(base + "/", timeout=6) as response:
                    self.assertIn(b"INCOMPLETE", response.read())
            except Exception as exc:
                failures.append(exc)

        fetching = Thread(target=fetch_page, daemon=True)
        fetching.start()
        try:
            self.assertTrue(entered.wait(2))
            with urlopen(base + "/healthz", timeout=2) as response:
                self.assertEqual(json.load(response), {"liveness": "ok"})
            with self.assertRaises(HTTPError) as context:
                urlopen(base + "/", timeout=2)
            self.assertEqual(context.exception.code, 503)
            context.exception.close()
        finally:
            release.set()
            fetching.join(7)
            server.shutdown()
            server.server_close()
            serving.join(2)
        self.assertFalse(fetching.is_alive())
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
