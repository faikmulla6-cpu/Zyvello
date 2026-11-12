"""Workspace onboarding and team-management HTTP contract regression tests.

These exercise the real FastAPI routes with a fake identity provider and store.
PostgreSQL RLS is tested separately against an isolated Neon branch.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from tests.test_hosted_workspace import FakeStore, WORKSPACE_A, WORKSPACE_B
from zyvello_hosted.auth import TokenRejected, VerifiedIdentity
from zyvello_hosted.health import create_app
from zyvello_hosted.repository import StoreConflict, StoreForbidden, StoreNotFound

AUTH_A = "00000000-0000-4000-8000-0000000000a1"
AUTH_B = "00000000-0000-4000-8000-0000000000b2"
AUTH_C = "00000000-0000-4000-8000-0000000000c3"


class TeamStore(FakeStore):
    def __init__(self):
        super().__init__()
        self.by_owner = {
            AUTH_A: [{"id": UUID(WORKSPACE_A), "slug": "agency-a", "name": "Agency A", "role": "owner", "settings": {}}],
            AUTH_B: [{"id": UUID(WORKSPACE_B), "slug": "agency-b", "name": "Agency B", "role": "owner", "settings": {}}],
        }
        self.members = {
            UUID(WORKSPACE_A): {AUTH_A: "owner"},
            UUID(WORKSPACE_B): {AUTH_B: "owner"},
        }
        self.invites = {}
        self.bootstrap_count = 0

    def _own(self, user, workspace):
        self.get_workspace(user, workspace)
        return self.members.get(workspace, {}).get(user)

    def bootstrap_workspace(self, user):
        workspaces = self.list_workspaces(user)
        owned = next((row for row in workspaces if row["role"] == "owner"), None)
        if owned:
            return {"workspace": owned, "created": False}
        self.bootstrap_count += 1
        row = {"id": uuid4(), "slug": "my-business-new", "name": "My Business", "role": "owner", "settings": {}}
        self.by_owner.setdefault(user, []).append(row)
        self.members[row["id"]] = {user: "owner"}
        return {"workspace": row, "created": True}

    def update_workspace(self, user, workspace, *, name=None, settings=None):
        if self._own(user, workspace) not in {"owner", "admin"}:
            raise StoreForbidden("Owner or admin required")
        rows = [row for group in self.by_owner.values() for row in group if row["id"] == workspace]
        for row in rows:
            if name is not None:
                row["name"] = name
            if settings is not None:
                row.setdefault("settings", {}).update(settings)
        return deepcopy(self.get_workspace(user, workspace))

    def delete_empty_workspace(self, user, workspace):
        if self._own(user, workspace) != "owner":
            raise StoreForbidden("Owner required")
        if (len(self.members[workspace]) > 1
                or any(i["workspace_id"] == workspace for i in self.invites.values())
                or workspace in self.paid_workspaces
                or any(row["workspace_id"] == workspace for row in self.contacts.values())
                or any(row["workspace_id"] == workspace for row in self.jobs.values())):
            raise StoreConflict("Workspace is not empty")
        self.by_owner[user] = [row for row in self.by_owner[user] if row["id"] != workspace]
        del self.members[workspace]
        return {"deleted": True}

    def list_workspace_members(self, user, workspace):
        self._own(user, workspace)
        return [
            {"auth_user_id": member, "role": role, "email": {
                AUTH_A: "a@example.test", AUTH_B: "b@example.test", AUTH_C: "c@example.test"
            }[member]}
            for member, role in self.members[workspace].items()
        ]

    def create_workspace_invitation(self, user, workspace, *, email, role):
        if self._own(user, workspace) not in {"owner", "admin"}:
            raise StoreForbidden("Owner or admin required")
        token = "safe-test-token-value-" + str(uuid4()) + "-" + str(uuid4())
        invite = {"id": uuid4(), "invitee_email": email.lower(), "role": role,
                  "workspace_id": workspace, "used": False, "revoked": False}
        self.invites[token] = invite
        return {"invitation": invite, "token": token}

    def list_workspace_invitations(self, user, workspace):
        if self._own(user, workspace) not in {"owner", "admin"}:
            raise StoreForbidden("Owner or admin required")
        return [i for i in self.invites.values() if i["workspace_id"] == workspace]

    def accept_workspace_invitation(self, user, token):
        emails = {AUTH_A: "a@example.test", AUTH_B: "b@example.test", AUTH_C: "c@example.test"}
        invite = self.invites.get(token)
        if not invite or invite["used"] or invite["revoked"] or invite["invitee_email"] != emails.get(user):
            raise StoreNotFound("Invalid invitation")
        invite["used"] = True
        workspace = invite["workspace_id"]
        self.members[workspace][user] = invite["role"]
        workspace_info = next(row for group in self.by_owner.values() for row in group if row["id"] == workspace)
        self.by_owner.setdefault(user, []).append({**workspace_info, "role": invite["role"]})
        return self.get_workspace(user, workspace)

    def set_member_role(self, user, workspace, member_id, *, role):
        if self._own(user, workspace) != "owner":
            raise StoreForbidden("Owner required")
        if self.members[workspace].get(member_id) not in {"member", "admin"}:
            raise StoreNotFound("Member not editable")
        self.members[workspace][member_id] = role
        for row in self.by_owner.get(member_id, []):
            if row["id"] == workspace:
                row["role"] = role
        return {"auth_user_id": member_id, "role": role}

    def remove_workspace_member(self, user, workspace, member_id):
        current_role = self._own(user, workspace)
        if current_role != "owner" and user != member_id:
            raise StoreForbidden("Owner required")
        if self.members[workspace].get(member_id) not in {"member", "admin"}:
            raise StoreNotFound("Cannot remove owner")
        del self.members[workspace][member_id]
        self.by_owner[member_id] = [row for row in self.by_owner.get(member_id, []) if row["id"] != workspace]
        return {"removed_auth_user_id": member_id}

    def revoke_workspace_invitation(self, user, workspace, invite_id):
        if self._own(user, workspace) not in {"owner", "admin"}:
            raise StoreForbidden("Owner or admin required")
        for item in self.invites.values():
            if item["id"] == invite_id and item["workspace_id"] == workspace and not item["used"]:
                item["revoked"] = True
                return {"id": invite_id}
        raise StoreNotFound("Invite not found")


class WorkspaceManagementApiTests(TestCase):
    def setUp(self):
        self.store = TeamStore()
        users = {
            "good-a": VerifiedIdentity(AUTH_A, "a@example.test"),
            "good-b": VerifiedIdentity(AUTH_B, "b@example.test"),
            "good-c": VerifiedIdentity(AUTH_C, "c@example.test"),
        }

        def verify(token):
            if token not in users:
                raise TokenRejected
            return users[token]

        mocked = patch("zyvello_hosted.health.NeonTokenVerifier",
                       return_value=SimpleNamespace(verify=verify))
        mocked.start()
        self.addCleanup(mocked.stop)
        self.client = TestClient(create_app(
            auth_base_url="https://identity.example.test/db/auth",
            store_override=self.store, allow_workspace_creation_override=True,
            subscription_enforcement_override=True,
        ))

    def h(self, user):
        return {"Authorization": "Bearer good-" + user}

    def test_signup_bootstrap_is_idempotent_and_unpaid(self):
        path = "/api/v1/workspaces/bootstrap"
        self.assertEqual(self.client.post(path).status_code, 401)
        first = self.client.post(path, headers=self.h("c"), json={})
        second = self.client.post(path, headers=self.h("c"), json={})
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["created"])
        self.assertFalse(second.json()["created"])
        self.assertEqual(first.json()["workspace"]["id"], second.json()["workspace"]["id"])
        self.assertEqual(self.store.bootstrap_count, 1)
        self.assertEqual(self.client.get("/api/v1/workspaces", headers=self.h("c")).status_code, 200)
        ws = first.json()["workspace"]["id"]
        self.assertEqual(self.client.get(f"/api/v1/workspaces/{ws}/contacts", headers=self.h("c")).status_code, 402)

    def test_invited_account_still_gets_its_own_private_workspace(self):
        invite = self.client.post(f"/api/v1/workspaces/{WORKSPACE_A}/invitations",
                                  headers=self.h("a"),
                                  json={"email": "c@example.test", "role": "member"}).json()
        self.assertEqual(self.client.post("/api/v1/workspace-invitations/accept",
                                          headers=self.h("c"),
                                          json={"token": invite["token"]}).status_code, 200)
        created = self.client.post("/api/v1/workspaces/bootstrap",
                                   headers=self.h("c"), json={}).json()
        self.assertTrue(created["created"])
        self.assertNotEqual(created["workspace"]["id"], WORKSPACE_A)
        self.assertEqual(created["workspace"]["role"], "owner")
        self.assertEqual(len(self.client.get("/api/v1/workspaces",
                                              headers=self.h("c")).json()["workspaces"]), 2)

    def test_workspace_settings_reject_unsafe_inputs(self):
        path = f"/api/v1/workspaces/{WORKSPACE_A}"
        for website in ("http://example.org", "https://localhost",
                        "https://127.0.0.1", "https://192.168.1.10",
                        "https://user:pass@example.org", "https://example.org:8080"):
            with self.subTest(website=website):
                self.assertEqual(self.client.patch(path, headers=self.h("a"),
                    json={"settings": {"website_url": website}}).status_code, 422)
        self.assertEqual(self.client.patch(path, headers=self.h("a"),
            json={"settings": {"timezone": "Not/A_Real_Zone"}}).status_code, 422)
        self.assertEqual(self.client.patch(path, headers=self.h("a"),
            json={"settings": {"website_url": "https://example.org"}}).status_code, 200)

    def test_workspace_settings_are_private_and_manager_only(self):
        path = f"/api/v1/workspaces/{WORKSPACE_A}"
        self.assertEqual(self.client.patch(path, headers=self.h("b"), json={"name": "Intruder"}).status_code, 404)
        self.assertEqual(self.client.patch(path, headers=self.h("a"), json={"created_by_auth_user_id": AUTH_B}).status_code, 422)
        self.assertEqual(self.client.patch(path, headers=self.h("a"), json={"settings": {"unknown": "no"}}).status_code, 422)
        changed = self.client.patch(path, headers=self.h("a"), json={"name": "Renamed",
                                           "settings": {"industry": "Software", "timezone": "Asia/Kolkata"}})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["workspace"]["name"], "Renamed")
        self.assertEqual(self.client.get(path, headers=self.h("b")).status_code, 404)

    def test_invitation_accept_requires_verified_matching_email_and_is_one_time(self):
        path = f"/api/v1/workspaces/{WORKSPACE_A}/invitations"
        self.assertEqual(self.client.post(path, headers=self.h("b"), json={
            "email": "b@example.test", "role": "member"}).status_code, 404)
        invitation = self.client.post(path, headers=self.h("a"), json={
            "email": "b@example.test", "role": "member"})
        self.assertEqual(invitation.status_code, 201)
        token = invitation.json()["token"]
        accept = "/api/v1/workspace-invitations/accept"
        self.assertEqual(self.client.post(accept, headers=self.h("c"), json={"token": token}).status_code, 404)
        self.assertEqual(self.client.post(accept, headers=self.h("b"), json={"token": token}).status_code, 200)
        self.assertEqual(self.client.post(accept, headers=self.h("b"), json={"token": token}).status_code, 404)
        roster = self.client.get(f"/api/v1/workspaces/{WORKSPACE_A}/members", headers=self.h("b"))
        self.assertEqual(roster.status_code, 200)
        self.assertEqual(len(roster.json()["members"]), 2)
        self.assertEqual(self.client.patch(f"/api/v1/workspaces/{WORKSPACE_A}",
                                           headers=self.h("b"), json={"name": "Unauthorized"}).status_code, 403)

    def test_owner_roles_and_member_departure(self):
        invite = self.client.post(f"/api/v1/workspaces/{WORKSPACE_A}/invitations", headers=self.h("a"),
                                  json={"email": "b@example.test", "role": "member"}).json()
        self.client.post("/api/v1/workspace-invitations/accept", headers=self.h("b"), json={"token": invite["token"]})
        target = f"/api/v1/workspaces/{WORKSPACE_A}/members/{AUTH_B}"
        self.assertEqual(self.client.patch(target, headers=self.h("b"), json={"role": "admin"}).status_code, 403)
        self.assertEqual(self.client.patch(target, headers=self.h("a"), json={"role": "owner"}).status_code, 422)
        self.assertEqual(self.client.patch(target, headers=self.h("a"), json={"role": "admin"}).status_code, 200)
        self.assertEqual(self.client.patch(f"/api/v1/workspaces/{WORKSPACE_A}", headers=self.h("b"),
                                           json={"name": "Admin edit"}).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/v1/workspaces/{WORKSPACE_A}/members/{AUTH_A}",
                                            headers=self.h("a")).status_code, 404)
        self.assertEqual(self.client.delete(target, headers=self.h("b")).status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/workspaces/{WORKSPACE_A}", headers=self.h("b")).status_code, 404)

    def test_only_owner_can_delete_an_empty_workspace(self):
        path = f"/api/v1/workspaces/{WORKSPACE_A}"
        self.assertEqual(self.client.delete(path).status_code, 401)
        self.assertEqual(self.client.delete(path, headers=self.h("b")).status_code, 404)
        invitation = self.client.post(f"{path}/invitations",
            headers=self.h("a"), json={"email": "c@example.test", "role": "member"})
        self.assertEqual(invitation.status_code, 201)
        self.assertEqual(self.client.delete(path, headers=self.h("a")).status_code, 409)
        self.assertEqual(self.client.delete(
            f"/api/v1/workspaces/{WORKSPACE_B}", headers=self.h("b")).status_code, 200)
        self.assertEqual(self.client.get(
            f"/api/v1/workspaces/{WORKSPACE_B}", headers=self.h("b")).status_code, 404)
        self.assertEqual(self.client.get(path, headers=self.h("a")).status_code, 200)

    def test_invitation_revoke_and_unpaid_workspace_management(self):
        path = f"/api/v1/workspaces/{WORKSPACE_A}/invitations"
        invite = self.client.post(path, headers=self.h("a"), json={
            "email": "c@example.test", "role": "member"}).json()
        invite_id = invite["invitation"]["id"]
        self.assertEqual(self.client.post(f"{path}/{invite_id}/revoke", headers=self.h("a")).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/workspace-invitations/accept", headers=self.h("c"),
                                          json={"token": invite["token"]}).status_code, 404)
        self.assertEqual(self.client.get(path, headers=self.h("a")).status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/workspaces/{WORKSPACE_A}/contacts", headers=self.h("a")).status_code, 402)
