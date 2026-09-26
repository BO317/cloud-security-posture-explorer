import json
import unittest
from unittest.mock import patch

from app.server import application, dashboard
from app.sample_data import sample_resources


def request(path="/", method="GET"):
    response = {}
    def start(status, headers):
        response.update(status=status, headers=dict(headers))
    response["body"] = b"".join(application({"PATH_INFO": path, "REQUEST_METHOD": method}, start)).decode()
    return response


class ServerTest(unittest.TestCase):
    def test_dashboard_has_evidence_and_honest_scope(self):
        response = request()
        self.assertEqual(response["status"], "200 OK")
        for label in ("PASS", "REVIEW", "UNKNOWN", "INCOMPLETE", "2026-09-25T12:00:00Z", "SYNTHETIC DATA", "effective exposure remains UNKNOWN"):
            self.assertIn(label, response["body"])
        self.assertEqual(response["body"].count("<tr>"), 9)

    def test_health_independent_of_broken_scan(self):
        with patch("app.server.sample_resources", side_effect=RuntimeError("private error")):
            health = request("/healthz")
            self.assertEqual(health["status"], "200 OK")
            self.assertEqual(json.loads(health["body"])["posture_scan"], "not_evaluated")
            page = request()
            self.assertEqual(page["status"], "503 Service Unavailable")
            self.assertIn("UNKNOWN", page["body"])
            self.assertNotIn("private error", page["body"])

    def test_read_only_and_no_inventory_route(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            self.assertEqual(request(method=method)["status"], "405 Method Not Allowed")
        self.assertEqual(request("/api/inventory")["status"], "404 Not Found")
        self.assertEqual(request(method="HEAD")["body"], "")

    def test_html_is_escaped(self):
        resources = sample_resources()
        resources[0]["name"] = "<script>alert(1)</script>"
        body = dashboard(resources)
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_empty_fixture_is_not_complete(self):
        self.assertIn("INCOMPLETE", dashboard([]))


if __name__ == "__main__":
    unittest.main()
