"""Connectivity probe tests do not contact Modal or any external service."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from zyvello_hosted.health import create_app


class HostedHealthTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app())

    def test_liveness_is_read_only(self):
        result = self.client.get("/healthz")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"service": "zyvello-staging", "status": "alive"})

    def test_readiness_explicitly_fails_until_hosted_foundation_exists(self):
        result = self.client.get("/readyz")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["status"], "not_ready")

    def test_local_dashboard_and_auth_are_not_exposed(self):
        for path in ("/", "/api/auth/session", "/api/auth/signup", "/api/auth/login", "/api/send"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_staging_frontend_is_opt_in_and_not_indexed(self):
        with TemporaryDirectory() as folder:
            Path(folder, "index.html").write_text("<html>Staging only</html>", encoding="utf-8")
            Path(folder, "favicon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
            client = TestClient(create_app(static_dir=folder))
            response = client.get("/login")
            self.assertEqual(response.status_code, 200)
            self.assertIn("noindex", response.headers["X-Robots-Tag"])
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(client.get("/static/favicon.svg").status_code, 200)
            self.assertEqual(client.get("/api/auth/login").status_code, 404)


if __name__ == "__main__":
    unittest.main()
