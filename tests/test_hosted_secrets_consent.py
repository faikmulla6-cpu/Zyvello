"""No real provider keys, email accounts, or network calls are used here."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx

from zyvello_hosted.auth import TokenRejected, VerifiedIdentity
from zyvello_hosted.credentials import (CredentialConfigurationError,
                                        CredentialDecryptionError, CredentialVault)
from zyvello_hosted.health import create_app
from zyvello_hosted.groq_drafts import DraftProviderError, GROQ_COMPLETIONS_URL, GroqDraftClient
from zyvello_hosted.repository import StoreForbidden, StoreNotFound


WS_A = UUID("00000000-0000-4000-8000-0000000000a1")
WS_B = UUID("00000000-0000-4000-8000-0000000000b2")
CONTACT = UUID("00000000-0000-4000-8000-0000000000c3")
CAMPAIGN = UUID("00000000-0000-4000-8000-0000000000d4")
SAMPLE_KEY = "gsk_" + "A" * 42


class FakeSecureStore:
    def __init__(self) -> None:
        self.secrets: dict[UUID, str] = {}
        self.events: dict[UUID, list[dict]] = {}
        self.jobs: dict[str, dict] = {}

    @staticmethod
    def _check(auth_user_id: str, workspace_id: UUID) -> None:
        if (auth_user_id, workspace_id) not in {("user-a", WS_A), ("user-b", WS_B)}:
            raise StoreNotFound("Workspace not found")

    def get_groq_credential_status(self, auth_user_id, workspace_id):
        self._check(auth_user_id, workspace_id)
        return {"configured": workspace_id in self.secrets,
                "updated_at": datetime.now(timezone.utc) if workspace_id in self.secrets else None}

    def put_groq_credential(self, auth_user_id, workspace_id, *, ciphertext):
        self._check(auth_user_id, workspace_id)
        self.secrets[workspace_id] = ciphertext
        return {"configured": True, "updated_at": datetime.now(timezone.utc)}

    def delete_groq_credential(self, auth_user_id, workspace_id):
        self._check(auth_user_id, workspace_id)
        return {"removed": self.secrets.pop(workspace_id, None) is not None}

    def get_contact_permission(self, auth_user_id, workspace_id, contact_id):
        self._check(auth_user_id, workspace_id)
        if contact_id != CONTACT:
            raise StoreNotFound("Contact not found")
        return self.events.get(workspace_id, [])[-1] if self.events.get(workspace_id) else {"action": "unknown"}

    def record_contact_permission(self, auth_user_id, workspace_id, contact_id, *, action, source, evidence):
        self.get_contact_permission(auth_user_id, workspace_id, contact_id)
        events = self.events.setdefault(workspace_id, [])
        if action == "claimed_consent" and len(evidence) < 10:
            raise ValueError("Evidence required")
        if action == "claimed_consent" and any(e["action"] in ("opted_out", "suppressed") for e in events):
            raise StoreForbidden("Previously suppressed contact cannot be opted back in here")
        row = {"action": action, "source": source, "evidence": evidence,
               "created_at": datetime.now(timezone.utc)}
        events.append(row)
        return row

    def list_generated_drafts(self, auth_user_id, workspace_id):
        self._check(auth_user_id, workspace_id)
        return []

    def enqueue_groq_draft(self, auth_user_id, workspace_id, *, campaign_id, contact_id, idempotency_key):
        self._check(auth_user_id, workspace_id)
        if campaign_id != CAMPAIGN or contact_id != CONTACT:
            raise StoreNotFound("Draft target not found")
        if workspace_id not in self.secrets:
            raise StoreForbidden("No credential")
        if any(e["action"] in ("opted_out", "suppressed") for e in self.events.get(workspace_id, [])):
            raise StoreForbidden("Suppressed")
        if idempotency_key not in self.jobs:
            self.jobs[idempotency_key] = {"id": CAMPAIGN, "status": "queued"}
            return {**self.jobs[idempotency_key], "created": True}
        return {**self.jobs[idempotency_key], "created": False}


class CredentialVaultTests(unittest.TestCase):
    def test_missing_and_invalid_encryption_key_fail_closed(self):
        with self.assertRaises(CredentialConfigurationError):
            CredentialVault(None)
        with self.assertRaises(CredentialConfigurationError):
            CredentialVault("invalid")

    def test_ciphertext_roundtrip_rotation_and_tamper(self):
        old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        historical = CredentialVault(old).seal(SAMPLE_KEY)
        vault = CredentialVault(new + "," + old)
        self.assertEqual(vault.open(historical), SAMPLE_KEY)
        encrypted = vault.seal(SAMPLE_KEY)
        self.assertNotIn(SAMPLE_KEY, encrypted)
        self.assertEqual(vault.open(encrypted), SAMPLE_KEY)
        with self.assertRaises(CredentialDecryptionError):
            CredentialVault(old).open(encrypted)
        with self.assertRaises(CredentialDecryptionError):
            vault.open(encrypted[:-3] + "xyz")


class HostedSecretsAndPermissionApiTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeSecureStore()
        self.vault = CredentialVault(Fernet.generate_key().decode())

        def verify(token):
            if token in ("a", "b"):
                return VerifiedIdentity("user-" + token, token + "@example.test")
            raise TokenRejected

        patcher = patch("zyvello_hosted.health.NeonTokenVerifier", return_value=SimpleNamespace(verify=verify))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(auth_base_url="https://auth.example.test/db/auth",
                                            store_override=self.store, credential_vault_override=self.vault))

    @staticmethod
    def header(token):
        return {"Authorization": "Bearer " + token}

    def test_credentials_require_authenticated_tenant_manager_and_hide_key(self):
        path = f"/api/v1/workspaces/{WS_A}/credentials/groq"
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.put(path, json={"api_key": SAMPLE_KEY}).status_code, 401)
        self.assertEqual(self.client.get(path, headers=self.header("b")).status_code, 404)
        self.assertEqual(self.client.put(path, headers=self.header("b"),
                                         json={"api_key": SAMPLE_KEY}).status_code, 404)
        response = self.client.put(path, headers=self.header("a"), json={"api_key": SAMPLE_KEY})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(SAMPLE_KEY, response.text)
        self.assertNotIn(SAMPLE_KEY, self.store.secrets[WS_A])
        self.assertEqual(self.vault.open(self.store.secrets[WS_A]), SAMPLE_KEY)
        status = self.client.get(path, headers=self.header("a"))
        self.assertTrue(status.json()["configured"])
        self.assertNotIn("ciphertext", status.text)
        self.assertFalse(status.json()["ai_execution_enabled"])
        self.assertEqual(self.client.delete(path, headers=self.header("a")).status_code, 200)
        self.assertFalse(self.client.get(path, headers=self.header("a")).json()["configured"])

    def test_invalid_secret_rejected_and_api_fails_without_vault(self):
        path = f"/api/v1/workspaces/{WS_A}/credentials/groq"
        self.assertEqual(self.client.put(path, headers=self.header("a"),
                                         json={"api_key": "not-a-groq-credential"}).status_code, 422)
        with patch.dict("os.environ", {"ZYVELLO_CREDENTIAL_ENCRYPTION_KEYS": ""}):
            missing_vault = TestClient(create_app(auth_base_url="https://auth.example.test/db/auth",
                                                  store_override=self.store))
        self.assertEqual(missing_vault.put(path, headers=self.header("a"),
                                           json={"api_key": SAMPLE_KEY}).status_code, 503)

    def test_permission_is_unknown_by_default_and_never_authorizes_sending(self):
        path = f"/api/v1/workspaces/{WS_A}/contacts/{CONTACT}/permission"
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get(path, headers=self.header("b")).status_code, 404)
        empty = self.client.get(path, headers=self.header("a"))
        self.assertEqual(empty.json()["permission"]["action"], "unknown")
        claimed = self.client.post(path, headers=self.header("a"), json={
            "action": "claimed_consent", "source": "customer_import",
            "evidence": "opt in at 2026-01-01"})
        self.assertEqual(claimed.status_code, 201)
        self.assertFalse(claimed.json()["outbound_allowed"])
        self.assertEqual(self.client.get(path, headers=self.header("a")).json()["permission"]["action"],
                         "claimed_consent")
        self.assertEqual(self.client.post(path, headers=self.header("a"), json={
            "action": "claimed_consent", "source": "import", "evidence": ""}).status_code, 422)
        suppressed = self.client.post(path, headers=self.header("a"), json={
            "action": "opted_out", "source": "recipient_request"})
        self.assertEqual(suppressed.status_code, 201)
        self.assertEqual(self.client.post(path, headers=self.header("a"), json={
            "action": "claimed_consent", "source": "import", "evidence": "new claim at 2026-01-02"}).status_code, 403)
        self.assertEqual(self.client.get(path, headers=self.header("a")).json()["permission"]["action"],
                         "opted_out")
        self.assertEqual(self.client.post(path, headers=self.header("b"), json={
            "action": "suppressed", "source": "manual"}).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/capabilities").json()["outbound_sending_enabled"], False)

    def test_ai_job_is_default_disabled_and_does_not_send(self):
        path = f"/api/v1/workspaces/{WS_A}/drafts"
        data = {"campaign_id": str(CAMPAIGN), "contact_id": str(CONTACT), "idempotency_key": "ai-draft-unique-001"}
        self.assertEqual(self.client.post(path, json=data).status_code, 401)
        self.assertEqual(self.client.post(path, headers=self.header("a"), json=data).status_code, 503)
        self.assertEqual(self.client.get(path, headers=self.header("b")).status_code, 404)
        self.assertFalse(self.client.get("/api/v1/capabilities").json()["ai_enabled"])

        dispatch = []
        enabled = TestClient(create_app(auth_base_url="https://auth.example.test/db/auth",
                                        store_override=self.store, credential_vault_override=self.vault,
                                        enable_ai_drafts_override=True,
                                        dispatch_job=lambda: dispatch.append(True)))
        # Even with the feature flag, the credential and target must exist.
        self.assertEqual(enabled.post(path, headers=self.header("a"), json=data).status_code, 403)
        self.store.secrets[WS_A] = self.vault.seal(SAMPLE_KEY)
        self.assertEqual(enabled.post(path, headers=self.header("b"), json=data).status_code, 404)
        first = enabled.post(path, headers=self.header("a"), json=data)
        self.assertEqual(first.status_code, 202)
        self.assertFalse(first.json()["sent"])
        self.assertTrue(first.json()["dispatch_requested"])
        second = enabled.post(path, headers=self.header("a"), json=data)
        self.assertFalse(second.json()["job"]["created"])
        self.assertEqual(len(dispatch), 1)
        self.assertFalse(enabled.get("/api/v1/capabilities").json()["outbound_sending_enabled"])


class GroqProviderContractTests(unittest.TestCase):
    def test_request_never_contains_email_or_sends_anything(self):
        seen = []

        def respond(request):
            seen.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "Subject: Hello\nFriendly draft"}}]})

        client = GroqDraftClient(transport=httpx.MockTransport(respond))
        out = client.generate(api_key=SAMPLE_KEY, contact_name="Test Name", company="Company",
                              campaign_goal="Schedule a call")
        self.assertEqual(out, "Subject: Hello\nFriendly draft")
        self.assertEqual(str(seen[0].url), GROQ_COMPLETIONS_URL)
        self.assertEqual(seen[0].method, "POST")
        self.assertNotIn(SAMPLE_KEY, seen[0].content.decode())
        self.assertIn("Schedule a call", seen[0].content.decode())

    def test_provider_failures_are_sanitized(self):
        for code, message in [(429, "Provider rate limit reached"), (401, "Provider credential was rejected"),
                              (500, "Provider temporarily unavailable")]:
            with self.subTest(code=code):
                client = GroqDraftClient(transport=httpx.MockTransport(
                    lambda request: httpx.Response(code, json={"error": {"message": "sensitive provider echo"}})))
                with self.assertRaisesRegex(DraftProviderError, message) as result:
                    client.generate(api_key=SAMPLE_KEY, contact_name="A", company="B", campaign_goal="C")
                self.assertNotIn("sensitive provider echo", str(result.exception))
        malformed = GroqDraftClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"invalid": True})))
        with self.assertRaises(DraftProviderError):
            malformed.generate(api_key=SAMPLE_KEY, contact_name="A", company="B", campaign_goal="C")


if __name__ == "__main__":
    unittest.main()
