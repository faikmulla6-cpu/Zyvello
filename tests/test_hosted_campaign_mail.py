"""A campaign pilot must never resend an ambiguous delivery."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from zyvello_hosted.auth import VerifiedIdentity
from zyvello_hosted.credentials import CredentialVault
from zyvello_hosted.gmail_oauth import GmailAuthorizationError, GmailOAuth
from zyvello_hosted.health import create_app
from zyvello_hosted.repository import StoreConflict

WORKSPACE = UUID(int=1)
OTHER = UUID(int=2)
DRAFT = UUID(int=3)
DELIVERY = UUID(int=4)
PILOT = "recipient@example.test"
OAUTH = GmailOAuth("test", "test", "https://zyvello.example.test/app")


class CampaignPilotTests(unittest.TestCase):
    def setUp(self):
        self.vault = CredentialVault(Fernet.generate_key().decode())
        self.token = self.vault.seal_gmail(WORKSPACE, "refresh", "private-token")
        self.attempted = False
        self.result = None
        self.reply = None
        self.now = datetime(2026, 11, 13, 10, tzinfo=timezone.utc)
        self.path = f"/api/v1/workspaces/{WORKSPACE}/drafts/{DRAFT}/send"
        verifier = SimpleNamespace(verify=lambda token: VerifiedIdentity(token, "owner@example.test"))
        auth = patch("zyvello_hosted.health.NeonTokenVerifier", return_value=verifier)
        auth.start()
        self.addCleanup(auth.stop)
        self.client = TestClient(create_app(
            auth_base_url="https://auth.example.test/db/auth", store_override=self,
            gmail_oauth_override=OAUTH, credential_vault_override=self.vault,
            campaign_pilot_recipient_override=PILOT, campaign_pilot_workspace_override=WORKSPACE,
            campaign_reply_sync_override=True,
        ), headers={"Authorization": "Bearer owner"})
        self.addCleanup(self.client.close)

    def has_paid_entitlement(self, user_id, workspace_id):
        return user_id != "unpaid"

    def reserve_campaign_delivery(self, user_id, workspace_id, *, draft_id, pilot_recipient, subject):
        self.assertEqual((workspace_id, draft_id, pilot_recipient), (WORKSPACE, DRAFT, PILOT))
        if self.attempted:
            raise StoreConflict("Already reserved")
        self.attempted = True
        return {"delivery_id": DELIVERY, "sender": "sender@example.test",
                "recipient": PILOT, "refresh_ciphertext": self.token}

    def finish_campaign_delivery(self, user_id, workspace_id, **result):
        self.result = result

    def reserve_campaign_reply_sync(self, user_id, workspace_id, delivery_id):
        return {"gmail_message_id": "original", "gmail_thread_id": "thread",
                "recipient": PILOT, "created_at": self.now, "refresh_ciphertext": self.token}

    def record_campaign_reply(self, user_id, workspace_id, **reply):
        self.reply = reply

    def test_send_uses_configured_target_and_blocks_second_attempt(self):
        with patch.object(GmailOAuth, "send_message", return_value=("original", "thread")) as send:
            self.assertEqual(self.client.post(self.path, json={"subject": "Pilot", "body": "Hello"}).json()["status"], "accepted")
            self.assertEqual(self.client.post(self.path, json={"subject": "Pilot", "body": "Hello"}).status_code, 409)
            send.assert_called_once()
            self.assertEqual(send.call_args.kwargs["recipient"], PILOT)
            self.assertEqual(self.result["thread_id"], "thread")

    def test_ambiguous_send_is_never_retried(self):
        with patch.object(GmailOAuth, "send_message", side_effect=GmailAuthorizationError("timeout")) as send:
            data = {"subject": "Pilot", "body": "Hello"}
            self.assertEqual(self.client.post(self.path, json=data).json()["status"], "unconfirmed")
            self.assertEqual(self.client.post(self.path, json=data).status_code, 409)
            send.assert_called_once()
            self.assertIsNone(self.result["message_id"])

    def test_unpaid_wrong_workspace_and_extra_recipient_are_denied(self):
        data = {"subject": "Pilot", "body": "Hello"}
        self.assertEqual(self.client.post(self.path, json=data, headers={"Authorization": "Bearer unpaid"}).status_code, 402)
        self.assertEqual(self.client.post(self.path.replace(str(WORKSPACE), str(OTHER)), json=data).status_code, 503)
        self.assertEqual(self.client.post(self.path, json={**data, "recipient": "other@example.test"}).status_code, 422)
        self.assertFalse(self.attempted)

    def test_reply_sync_uses_stored_thread_and_persists_timestamp(self):
        with patch.object(GmailOAuth, "newest_reply", return_value=self.now) as newest:
            path = f"/api/v1/workspaces/{WORKSPACE}/campaign-deliveries/{DELIVERY}/sync"
            self.assertEqual(self.client.post(path).json(), {"reply_found": True})
            self.assertEqual(newest.call_args.kwargs["thread_id"], "thread")
            self.assertEqual(self.reply, {"delivery_id": DELIVERY, "received_at": self.now})

    def test_metadata_request_filters_by_sender_and_original_message(self):
        def respond(request):
            if request.method == "POST":
                return httpx.Response(200, json={"access_token": "test"})
            self.assertEqual(request.url.params["format"], "metadata")
            self.assertEqual(request.url.params.get_list("metadataHeaders"), ["From"])
            return httpx.Response(200, json={"messages": [
                {"id": "original", "labelIds": ["SENT"]},
                {"id": "reply", "internalDate": str(int(self.now.timestamp() * 1000)),
                 "payload": {"headers": [{"name": "From", "value": f"Recipient <{PILOT}>"}]}},
                {"id": "unrelated", "internalDate": "1900000000000",
                 "payload": {"headers": [{"name": "From", "value": "other@example.test"}]}},
            ]})
        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch("zyvello_hosted.gmail_oauth.httpx.Client", return_value=client):
            self.assertEqual(OAUTH.newest_reply(refresh_token="test", thread_id="thread", sent_id="original",
                                               recipient=PILOT, sent_after=self.now), self.now)
