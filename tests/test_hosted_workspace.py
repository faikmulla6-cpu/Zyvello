"""Hosted API contract tests use fake identities and an isolated fake store."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from zyvello_hosted.auth import TokenRejected, VerifiedIdentity
from zyvello_hosted.health import create_app
from zyvello_hosted.repository import StoreConflict, StoreNotFound


WORKSPACE_A = "00000000-0000-4000-8000-0000000000a1"
WORKSPACE_B = "00000000-0000-4000-8000-0000000000b2"


class FakeStore:
    def __init__(self):
        self.by_owner = {"test-auth-a": [{"id": UUID(WORKSPACE_A), "name": "Agency A", "slug": "agency-a", "role": "owner"}],
                         "test-auth-b": [{"id": UUID(WORKSPACE_B), "name": "Agency B", "slug": "agency-b", "role": "owner"}]}
        self.jobs = {}
        self.contacts = {}
        self.campaigns = {}
        self.recipients = set()
        self.paid_workspaces = set()

    def list_workspaces(self, auth_user_id):
        return self.by_owner.get(auth_user_id, [])

    def has_paid_entitlement(self, auth_user_id, workspace_id):
        self.get_workspace(auth_user_id, workspace_id)
        return workspace_id in self.paid_workspaces

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

    def list_contacts(self, auth_user_id, workspace_id):
        self.get_workspace(auth_user_id, workspace_id)
        return [r for r in self.contacts.values() if r["workspace_id"] == workspace_id]

    def list_companies(self, auth_user_id, workspace_id):
        self.get_workspace(auth_user_id, workspace_id)
        return [{"name": "Example Company", "contact_count": 2}]

    def create_contact(self, auth_user_id, workspace_id, *, full_name, email, company=""):
        self.get_workspace(auth_user_id, workspace_id)
        if any(r["workspace_id"] == workspace_id and r["email"] == email.lower() for r in self.contacts.values()):
            raise StoreConflict("This email already exists in the workspace")
        row = {"id": uuid4(), "workspace_id": workspace_id, "full_name": full_name,
               "email": email.lower(), "company": company}
        self.contacts[row["id"]] = row
        return row

    def list_campaigns(self, auth_user_id, workspace_id):
        self.get_workspace(auth_user_id, workspace_id)
        return [r for r in self.campaigns.values() if r["workspace_id"] == workspace_id]

    def create_campaign(self, auth_user_id, workspace_id, *, name, goal=""):
        self.get_workspace(auth_user_id, workspace_id)
        row = {"id": uuid4(), "workspace_id": workspace_id, "name": name, "goal": goal, "status": "draft"}
        self.campaigns[row["id"]] = row
        return row

    def list_campaign_contacts(self, auth_user_id, workspace_id, campaign_id):
        self.get_workspace(auth_user_id, workspace_id)
        if campaign_id not in self.campaigns or self.campaigns[campaign_id]["workspace_id"] != workspace_id:
            raise StoreNotFound("Campaign not found")
        return [self.contacts[contact_id] for c_id, contact_id in self.recipients if c_id == campaign_id]

    def add_campaign_contact(self, auth_user_id, workspace_id, campaign_id, contact_id):
        self.list_campaign_contacts(auth_user_id, workspace_id, campaign_id)
        if contact_id not in self.contacts or self.contacts[contact_id]["workspace_id"] != workspace_id:
            raise StoreNotFound("Contact not found")
        pair = (campaign_id, contact_id)
        created = pair not in self.recipients
        self.recipients.add(pair)
        return {"created": created, "contact_id": contact_id}

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
            if token == "good-new":
                return VerifiedIdentity("test-auth-uninvited", "uninvited@example.test")
            raise TokenRejected

        self.verifier_patch = patch("zyvello_hosted.health.NeonTokenVerifier", return_value=SimpleNamespace(verify=verify))
        self.verifier_patch.start()
        self.addCleanup(self.verifier_patch.stop)

        def dispatch():
            self.dispatch_count += 1

        self.client = TestClient(create_app(auth_base_url="https://identity.example.test/db/auth",
                                            store_override=self.store, subscription_enforcement_override=False, dispatch_job=dispatch,
                                            allow_workspace_creation_override=True))

    @staticmethod
    def token(value):
        return {"Authorization": "Bearer " + value}

    def test_every_workspace_endpoint_requires_bearer(self):
        routes = [("get", "/api/v1/workspaces"),
                  ("post", "/api/v1/workspaces"),
                  ("get", f"/api/v1/workspaces/{WORKSPACE_A}"),
                  ("get", f"/api/v1/workspaces/{WORKSPACE_A}/jobs"),
                  ("post", f"/api/v1/workspaces/{WORKSPACE_A}/jobs"),
                  ("get", f"/api/v1/workspaces/{WORKSPACE_A}/contacts"),
                  ("post", f"/api/v1/workspaces/{WORKSPACE_A}/contacts"),
                  ("get", f"/api/v1/workspaces/{WORKSPACE_A}/campaigns"),
                  ("post", f"/api/v1/workspaces/{WORKSPACE_A}/campaigns"),
                  ("get", f"/api/v1/workspaces/{WORKSPACE_A}/campaigns/{WORKSPACE_A}/contacts"),
                  ("post", f"/api/v1/workspaces/{WORKSPACE_A}/campaigns/{WORKSPACE_A}/contacts")]
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

    def test_public_onboarding_allows_new_verified_identity_to_create_free_workspace(self):
        result = self.client.get("/api/v1/workspaces", headers=self.token("good-new"))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["workspaces"], [])
        created = self.client.post("/api/v1/workspaces", headers=self.token("good-new"),
                                   json={"slug": "new-agency", "name": "New agency"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(len(self.store.by_owner["test-auth-uninvited"]), 1)
        self.assertEqual(self.client.get(f"/api/v1/workspaces/{WORKSPACE_A}",
                                         headers=self.token("good-new")).status_code, 404)

    def test_paid_subscription_is_required_for_every_operational_workspace_route(self):
        gated = TestClient(create_app(auth_base_url="https://identity.example.test/db/auth",
                                      store_override=self.store, subscription_enforcement_override=True,
                                      allow_workspace_creation_override=True))
        headers = self.token("good-a")
        feature_paths = (
            f"/api/v1/workspaces/{WORKSPACE_A}/contacts",
            f"/api/v1/workspaces/{WORKSPACE_A}/campaigns",
            f"/api/v1/workspaces/{WORKSPACE_A}/jobs",
            f"/api/v1/workspaces/{WORKSPACE_A}/drafts",
            f"/api/v1/workspaces/{WORKSPACE_A}/companies",
            f"/api/v1/workspaces/{WORKSPACE_A}/credentials/groq",
        )
        self.assertEqual(gated.get("/api/v1/workspaces", headers=headers).status_code, 200)
        self.assertEqual(gated.get(f"/api/v1/workspaces/{WORKSPACE_A}", headers=headers).status_code, 200)
        for path in feature_paths:
            with self.subTest(path=path):
                self.assertEqual(gated.get(path, headers=headers).status_code, 402)
                self.assertEqual(gated.get(path, headers=self.token("good-b")).status_code, 404)
        self.assertEqual(gated.post(feature_paths[0], headers=headers,
                                    json={"full_name": "Not Paid", "email": "unpaid@example.test"}).status_code, 402)
        self.store.paid_workspaces.add(UUID(WORKSPACE_A))
        self.assertEqual(gated.get(feature_paths[0], headers=headers).status_code, 200)
        self.assertEqual(gated.post(feature_paths[0], headers=headers,
                                    json={"full_name": "Paid User", "email": "paid@example.test"}).status_code, 201)
        companies = gated.get(f"/api/v1/workspaces/{WORKSPACE_A}/companies", headers=headers)
        self.assertEqual(companies.status_code, 200)
        self.assertEqual(companies.json()["companies"], [{"name": "Example Company", "contact_count": 2}])
        self.store.paid_workspaces.remove(UUID(WORKSPACE_A))
        self.assertEqual(gated.get(feature_paths[0], headers=headers).status_code, 402)

    def test_all_operational_routes_have_paid_dependency(self):
        protected = 0
        for route in self.client.app.routes:
            path = getattr(route, "path", "")
            if "/api/v1/workspaces/{workspace_id}/" not in path:
                continue
            # Workspace identity, settings, members and invitations are
            # free account-management features. Managers can also inspect and
            # disconnect Gmail after a subscription ends.
            if ("/billing" in path or path.endswith("/entitlement")
                    or "/members" in path or "/invitations" in path
                    or (path.endswith("/gmail") and route.methods <= {"GET", "DELETE"})):
                continue
            protected += 1
            self.assertTrue(any(getattr(item.call, "__name__", "") == "require_paid_workspace"
                                for item in route.dependant.dependencies), path)
        self.assertGreaterEqual(protected, 15)

    def test_closed_beta_rejects_new_workspace_even_with_verified_jwt(self):
        locked = TestClient(create_app(auth_base_url="https://identity.example.test/db/auth",
                                       store_override=self.store, subscription_enforcement_override=False,
                                       allow_workspace_creation_override=False))
        response = locked.post("/api/v1/workspaces", headers=self.token("good-b"),
                               json={"slug": "fresh-new-agency", "name": "Uninvited agency"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(self.store.by_owner["test-auth-b"]), 1)
        self.assertEqual(locked.get("/api/v1/workspaces", headers=self.token("good-a")).status_code, 200)

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

    def test_contacts_are_private_and_email_is_unique_per_workspace(self):
        path_a = f"/api/v1/workspaces/{WORKSPACE_A}/contacts"
        body = {"full_name": "Example Contact", "email": "qa@example.test", "company": "Testing"}
        self.assertEqual(self.client.post(path_a, headers=self.token("good-a"), json=body).status_code, 201)
        self.assertEqual(self.client.post(path_a, headers=self.token("good-a"), json=body).status_code, 409)
        self.assertEqual(self.client.get(path_a, headers=self.token("good-a")).json()["contacts"][0]["email"], "qa@example.test")
        self.assertEqual(self.client.get(path_a, headers=self.token("good-b")).status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/workspaces/{WORKSPACE_B}/contacts", headers=self.token("good-b")).json()["contacts"], [])
        self.assertEqual(self.client.post(path_a, headers=self.token("good-b"), json=body).status_code, 404)
        self.assertEqual(self.client.post(path_a, headers=self.token("good-a"), json={**body, "email": "invalid"}).status_code, 422)

    def test_draft_campaign_and_recipient_same_tenant_only(self):
        base_a = f"/api/v1/workspaces/{WORKSPACE_A}"
        contact = self.client.post(base_a + "/contacts", headers=self.token("good-a"),
                                   json={"full_name": "QA", "email": "qa@example.test"}).json()["contact"]
        campaign = self.client.post(base_a + "/campaigns", headers=self.token("good-a"),
                                    json={"name": "QA draft", "goal": "No sends"}).json()["campaign"]
        self.assertEqual(campaign["status"], "draft")
        self.assertEqual(len(self.client.get(base_a + "/campaigns", headers=self.token("good-a")).json()["campaigns"]), 1)
        recipients = base_a + "/campaigns/" + campaign["id"] + "/contacts"
        first = self.client.post(recipients, headers=self.token("good-a"), json={"contact_id": contact["id"]})
        again = self.client.post(recipients, headers=self.token("good-a"), json={"contact_id": contact["id"]})
        self.assertEqual(first.status_code, 201)
        self.assertTrue(first.json()["created"])
        self.assertFalse(again.json()["created"])
        self.assertEqual(len(self.client.get(recipients, headers=self.token("good-a")).json()["contacts"]), 1)
        self.assertEqual(self.client.get(recipients, headers=self.token("good-b")).status_code, 404)
        self.assertEqual(self.client.post(recipients, headers=self.token("good-b"), json={"contact_id": contact["id"]}).status_code, 404)
        self.assertEqual(self.client.post(base_a + "/campaigns", headers=self.token("good-a"),
                                          json={"name": "QA", "status": "sending"}).status_code, 422)
        self.assertEqual(self.client.get("/api/v1/capabilities").json()["outbound_sending_enabled"], False)


if __name__ == "__main__":
    unittest.main()
