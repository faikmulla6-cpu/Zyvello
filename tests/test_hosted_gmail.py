"""Hosted Google OAuth and mailbox test contracts without provider traffic."""

import base64
import hashlib
import json
import unittest
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
from uuid import UUID

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from zyvello_hosted.auth import VerifiedIdentity
from zyvello_hosted.credentials import CredentialDecryptionError, CredentialVault
from zyvello_hosted.gmail_oauth import GmailOAuth, SEND_SCOPE
from zyvello_hosted.health import create_app
from zyvello_hosted.repository import StoreConflict, StoreNotFound


WORKSPACE = UUID(int=1)
OTHER = UUID(int=2)
MAILBOX = "owner@example.test"
OAUTH = GmailOAuth("fixture", "fixture-secret", "https://zyvello.example.test/app")
PATH = f"/api/v1/workspaces/{WORKSPACE}/gmail"


class GmailConnectionTests(unittest.TestCase):
    def setUp(self):
        self.vault = CredentialVault(Fernet.generate_key().decode())
        self.attempt = {}
        self.mailbox = {}
        verifier = SimpleNamespace(verify=lambda token: VerifiedIdentity(token, MAILBOX))
        auth = patch("zyvello_hosted.health.NeonTokenVerifier", return_value=verifier)
        auth.start()
        self.addCleanup(auth.stop)
        self.client = TestClient(create_app(
            auth_base_url="https://auth.example.test/db/auth", store_override=self,
            gmail_oauth_override=OAUTH, credential_vault_override=self.vault,
        ))
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer owner"}

    def has_paid_entitlement(self, user_id, workspace_id):
        return user_id != "unpaid"

    @staticmethod
    def check(user_id, workspace_id):
        if user_id != "owner" or workspace_id != WORKSPACE:
            raise StoreNotFound("Workspace not found")

    def begin_gmail_connection(self, user_id, workspace_id, **attempt):
        self.check(user_id, workspace_id)
        self.attempt = attempt

    def claim_gmail_connection(self, user_id, workspace_id, *, state_hash):
        self.check(user_id, workspace_id)
        if self.attempt.pop("state_hash", None) != state_hash:
            raise StoreConflict("Invalid attempt")
        return self.attempt["verifier_ciphertext"]

    def finish_gmail_connection(self, user_id, workspace_id, *, state_hash, **mailbox):
        self.check(user_id, workspace_id)
        self.mailbox = mailbox
        return {"email": mailbox["email"]}

    def reserve_gmail_self_test(self, user_id, workspace_id):
        self.check(user_id, workspace_id)
        return self.mailbox

    def test_google_consent_is_bound_to_workspace_and_single_use(self):
        start = self.client.post(PATH + "/begin", headers=self.headers)
        self.assertEqual(start.status_code, 200)
        state = start.json()["state"]
        query = parse_qs(urlsplit(start.json()["authorization_url"]).query)
        self.assertEqual(query["state"], [state])
        self.assertIn(SEND_SCOPE, query["scope"][0].split())
        self.assertEqual(self.attempt["state_hash"], hashlib.sha256(state.encode()).hexdigest())
        verifier = self.vault.open_gmail(WORKSPACE, "verifier", self.attempt["verifier_ciphertext"])
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], [challenge])
        data = {"code": "single-use-code", "state": state}
        with patch.object(GmailOAuth, "exchange", return_value=(MAILBOX, "private-refresh-token")) as exchange:
            self.assertEqual(self.client.post(PATH + "/complete", json=data, headers={
                "Authorization": "Bearer other"
            }).status_code, 404)
            completed = self.client.post(PATH + "/complete", json=data, headers=self.headers)
            self.assertEqual(completed.json()["email"], MAILBOX)
            self.assertNotIn("private-refresh-token", completed.text)
            exchange.assert_called_once_with(code="single-use-code", verifier=verifier)
            self.assertEqual(self.client.post(PATH + "/complete", json=data, headers=self.headers).status_code, 409)
        encrypted = self.mailbox["refresh_ciphertext"]
        self.assertEqual(self.vault.open_gmail(WORKSPACE, "refresh", encrypted), "private-refresh-token")
        with self.assertRaises(CredentialDecryptionError):
            self.vault.open_gmail(OTHER, "refresh", encrypted)

    def test_validation_redacts_codes_and_unpaid_cannot_start(self):
        self.assertEqual(self.client.post(PATH + "/begin", headers={"Authorization": "Bearer unpaid"}).status_code, 402)
        result = self.client.post(PATH + "/complete", json={
            "code": "private-provider-code", "state": "invalid",
        }, headers=self.headers)
        self.assertEqual(result.status_code, 422)
        self.assertNotIn("private-provider-code", result.text)

    def test_test_send_uses_the_stored_mailbox_only(self):
        self.mailbox = {"email": MAILBOX, "refresh_ciphertext": self.vault.seal_gmail(WORKSPACE, "refresh", "refresh")}
        with patch.object(GmailOAuth, "send_self_test") as send:
            response = self.client.post(PATH + "/test", json={"to": "other@example.test"}, headers=self.headers)
            self.assertEqual(response.json(), {"accepted": True})
            send.assert_called_once_with(email=MAILBOX, refresh_token="refresh")

    def test_provider_message_is_addressed_back_to_connected_account(self):
        def respond(request):
            if request.url.host == "oauth2.googleapis.com":
                return httpx.Response(200, json={"access_token": "test-token"})
            self.assertEqual(request.url.host, "gmail.googleapis.com")
            raw = base64.urlsafe_b64decode(json.loads(request.content)["raw"])
            message = BytesParser(policy=policy.default).parsebytes(raw)
            self.assertEqual(message["To"], MAILBOX)
            self.assertEqual(message["From"], MAILBOX)
            self.assertEqual(request.headers["Authorization"], "Bearer test-token")
            return httpx.Response(200, json={"id": "accepted"})
        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch("zyvello_hosted.gmail_oauth.httpx.Client", return_value=client):
            OAUTH.send_self_test(email=MAILBOX, refresh_token="fixture-token")
