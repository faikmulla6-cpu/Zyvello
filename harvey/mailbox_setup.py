"""Dashboard mailbox onboarding. Existing adapters and CLI authorization stay intact."""
import asyncio
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
import yaml
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/mailboxes")
PROVIDERS = ("gmail", "microsoft", "smtp", "instantly")
PENDING = {}
_lock = asyncio.Lock()
_test_locks = {name: asyncio.Lock() for name in PROVIDERS}
KEYS = {
    "gmail": ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET"),
    "microsoft": ("M365_CLIENT_ID", "M365_TENANT_ID", "M365_CLIENT_SECRET"),
    "smtp": ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "IMAP_HOST", "IMAP_PORT", "IMAP_USERNAME", "IMAP_PASSWORD"),
    "instantly": ("INSTANTLY_API_KEY",),
}


def dashboard():
    from harvey import dashboard as module
    return module


def values():
    result = dict(os.environ)
    result.update(dashboard()._read_env_file())
    return result


def token_path(provider):
    return dashboard().token_store.path.parent / ("gmail_token.json" if provider == "gmail" else "m365_token.json")


def private_json(path: Path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Linked credential files must be managed manually.")
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".mailbox-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            if os.name != "nt":
                os.fchmod(stream.fileno(), 0o600)
            json.dump(content, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def health():
    try:
        result = json.loads((dashboard().token_store.path.parent / "mailbox-health.json").read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (ValueError, OSError):
        return {}


def fingerprint(provider):
    current = values()
    parts = [current.get(key, "") for key in KEYS[provider]]
    if provider in ("gmail", "microsoft"):
        try:
            token = json.loads(token_path(provider).read_text(encoding="utf-8"))
            parts += [str(token.get("obtained_at", "")), str(bool(token.get("refresh_token")))]
        except (OSError, ValueError):
            parts.append("missing")
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def configured(provider):
    current = values()
    required = {"gmail": ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET"), "microsoft": ("M365_CLIENT_ID",),
                "smtp": ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD"), "instantly": ("INSTANTLY_API_KEY",)}[provider]
    return all(current.get(key, "").strip() for key in required)


def stored_token(provider):
    try:
        return bool(json.loads(token_path(provider).read_text(encoding="utf-8")).get("refresh_token"))
    except (OSError, ValueError, AttributeError):
        return False


def redirect_uri(request, provider):
    return str(request.base_url).rstrip("/") + f"/api/mailboxes/oauth/{provider}/callback"


@router.get("")
async def status(request: Request):
    current, results = values(), health()
    rows = []
    for provider in PROVIDERS:
        ready = configured(provider)
        authorized = stored_token(provider) if provider in ("gmail", "microsoft") else ready
        check = results.get(provider, {})
        valid = check.get("fingerprint") == fingerprint(provider) and time.time() - check.get("checked_at", 0) < 86400
        state = ("connected" if check.get("ok") else "reconnect") if valid else (
            "saved" if authorized and ready else "not_connected" if ready else "setup_required")
        rows.append({"id": provider, "status": state, "app_ready": ready,
                     "email": check.get("email", "") if valid else "",
                     "checked_at": check.get("checked_at") if valid else None,
                     "message": check.get("message", "") if valid else "",
                     "redirect_uri": redirect_uri(request, provider) if provider in ("gmail", "microsoft") else None})
    public_fields = {key: current.get(key, "") for keys in KEYS.values() for key in keys
                     if not any(s in key for s in ("PASSWORD", "SECRET", "API_KEY"))}
    return {"active_provider": dashboard()._current_provider(), "providers": rows,
            "fields": public_fields,
            "saved_secrets": {key: bool(current.get(key)) for keys in KEYS.values() for key in keys
                              if any(s in key for s in ("PASSWORD", "SECRET", "API_KEY"))}}


class SaveMailbox(BaseModel):
    fields: dict[str, str] = Field(default_factory=dict)


class MailboxCheckError(Exception):
    """A safe, user-facing connection check result (never raw provider errors)."""


@router.post("/{provider}/save")
async def save(provider: str, body: SaveMailbox):
    if provider not in PROVIDERS:
        return JSONResponse({"message": "Unknown mailbox provider."}, status_code=404)
    try:
        if any(key not in KEYS[provider] for key in body.fields):
            raise ValueError()
        for key, value in body.fields.items():
            if len(value) > 4096 or any(c in value for c in "\r\n\x00"):
                raise ValueError()
            if key.endswith("_PORT") and value and (not value.isdigit() or not 1 <= int(value) <= 65535):
                raise ValueError()
            if key.endswith("_HOST") and value and not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", value):
                raise ValueError()
            if key == "M365_TENANT_ID" and value and not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", value):
                raise ValueError()
        async with dashboard()._env_lock:
            dashboard()._write_env_file(body.fields)
        return {"success": True, "message": "Saved securely on this installation. Test the connection next."}
    except (ValueError, OSError):
        return JSONResponse({"success": False, "message": "Check your server names, ports, and entered values."}, status_code=400)


def adapter(provider):
    from harvey.config import EnvConfig
    from harvey.integrations.gmail import GmailProvider
    from harvey.integrations.microsoft import MicrosoftProvider
    from harvey.integrations.smtp_mail import SmtpImapProvider
    current = values()
    env = EnvConfig(**{name: current[name.upper()] for name in EnvConfig.model_fields if current.get(name.upper())})
    config = SimpleNamespace(persona=SimpleNamespace(name="Zyvello", email=""))
    return {"gmail": GmailProvider, "microsoft": MicrosoftProvider, "smtp": SmtpImapProvider}[provider](config, env)


async def probe(provider):
    if provider == "instantly":
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.get("https://api.instantly.ai/api/v2/accounts", headers={"Authorization": "Bearer " + values().get("INSTANTLY_API_KEY", "")})
            return response.status_code == 200, ""
    mailbox = adapter(provider)
    ok, detail = await mailbox.test_connection()
    if not ok and provider == "smtp":
        if detail.startswith("SMTP OK but IMAP"):
            raise MailboxCheckError("Sending-server sign-in works, but inbox sign-in failed. Check the IMAP server, port and password.")
        raise MailboxCheckError("Sending-server sign-in failed. Check the SMTP server, port and password, then try again.")
    email = ""
    if ok and provider == "gmail":
        profile = await mailbox._request("GET", "/profile")
        email = (profile or {}).get("emailAddress", "")
        inbox = await mailbox._request("GET", "/messages", params={"maxResults": 1, "labelIds": "INBOX"})
        ok = inbox is not None
    elif ok and provider == "microsoft":
        email = await mailbox._my_address()
    elif ok:
        email = mailbox.smtp_user
    return ok, email


@router.post("/{provider}/test")
async def test(provider: str):
    if provider not in PROVIDERS:
        return JSONResponse({"message": "Unknown mailbox provider."}, status_code=404)
    if not configured(provider):
        return {"success": False, "message": "Add the required connection details first."}
    if _test_locks[provider].locked():
        return JSONResponse({"success": False, "message": "A connection check is already running."}, status_code=409)
    async with _test_locks[provider]:
        before = fingerprint(provider)
        detail = ""
        try:
            ok, email = await asyncio.wait_for(probe(provider), timeout=40)
        except MailboxCheckError as error:
            ok, email, detail = False, "", str(error)
        except Exception:
            ok, email = False, ""
        message = ("Sending account and inbox access verified. No email was sent." if provider != "instantly" else "Instantly API access verified.") if ok else (
            "Connection failed. Check SMTP/IMAP server settings and password; confirm both services are enabled." if provider == "smtp" else
            "Connection failed. Reconnect your account and approve the requested permissions." if provider != "instantly" else "Instantly rejected the connection. Check the key and API access on your plan.")
        if detail:
            message = detail
        if before != fingerprint(provider):
            return {"success": False, "message": "Connection details changed during the check. Test again."}
        async with _lock:
            checks = health()
            checks[provider] = {"ok": ok, "email": email, "message": message, "checked_at": int(time.time()), "fingerprint": before}
            private_json(dashboard().token_store.path.parent / "mailbox-health.json", checks)
        return {"success": ok, "message": message, "email": email}


@router.post("/{provider}/activate")
async def activate(provider: str):
    if provider not in PROVIDERS:
        return JSONResponse({"message": "Unknown mailbox provider."}, status_code=404)
    check = health().get(provider, {})
    if not check.get("ok") or check.get("fingerprint") != fingerprint(provider) or time.time() - check.get("checked_at", 0) >= 86400:
        return JSONResponse({"success": False, "message": "Test this connection successfully before using it."}, status_code=409)
    async with dashboard()._env_lock:
        if check.get("fingerprint") != fingerprint(provider):
            return JSONResponse({"success": False, "message": "Connection details changed. Test again before activating."}, status_code=409)
        path = dashboard()._resolve_config_file()
        try:
            if path.is_symlink():
                raise ValueError()
            config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            config.setdefault("channels", {}).setdefault("email", {})["provider"] = provider
            if check.get("email") and "@" in check["email"]:
                config.setdefault("persona", {})["email"] = check["email"]
            fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".mailbox-config-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    yaml.safe_dump(config, stream, sort_keys=False, allow_unicode=True)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
            return JSONResponse({"success": False, "message": "Could not update the active mailbox configuration."}, status_code=400)
    return {"success": True, "message": "Active mailbox updated. Restart automation to apply. Sending limits and approval settings are preserved."}


class SendTest(BaseModel):
    confirm: bool = False


@router.post("/{provider}/send-test")
async def send_test(provider: str, body: SendTest):
    if provider not in ("gmail", "microsoft", "smtp") or not body.confirm:
        return JSONResponse({"success": False, "message": "Confirm sending a test email to your connected mailbox."}, status_code=400)
    check = health().get(provider, {})
    if not check.get("ok") or check.get("fingerprint") != fingerprint(provider) or time.time() - check.get("checked_at", 0) >= 86400 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", check.get("email", "")):
        return JSONResponse({"success": False, "message": "Verify a mailbox with an email address first."}, status_code=409)
    if _test_locks[provider].locked():
        return JSONResponse({"success": False, "message": "A mailbox action is already running."}, status_code=409)
    async with _test_locks[provider]:
        mailbox = adapter(provider)
        mailbox.config.persona.email = check["email"]
        try:
            result = await asyncio.wait_for(mailbox.send_email(check["email"], "Zyvello mailbox connection test", "Your Zyvello sending connection works. This test was requested from Mailbox Connections. No campaign has been started."), timeout=40)
            ok = result.ok
        except Exception:
            ok = False
        return {"success": ok, "message": "Test accepted by your mail provider. Check your inbox and spam folder." if ok else "The test could not be confirmed. Check your mailbox before retrying; delivery may still occur."}


@router.post("/oauth/{provider}/start")
async def oauth_start(provider: str, request: Request):
    if provider not in ("gmail", "microsoft") or not configured(provider):
        return JSONResponse({"success": False, "message": "This sign-in is not configured yet. Open administrator setup to add the app credentials."}, status_code=409)
    current = values()
    tenant = current.get("M365_TENANT_ID") or "organizations"
    if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", tenant):
        return JSONResponse({"message": "Invalid Microsoft tenant configuration."}, status_code=400)
    from harvey.integrations.gmail import AUTH_URL, SCOPE as GMAIL_SCOPE
    from harvey.integrations.microsoft import LOGIN, SCOPE as MICROSOFT_SCOPE, _pkce_pair
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()
    uri = redirect_uri(request, provider)
    async with _lock:
        for key in list(PENDING):
            if PENDING[key]["expires"] < time.time():
                PENDING.pop(key)
        if len(PENDING) >= 64:
            return JSONResponse({"message": "Too many sign-in attempts. Try again shortly."}, status_code=429)
        PENDING[state] = {"provider": provider, "nonce": nonce, "verifier": verifier, "uri": uri,
                          "owner_session": request.cookies.get("zyvello_session", ""),
                          "expires": time.time() + 600, "fingerprint": fingerprint(provider)}
    params = {"client_id": current["GMAIL_CLIENT_ID" if provider == "gmail" else "M365_CLIENT_ID"],
              "redirect_uri": uri, "response_type": "code", "state": state,
              "scope": GMAIL_SCOPE if provider == "gmail" else MICROSOFT_SCOPE,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    if provider == "gmail":
        params.update(access_type="offline", prompt="consent")
    else:
        params.update(response_mode="query", prompt="select_account")
    url = (AUTH_URL if provider == "gmail" else f"{LOGIN}/{tenant}/oauth2/v2.0/authorize") + "?" + urlencode(params)
    response = JSONResponse({"success": True, "url": url})
    response.set_cookie("zyvello_oauth_" + provider, nonce, max_age=600, httponly=True, samesite="lax", secure=request.url.scheme == "https", path=f"/api/mailboxes/oauth/{provider}")
    return response


async def exchange(provider, code, pending):
    from harvey.integrations.gmail import TOKEN_URL, SCOPE as GMAIL_SCOPE
    from harvey.integrations.microsoft import _token_url, SCOPE as MICROSOFT_SCOPE
    current = values()
    tenant = current.get("M365_TENANT_ID") or "organizations"
    payload = {"client_id": current["GMAIL_CLIENT_ID" if provider == "gmail" else "M365_CLIENT_ID"],
               "grant_type": "authorization_code", "code": code, "redirect_uri": pending["uri"], "code_verifier": pending["verifier"]}
    if provider == "gmail":
        payload["client_secret"] = current["GMAIL_CLIENT_SECRET"]
    else:
        payload["scope"] = MICROSOFT_SCOPE
        if current.get("M365_CLIENT_SECRET"):
            payload["client_secret"] = current["M365_CLIENT_SECRET"]
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        result = await client.post(TOKEN_URL if provider == "gmail" else _token_url(tenant), data=payload)
    if result.status_code != 200:
        raise ValueError("Token exchange failed")
    token = result.json()
    if not isinstance(token.get("refresh_token"), str) or not token["refresh_token"]:
        raise ValueError("No offline access")
    scopes = str(token.get("scope") or "").lower()
    required = {"gmail.modify"} if provider == "gmail" else {"mail.readwrite", "mail.send"}
    scope_names = {scope.rsplit("/", 1)[-1] for scope in scopes.split()}
    if scopes and not required.issubset(scope_names):
        raise ValueError("Missing mailbox permissions")
    return {"refresh_token": token["refresh_token"], "scope": token.get("scope", GMAIL_SCOPE if provider == "gmail" else MICROSOFT_SCOPE),
            "tenant": tenant if provider == "microsoft" else "", "obtained_at": time.time_ns()}


@router.get("/oauth/{provider}/callback")
async def oauth_callback(provider: str, request: Request):
    state = request.query_params.get("state", "")
    async with _lock:
        pending = PENDING.get(state)
        cookie = request.cookies.get("zyvello_oauth_" + provider, "")
        if not pending or pending["provider"] != provider or pending["expires"] < time.time() or not hmac.compare_digest(cookie.encode(), pending["nonce"].encode()):
            return JSONResponse({"message": "Sign-in expired or did not start in this browser. Return to Mailbox Connections and try again."}, status_code=400)
        PENDING.pop(state)
    result = "cancelled" if request.query_params.get("error") else "failed"
    accounts = dashboard()._accounts()
    owner_valid = not await asyncio.to_thread(accounts.has_owner) or bool(await asyncio.to_thread(accounts.user, pending["owner_session"]))
    if owner_valid and not request.query_params.get("error") and request.query_params.get("code") and pending["fingerprint"] == fingerprint(provider):
        try:
            token = await exchange(provider, request.query_params["code"], pending)
            if pending["fingerprint"] != fingerprint(provider):
                raise ValueError()
            private_json(token_path(provider), token)
            result = "authorized"
        except Exception:
            result = "failed"
    destination = "/?" + urlencode({"provider": provider, "result": result}) + "#settings"
    # A fresh same-origin navigation restores the Strict owner cookie after
    # returning from Google/Microsoft. A cross-site 303 chain may omit it.
    response = HTMLResponse('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Returning to Zyvello</title><body style="background:#0a0e0e;color:#dce8d2;font:16px system-ui;padding:48px"><p>Returning to your mailbox connections…</p><a style="color:#c4e8a6" href="' + html.escape(destination, quote=True) + '">Continue to Zyvello</a><script>window.location.replace(' + json.dumps(destination) + ');</script></body></html>')
    response.delete_cookie("zyvello_oauth_" + provider, path=f"/api/mailboxes/oauth/{provider}")
    return response
