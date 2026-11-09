"""Hosted API contract tests use fake identities and an isolated fake store."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

from zyvello_hosted.auth import TokenRejected, VerifiedIdentity
from zyvello_hosted.health import create_app
from zyvello_hosted.repository import StoreNotFound


WORKSPACE_A = "00000000-0000-4000-8000-0000000000a1"
WORKSPACE_B = "00000000-0000-4000-8000-0000000000b2"


class FakeStore:
    def __init__(self):
        self.by_owner = {"test-auth-a": [{"id": UUID(WORKSPACE_A), "name": "Agency A", "slug": "agency-a", "role": "owner"}],
                         "test-auth-b": [{"id": UUID(WORKSPACE_B), "name": "Agency B", "slug": "agency-b", "role": "owner"}]}
        self.jobs = {}

    def list_workspaces(self, auth_user_id):
        return self.by_owner.get(auth_user_id, [])

    def create_workspace(self, auth_user_id, *, slug, name):
        row = {"id": UUID("00000000-0000-4000-8000-0000000000c3"), "name": name, "slug": slug, "role": "owner"}
        self.by_owner.setdefault(auth_user_id, []).append(row)
        return row

    def get_workspace(self, auth_user_id, workspace_id):
        return next((row for row in self.list_workspaces(auth_user_id) if row["id"] == workspace_id), None) or self._not_found()

    def list_jobs(self, auth_user_id, workspace_id, *, limit=25):
        self.get_workspace(auth_user_id, workspace_id)
        return [row for row in self.jobs.values() if row["workspace_id"] == workspace_id]

    def enqueue_health_job(self, auth_user_id, workspace_id, *, idempotency_key):
        self.get_workspace(auth_user_id, workspace_id)
        if idempotency_key in self.jobs:
            return {"id": self.jobs[idempotency_key]["id"], "created": False, "status": "queued"}
        row = {"id": UUID("00000000-0000-4000-8000-0000000000d4"), "status": "queued", "created": True}
        self.jobs[idempotency_key] = {"id": row["id"], "workspace_id": workspace_id, "kind": "workspace_health", "status": "queued"}
        return row

    @staticmethod
    def _not_found():
        raise StoreNotFound("Workspace not found")


class HostedWorkspaceApiTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.dispatch_count = 0

        def verify(token):
            if token == "good-a":
                return VerifiedIdentity("test-auth-a", "a@example.test")
            if token == "good-b":
                return VerifiedIdentity("test-auth-b", "b@example.test")
            raise TokenRejected

        self.verifier_patch = patch("zyvello_hosted.health.NeonTokenVerifier", return_value=SimpleNamespace(verify=verify))
        self.verifier_patch.start()
        self.addCleanup(self.verifier_patch.stop)

        def dispatch():
            self.dispatch_count += 1

        self.client = TestClient(create_app(auth_base_url="https://identity.example.test/db/auth",
                                            store_override=self.store, dispatch_job=dispatch))

    @staticmethod
    def token(value):
        return {"Authorization": "Bearer " + value}

    def test_every_workspace_endpoint_requires_bearer(self):
        routes = [("get", "/api/v1/workspaces"),
                  ("post", "/api/v1/workspaces"),
                  ("get", f"/api/v1/workspaces/{WORKSPACE_A}"),
                  ("get", f"/api/v1/workspaces/{WORKSPACE_A}/jobs"),
                  ("post", f"/api/v1/workspaces/{WORKSPACE_A}/jobs")]
        for method, path in routes:
            with self.subTest(path=path):
                self.assertEqual(getattr(self.client, method)(path).status_code, 401)
        self.assertEqual(self.client.get("/api/v1/workspaces", headers=self.token("forged")).status_code, 401)

    def test_workspaces_are_scoped_to_verified_subject(self):
        a = self.client.get("/api/v1/workspaces", headers=self.token("good-a"))
        b = self.client.get("/api/v1/workspaces", headers=self.token("good-b"))
        self.assertEqual(a.json()["workspaces"][0]["slug"], "agency-a")
        self.assertEqual(b.json()["workspaces"][0]["slug"], "agency-b")
        self.assertEqual(self.client.get(f"/api/v1/workspaces/{WORKSPACE_B}", headers=self.token("good-a")).status_code, 404)

    def test_create_workspace_validates_and_uses_verified_identity(self):
        bad = self.client.post("/api/v1/workspaces", headers=self.token("good-a"), json={"slug": "UPPER", "name": "Fake"})
        self.assertEqual(bad.status_code, 422)
        okay = self.client.post("/api/v1/workspaces", headers=self.token("good-a"), json={"slug": "new-agency", "name": "Agency"})
        self.assertEqual(okay.status_code, 201)
        self.assertEqual(okay.json()["workspace"]["slug"], "new-agency")
        self.assertEqual(len(self.store.by_owner["test-auth-a"]), 2)
        self.assertEqual(len(self.store.by_owner["test-auth-b"]), 1)

    def test_health_job_is_idempotent_and_only_in_own_workspace(self):
        path = f"/api/v1/workspaces/{WORKSPACE_A}/jobs"
        first = self.client.post(path, headers=self.token("good-a"), json={"idempotency_key": "same-action"})
        again = self.client.post(path, headers=self.token("good-a"), json={"idempotency_key": "same-action"})
        self.assertEqual(first.status_code, 202)
        self.assertTrue(first.json()["dispatch_requested"])
        self.assertFalse(again.json()["job"]["created"])
        self.assertEqual(self.dispatch_count, 1)
        cross = self.client.post(f"/api/v1/workspaces/{WORKSPACE_B}/jobs", headers=self.token("good-a"), json={"idempotency_key": "steal"})
        self.assertEqual(cross.status_code, 404)
        self.assertEqual(self.dispatch_count, 1)
        other_jobs = self.client.get(f"/api/v1/workspaces/{WORKSPACE_B}/jobs", headers=self.token("good-b"))
        self.assertEqual(other_jobs.json()["jobs"], [])

    def test_outbound_features_disabled(self):
        result = self.client.get("/api/v1/capabilities").json()
        self.assertEqual(result["job_kinds"], ["workspace_health"])
        self.assertFalse(result["ai_enabled"])
        self.assertFalse(result["outbound_sending_enabled"])
        self.assertFalse(result["payments_enabled"])
        self.assertEqual(self.client.post("/api/send").status_code, 404)


if __name__ == "__main__":
    unittest.main()
