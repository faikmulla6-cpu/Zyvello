"""Connectivity probe tests do not contact Modal or any external service."""

import unittest

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


if __name__ == "__main__":
    unittest.main()
