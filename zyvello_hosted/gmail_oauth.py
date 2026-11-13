"""Hosted Gmail authorization for a workspace's sending mailbox."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
from dataclasses import dataclass, field
from email.message import EmailMessage
from urllib.parse import urlencode, urlsplit

import httpx


SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
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

    def begin(self) -> tuple[str, str, str]:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        url = AUTH_URL + "?" + urlencode({
            "client_id": self.client_id, "redirect_uri": self.redirect_uri,
            "response_type": "code", "scope": f"openid {EMAIL_SCOPE} {SEND_SCOPE}",
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

    def send_self_test(self, *, email: str, refresh_token: str) -> None:
        message = EmailMessage()
        message["From"] = email
        message["To"] = email
        message["Subject"] = "Zyvello Gmail connection test"
        message.set_content("This message confirms your Gmail mailbox can send from your Zyvello workspace.")
        encoded = base64.urlsafe_b64encode(message.as_bytes()).decode()
        try:
            with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
                token_response = client.post(TOKEN_URL, data={
                    "client_id": self.client_id, "client_secret": self.client_secret,
                    "refresh_token": refresh_token, "grant_type": "refresh_token",
                })
                if token_response.status_code != 200:
                    raise GmailAuthorizationError("Google could not renew mailbox access. Reconnect Gmail.")
                token = token_response.json()["access_token"]
                if not isinstance(token, str) or not 1 <= len(token) <= 4096:
                    raise GmailAuthorizationError("Google did not return usable mailbox access.")
                sent = client.post(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                    headers={"Authorization": "Bearer " + token}, json={"raw": encoded},
                )
                if sent.status_code != 200 or not sent.json().get("id"):
                    raise GmailAuthorizationError("Gmail did not confirm the test message. Check your inbox before retrying.")
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            raise GmailAuthorizationError("Gmail did not confirm the test message. Check your inbox before retrying.") from None
