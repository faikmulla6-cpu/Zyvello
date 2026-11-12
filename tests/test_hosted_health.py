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

    def test_hosted_root_build_assets_are_served_at_compiled_urls(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "index.html").write_text('<script src="/assets/app.js"></script>', encoding="utf-8")
            for directory in ("assets", "static", "fonts"):
                (root / directory).mkdir()
            (root / "assets" / "app.js").write_text('console.log("staging");', encoding="utf-8")
            (root / "static" / "favicon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
            (root / "fonts" / "demo.woff").write_bytes(b"fontfixture")
            (root / "mailboxes.js").write_text("export const mailbox = true;", encoding="utf-8")
            (root / "sidebar.js").write_text("export const sidebar = true;", encoding="utf-8")
            client = TestClient(create_app(static_dir=folder))
            self.assertEqual(client.get("/signup").status_code, 200)
            self.assertEqual(client.get("/assets/app.js").status_code, 200)
            self.assertEqual(client.get("/static/favicon.svg").status_code, 200)
            self.assertEqual(client.get("/fonts/demo.woff").status_code, 200)
            self.assertEqual(client.get("/mailboxes.js").status_code, 200)
            self.assertEqual(client.get("/sidebar.js").status_code, 200)
            self.assertEqual(client.get("/api/auth/login").status_code, 404)

    def test_only_the_private_beta_preview_can_call_the_api_from_a_browser(self):
        headers = {
            "Origin": "https://peachpuff-curlew-471332.hostingersite.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        }
        allowed = self.client.options("/api/v1/workspaces", headers=headers)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(
            allowed.headers["access-control-allow-origin"],
            "https://peachpuff-curlew-471332.hostingersite.com",
        )
        self.assertNotIn("access-control-allow-credentials", allowed.headers)

        blocked = self.client.options(
            "/api/v1/workspaces",
            headers={**headers, "Origin": "https://untrusted.example"},
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertNotIn("access-control-allow-origin", blocked.headers)

    def test_auth_and_api_responses_have_private_cache_and_browser_defenses(self):
        for path in ("/api/v1/me", "/api/v1/capabilities", "/api/v1/workspaces"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
                self.assertIn("camera=()", response.headers["Permissions-Policy"])


if __name__ == "__main__":
    unittest.main()
