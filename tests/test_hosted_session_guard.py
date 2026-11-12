"""Authorization layer must reject a signed JWT when managed Auth revoked it.

The SQL integration check runs on an isolated Neon child branch separately.
"""

import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from zyvello_hosted.auth import TokenRejected, VerifiedIdentity
from zyvello_hosted.health import create_app
from zyvello_hosted.repository import StoreUnavailable
from zyvello_hosted.session_guard import NeonSessionGuard


ORIGIN = "https://identity.example.test/neondb/auth"


class SessionGuardApiTests(unittest.TestCase):
    def make_client(self, guard):
        def verify(value):
            if value == "signed-test-token":
                return VerifiedIdentity("approved-test-subject", "approved@example.test", int(time.time()) - 10)
            raise TokenRejected

        patcher = patch("zyvello_hosted.health.NeonTokenVerifier",
                        return_value=SimpleNamespace(verify=verify))
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(create_app(auth_base_url=ORIGIN, session_guard_override=guard))

    def test_revoked_or_reset_session_rejected_before_any_workspace_access(self):
        client = self.make_client(lambda _: False)
        for path in ("/api/v1/me", "/api/v1/workspaces"):
            response = client.get(path, headers={"Authorization": "Bearer signed-test-token"})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers.get("WWW-Authenticate"), "Bearer")
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")

    def test_database_failure_fails_closed_not_as_false_success(self):
        def unavailable(_):
            raise StoreUnavailable("sensitive db diagnostic")
        client = self.make_client(unavailable)
        response = client.get("/api/v1/me", headers={"Authorization": "Bearer signed-test-token"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("sensitive", response.text)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")

    def test_active_approved_session_can_authenticate(self):
        seen = []
        def approved(identity):
            seen.append(identity.issued_at)
            return True
        client = self.make_client(approved)
        response = client.get("/api/v1/me", headers={"Authorization": "Bearer signed-test-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "identity_verified")
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], int)

    def test_database_guard_rejects_missing_iat_without_query(self):
        guard = NeonSessionGuard("postgresql://safe-host/safe-db")
        self.assertFalse(guard(VerifiedIdentity("a", "a@example.test", None)))
        self.assertFalse(guard(VerifiedIdentity("a", "a@example.test", 0)))

    def test_guard_requires_postgres_and_rejects_unexpected_database_role(self):
        with self.assertRaises(ValueError):
            NeonSessionGuard("https://not-a-postgres-dsn")
        guard = NeonSessionGuard("postgresql://safe-host/safe-db")
        cursor = unittest.mock.MagicMock()
        cursor.fetchone.return_value = ("neondb_owner",)
        conn = unittest.mock.MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value.__enter__.return_value = cursor
        with patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=lambda *a, **k: conn, Error=Exception)}):
            with self.assertRaises(StoreUnavailable):
                guard(VerifiedIdentity("a", "a@example.test", 1800000000))
        self.assertEqual(cursor.execute.call_count, 1)


if __name__ == "__main__":
    unittest.main()
