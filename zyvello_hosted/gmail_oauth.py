"""Hosted Gmail authorization for a workspace's sending mailbox."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.parse import urlencode, urlsplit

import httpx


SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
METADATA_SCOPE = "https://www.googleapis.com/auth/gmail.metadata"
GMAIL_MESSAGES_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class GmailAuthorizationError(RuntimeError):
    """A Google response that cannot authorize a sending mailbox."""


@dataclass(frozen=True)
class GmailOAuth:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str

    @classmethod
    def from_env(cls) -> GmailOAuth | None:
        client_id = os.getenv("ZYVELLO_GMAIL_CLIENT_ID", "").strip()
        client_secret = os.getenv("ZYVELLO_GMAIL_CLIENT_SECRET", "").strip()
        redirect_uri = os.getenv("ZYVELLO_GMAIL_REDIRECT_URI", "").strip()
        try:
            url = urlsplit(redirect_uri)
            if (not client_id.endswith(".apps.googleusercontent.com") or not client_secret
                    or url.scheme != "https" or not url.hostname or url.username or url.password
                    or url.query or url.fragment or url.path != "/app" or url.port not in (None, 443)):
                return None
        except ValueError:
            return None
        return cls(client_id, client_secret, redirect_uri)

    def begin(self, *, reply_metadata: bool = False) -> tuple[str, str, str]:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        url = AUTH_URL + "?" + urlencode({
            "client_id": self.client_id, "redirect_uri": self.redirect_uri,
            "response_type": "code", "scope": " ".join(
                ["openid", EMAIL_SCOPE, SEND_SCOPE] + ([METADATA_SCOPE] if reply_metadata else [])
            ),
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
            "access_type": "offline", "prompt": "consent select_account",
        })
        return state, verifier, url

    def exchange(self, *, code: str, verifier: str) -> tuple[str, str]:
        try:
            with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
                response = client.post(TOKEN_URL, data={
                    "client_id": self.client_id, "client_secret": self.client_secret,
                    "redirect_uri": self.redirect_uri, "code": code,
                    "code_verifier": verifier, "grant_type": "authorization_code",
                })
                if response.status_code != 200:
                    raise GmailAuthorizationError("Google authorization expired or was rejected. Connect Gmail again.")
                data = response.json()
                refresh_token, access_token = data["refresh_token"], data["access_token"]
                scope = data.get("scope", "")
                if (not isinstance(scope, str) or not isinstance(refresh_token, str) or not 1 <= len(refresh_token) <= 2048
                        or not isinstance(access_token, str) or not 1 <= len(access_token) <= 4096
                        or SEND_SCOPE not in scope.split()):
                    raise GmailAuthorizationError("Grant Gmail send permission and offline access, then try again.")
                account = client.get(USERINFO_URL, headers={"Authorization": "Bearer " + access_token})
                if account.status_code != 200:
                    raise GmailAuthorizationError("Google could not verify the mailbox address.")
                profile = account.json()
                email = profile["email"]
                if (profile.get("email_verified") is not True or not isinstance(email, str)
                        or len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)):
                    raise GmailAuthorizationError("Google did not return a verified mailbox address.")
                return email, refresh_token
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError):
            raise GmailAuthorizationError("Google could not confirm the connection. Connect Gmail again.") from None

    def _refresh(self, client: httpx.Client, refresh_token: str) -> str:
        response = client.post(TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token",
        })
        if response.status_code != 200:
            raise GmailAuthorizationError("Google could not renew mailbox access. Reconnect Gmail.")
        token = response.json()["access_token"]
        if not isinstance(token, str) or not 1 <= len(token) <= 4096:
            raise GmailAuthorizationError("Google did not return usable mailbox access.")
        return token

    def send_message(self, *, sender: str, recipient: str, refresh_token: str,
                     subject: str, body: str, message_id: str | None = None) -> tuple[str, str | None]:
        try:
            message = EmailMessage()
            message["From"] = Address(addr_spec=sender)
            message["To"] = Address(addr_spec=recipient)
            message["Subject"] = subject
            if message_id:
                message["Message-ID"] = message_id
            message.set_content(body)
            encoded = base64.urlsafe_b64encode(message.as_bytes()).decode()
            with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
                token = self._refresh(client, refresh_token)
                sent = client.post(
                    GMAIL_MESSAGES_URL + "/messages/send",
                    headers={"Authorization": "Bearer " + token}, json={"raw": encoded},
                )
                if sent.status_code != 200:
                    raise GmailAuthorizationError("Gmail did not confirm the send.")
                result = sent.json()
                provider_id = result["id"]
                thread_id = result.get("threadId")
                if not isinstance(provider_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", provider_id):
                    raise GmailAuthorizationError("Gmail returned an invalid message identifier.")
                if thread_id is not None and (not isinstance(thread_id, str)
                                              or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", thread_id)):
                    raise GmailAuthorizationError("Gmail returned an invalid conversation identifier.")
                return provider_id, thread_id
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            raise GmailAuthorizationError("Gmail did not confirm the send.") from None

    def send_self_test(self, *, email: str, refresh_token: str) -> None:
        self.send_message(
            sender=email, recipient=email, refresh_token=refresh_token,
            subject="Zyvello Gmail connection test",
            body="This message confirms your Gmail mailbox can send from your Zyvello workspace.",
        )

    def newest_reply(self, *, refresh_token: str, thread_id: str, sent_id: str,
                     recipient: str, sent_after: datetime) -> datetime | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", thread_id):
            raise GmailAuthorizationError("Invalid Gmail conversation identifier.")
        try:
            with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
                token = self._refresh(client, refresh_token)
                response = client.get(
                    GMAIL_MESSAGES_URL + "/threads/" + thread_id,
                    headers={"Authorization": "Bearer " + token},
                    params=[("format", "metadata"), ("metadataHeaders", "From")],
                )
            if response.status_code == 403:
                raise GmailAuthorizationError("Reconnect Gmail and grant message metadata access for reply sync.")
            if response.status_code != 200:
                raise GmailAuthorizationError("Gmail did not return this conversation.")
            messages = response.json()["messages"]
            if not any(message["id"] == sent_id for message in messages):
                raise GmailAuthorizationError("Gmail did not return the original message in this conversation.")
            replies = []
            for message in messages:
                if message["id"] == sent_id or "SENT" in message.get("labelIds", []):
                    continue
                headers = message.get("payload", {}).get("headers", [])
                sender = next((header["value"] for header in headers if header["name"].lower() == "from"), "")
                if parseaddr(sender)[1].lower() == recipient.lower():
                    received = datetime.fromtimestamp(int(message["internalDate"]) / 1000, tz=timezone.utc)
                    if received >= sent_after:
                        replies.append(received)
            return max(replies, default=None)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError):
            raise GmailAuthorizationError("Gmail did not return usable reply metadata.") from None
