"""Isolated hosted staging API for verified Zyvello users.

This module must never import the local owner-authenticated dashboard or the
single-tenant SQLite state manager. Neither is safe to expose as a hosted SaaS.
"""

import asyncio
import hashlib
import os
import re
from pathlib import Path
from typing import Callable, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from ipaddress import ip_address
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .auth import IdentityProviderUnavailable, NeonTokenVerifier, TokenRejected
from .credentials import CredentialConfigurationError, CredentialDecryptionError, CredentialVault
from .gmail_oauth import GmailAuthorizationError, GmailOAuth
from .billing_store import BillingApiStore, BillingWebhookStore
from .billing_catalog import APPROVED_MONTHLY_TIERS, FUTURE_AI_EMPLOYEE, approved_monthly_plan, public_monthly_catalog
from .razorpay_billing import (MAX_WEBHOOK_BODY_BYTES, BillingRejected, BillingSettings,
                               BillingUnavailable, RazorpayGateway, parse_verified_webhook,
                               verify_checkout_signature, verify_webhook_signature)
from .repository import HostedStore, StoreConflict, StoreForbidden, StoreLimit, StoreNotFound, StoreUnavailable, serialize_row
from .session_guard import NeonSessionGuard


class CreateWorkspaceRequest(BaseModel):
    slug: str = Field(min_length=3, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
    name: str = Field(min_length=1, max_length=200)


class WorkspaceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    industry: str = Field(default="", max_length=100)
    website_url: str = Field(default="", max_length=300, pattern=r"^$|^https://[^\s/]+(?:/[^\s]*)?$")
    timezone: str = Field(default="Asia/Kolkata", min_length=3, max_length=64)

    @field_validator("website_url")
    @classmethod
    def public_website_only(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.query or parsed.fragment or not host
                or host == "localhost" or host.endswith(".localhost")
                or not host.isascii()):
            raise ValueError("Use a public HTTPS business website without credentials")
        try:
            if parsed.port not in (None, 443):
                raise ValueError("Custom ports are not permitted")
        except ValueError:
            raise ValueError("Invalid website port") from None
        try:
            address = ip_address(host)
        except ValueError:
            if "." not in host or host.startswith(".") or host.endswith("."):
                raise ValueError("A public domain is required") from None
        else:
            if not address.is_global:
                raise ValueError("Private network addresses are not permitted")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Use an IANA timezone such as Asia/Kolkata") from None
        return value


class UpdateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    settings: WorkspaceSettings | None = None


class WorkspaceInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    role: Literal["admin", "member"] = "member"


class AcceptWorkspaceInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=32, max_length=128)


class UpdateMemberRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["admin", "member"]


class EnqueueJobRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)


class CreateContactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    company: str = Field(default="", max_length=160)


class CreateCampaignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    goal: str = Field(default="", max_length=1000)


class AddCampaignContactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contact_id: UUID


class CreateSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_code: str = Field(min_length=2, max_length=40, pattern=r"^[a-z][a-z0-9_-]{1,39}$")
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]{8,100}$")


class VerifySubscriptionCheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    razorpay_payment_id: str = Field(pattern=r"^pay_[A-Za-z0-9]{8,40}$")
    razorpay_signature: str = Field(min_length=64, max_length=64)


class PutGroqCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr = Field(min_length=36, max_length=256)


class CompleteGmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: SecretStr = Field(min_length=1, max_length=2048)
    state: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class SendCampaignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subject: str = Field(min_length=1, max_length=160, pattern=r"^[^\x00-\x1f\x7f]+$")
    body: str = Field(min_length=1, max_length=5000)


class ContactPermissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern=r"^(claimed_consent|opted_out|suppressed)$")
    source: str = Field(min_length=3, max_length=100)
    evidence: str = Field(default="", max_length=1000)


class CreateGroqDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    campaign_id: UUID
    contact_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


def create_app(
    *, auth_base_url: str | None = None,
    static_dir: str | None = None,
    database_url: str | None = None,
    dispatch_job: Callable[[], None] | None = None,
    store_override: HostedStore | None = None,
    billing_database_url: str | None = None,
    billing_settings_override: BillingSettings | None = None,
    billing_api_override: BillingApiStore | None = None,
    billing_webhook_override: BillingWebhookStore | None = None,
    billing_gateway_override: RazorpayGateway | None = None,
    live_billing_settings_override: BillingSettings | None = None,
    test_checkout_workspaces: frozenset[UUID] | None = None,
    live_billing_gateway_override: RazorpayGateway | None = None,
    credential_vault_override: CredentialVault | None = None,
    gmail_oauth_override: GmailOAuth | None = None,
    campaign_pilot_recipient_override: str | None = None,
    campaign_pilot_workspace_override: UUID | None = None,
    campaign_reply_sync_override: bool | None = None,
    enable_ai_drafts_override: bool | None = None,
    allow_workspace_creation_override: bool | None = None,
    subscription_enforcement_override: bool | None = None,
    session_guard_override: Callable | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Zyvello staging workspace API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    verifier = NeonTokenVerifier(auth_base_url) if auth_base_url else None
    store = store_override or (HostedStore(database_url) if database_url else None)
    session_guard = (session_guard_override if session_guard_override is not None
                     else NeonSessionGuard(database_url) if database_url else None)
    billing_api = billing_api_override or (BillingApiStore(database_url) if database_url else None)
    billing_webhooks = billing_webhook_override or (BillingWebhookStore(billing_database_url) if billing_database_url else None)
    billing_settings = billing_settings_override or BillingSettings.from_env()
    billing_gateway = billing_gateway_override or (RazorpayGateway(billing_settings) if billing_settings.enabled else None)
    live_billing = live_billing_settings_override or BillingSettings.live_pilot_from_env()
    live_gateway = live_billing_gateway_override or (RazorpayGateway(live_billing) if live_billing.enabled else None)
    credential_vault = credential_vault_override
    if credential_vault is None and os.getenv("ZYVELLO_CREDENTIAL_ENCRYPTION_KEYS"):
        credential_vault = CredentialVault(os.environ["ZYVELLO_CREDENTIAL_ENCRYPTION_KEYS"])
    gmail_oauth = gmail_oauth_override or GmailOAuth.from_env()
    gmail_ready = gmail_oauth is not None and credential_vault is not None
    pilot_recipient = (campaign_pilot_recipient_override if campaign_pilot_recipient_override is not None
                       else os.getenv("ZYVELLO_CAMPAIGN_PILOT_RECIPIENT", "")).strip().lower()
    try:
        pilot_workspace = campaign_pilot_workspace_override or UUID(os.getenv("ZYVELLO_CAMPAIGN_PILOT_WORKSPACE", ""))
    except ValueError:
        pilot_workspace = None
    campaign_pilot = gmail_ready and pilot_workspace is not None and len(pilot_recipient) <= 254 and bool(
        re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}", pilot_recipient)
    )
    reply_sync = campaign_pilot and (campaign_reply_sync_override if campaign_reply_sync_override is not None
                                     else os.getenv("ZYVELLO_ENABLE_GMAIL_REPLY_SYNC", "").lower() == "true")
    ai_drafts_enabled = (enable_ai_drafts_override if enable_ai_drafts_override is not None
                         else os.getenv("ZYVELLO_ENABLE_AI_DRAFTS", "false").lower() == "true") and bool(credential_vault)
    # Public onboarding is enabled only on deployments with paid-feature checks.
    allow_workspace_creation = (allow_workspace_creation_override if allow_workspace_creation_override is not None
                                else os.getenv("ZYVELLO_ENABLE_NEW_WORKSPACES", "false").lower() == "true")
    # Never allow an environment variable to accidentally disable payment
    # enforcement in staging or production. Test doubles opt out explicitly.
    enforce_paid_features = (subscription_enforcement_override
                             if subscription_enforcement_override is not None else True)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://zyvello--zyvello-staging-web.us-east.modal.run",
            "https://peachpuff-curlew-471332.hostingersite.com",
            "https://zyvello.pro",
        ],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )

    @app.exception_handler(RequestValidationError)
    async def redact_credential_validation(request: Request, exc: RequestValidationError):
        if request.method == "POST" and request.url.path.endswith("/gmail/complete"):
            return JSONResponse(status_code=422, content={"detail": "Invalid Gmail authorization response"})
        if request.method == "PUT" and request.url.path.endswith("/credentials/groq"):
            return JSONResponse(status_code=422, content={"detail": "Invalid Groq API key"})
        return await request_validation_exception_handler(request, exc)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    async def verified_identity(authorization: str | None = Header(default=None)):
        if verifier is None:
            raise HTTPException(503, "Identity verification is not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "A bearer token is required", headers={"WWW-Authenticate": "Bearer"})
        try:
            identity = await asyncio.to_thread(verifier.verify, authorization[7:])
            if session_guard is not None:
                if not await asyncio.to_thread(session_guard, identity):
                    raise TokenRejected
            return identity
        except TokenRejected:
            raise HTTPException(401, "Invalid, expired or revoked identity", headers={"WWW-Authenticate": "Bearer"}) from None
        except (IdentityProviderUnavailable, StoreUnavailable):
            raise HTTPException(503, "Identity verification is temporarily unavailable") from None

    def ready_store() -> HostedStore:
        if store is None:
            raise HTTPException(503, "The workspace database is not configured")
        return store

    async def store_call(callback, *args, **kwargs):
        try:
            return await asyncio.to_thread(callback, *args, **kwargs)
        except StoreNotFound as error:
            raise HTTPException(404, str(error)) from None
        except StoreConflict as error:
            raise HTTPException(409, str(error)) from None
        except StoreForbidden as error:
            raise HTTPException(403, str(error)) from None
        except StoreLimit as error:
            raise HTTPException(429, str(error), headers={"Retry-After": "30"}) from None
        except StoreUnavailable:
            raise HTTPException(503, "Workspace database is temporarily unavailable") from None
        except ValueError:
            raise HTTPException(422, "Invalid workspace request") from None

    def ready_billing(workspace_id: UUID) -> tuple[BillingApiStore, RazorpayGateway, BillingSettings]:
        settings, gateway = (
            (live_billing, live_gateway)
            if live_billing.enabled and workspace_id == live_billing.workspace_id
            else (billing_settings, billing_gateway)
        )
        if not settings.enabled or billing_api is None or gateway is None or billing_webhooks is None:
            raise HTTPException(503, "Workspace billing is not configured")
        return billing_api, gateway, settings

    def test_checkout_allowed(workspace_id: UUID) -> bool:
        return test_checkout_workspaces is None or workspace_id in test_checkout_workspaces

    def require_billing_mode(sub: dict, settings: BillingSettings) -> None:
        if (sub["provider_mode"] != settings.mode
            or (settings.mode == "live" and sub["provider_plan_id"] != settings.plans["starter"].razorpay_plan_id)):
            raise HTTPException(409, "Subscription belongs to a different billing configuration")

    async def require_paid_workspace(workspace_id: UUID, identity=Depends(verified_identity),
                                     db: HostedStore = Depends(ready_store)) -> None:
        if not enforce_paid_features:
            return
        # Membership is checked first, so a foreign tenant's billing state
        # cannot be probed. Fail closed on missing/unpaid/expired entitlements.
        entitled = await store_call(db.has_paid_entitlement, identity.user_id, workspace_id)
        if not entitled:
            raise HTTPException(402, "An active paid subscription is required for this feature")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"service": "zyvello-staging", "status": "alive"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reason": "Full launch requires live payments and public outreach verification",
            },
        )

    @app.get("/api/v1/me")
    async def authenticated_identity(identity=Depends(verified_identity)) -> dict[str, str]:
        """Identity claims only; authorization happens again for each query."""
        return {"user_id": identity.user_id, "email": identity.email, "status": "identity_verified"}

    @app.get("/api/v1/workspaces")
    async def list_workspaces(identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        rows = await store_call(db.list_workspaces, identity.user_id)
        return {"workspaces": [serialize_row(row) for row in rows]}

    @app.post("/api/v1/workspaces", status_code=201)
    async def create_workspace(body: CreateWorkspaceRequest, identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        if not allow_workspace_creation:
            raise HTTPException(403, "Workspace onboarding is not available on this deployment")
        row = await store_call(db.create_workspace, identity.user_id, name=body.name, slug=body.slug)
        return {"workspace": serialize_row(row)}

    @app.get("/api/v1/workspaces/{workspace_id}")
    async def get_workspace(workspace_id: UUID, identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        row = await store_call(db.get_workspace, identity.user_id, workspace_id)
        return {"workspace": serialize_row(row)}

    @app.post("/api/v1/workspaces/bootstrap")
    async def bootstrap_workspace(identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        if not allow_workspace_creation:
            raise HTTPException(403, "Workspace onboarding is not available on this deployment")
        result = await store_call(db.bootstrap_workspace, identity.user_id)
        return {"workspace": serialize_row(result["workspace"]), "created": result["created"]}

    @app.delete("/api/v1/workspaces/{workspace_id}")
    async def delete_empty_workspace(workspace_id: UUID, identity=Depends(verified_identity),
                                     db: HostedStore = Depends(ready_store)):
        return await store_call(db.delete_empty_workspace, identity.user_id, workspace_id)

    @app.patch("/api/v1/workspaces/{workspace_id}")
    async def update_workspace(workspace_id: UUID, body: UpdateWorkspaceRequest,
                               identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        settings = body.settings.model_dump(exclude_unset=True) if body.settings is not None else None
        updated = await store_call(db.update_workspace, identity.user_id, workspace_id,
                                   name=body.name, settings=settings)
        return {"workspace": serialize_row(updated)}

    @app.get("/api/v1/workspaces/{workspace_id}/members")
    async def list_workspace_members(workspace_id: UUID, identity=Depends(verified_identity),
                                     db: HostedStore = Depends(ready_store)):
        rows = await store_call(db.list_workspace_members, identity.user_id, workspace_id)
        return {"members": [serialize_row(row) for row in rows]}

    @app.patch("/api/v1/workspaces/{workspace_id}/members/{member_id}")
    async def change_member_role(workspace_id: UUID, member_id: UUID, body: UpdateMemberRoleRequest,
                                 identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        row = await store_call(db.set_member_role, identity.user_id, workspace_id, str(member_id),
                               role=body.role)
        return {"member": serialize_row(row)}

    @app.delete("/api/v1/workspaces/{workspace_id}/members/{member_id}")
    async def remove_workspace_member(workspace_id: UUID, member_id: UUID,
                                      identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        result = await store_call(db.remove_workspace_member, identity.user_id, workspace_id, str(member_id))
        return result

    @app.get("/api/v1/workspaces/{workspace_id}/invitations")
    async def list_workspace_invitations(workspace_id: UUID, identity=Depends(verified_identity),
                                         db: HostedStore = Depends(ready_store)):
        rows = await store_call(db.list_workspace_invitations, identity.user_id, workspace_id)
        return {"invitations": [serialize_row(row) for row in rows]}

    @app.post("/api/v1/workspaces/{workspace_id}/invitations", status_code=201)
    async def create_workspace_invitation(workspace_id: UUID, body: WorkspaceInvitationRequest,
                                          identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        result = await store_call(db.create_workspace_invitation, identity.user_id, workspace_id,
                                  email=body.email, role=body.role)
        return {"invitation": serialize_row(result["invitation"]), "token": result["token"]}

    @app.post("/api/v1/workspace-invitations/accept")
    async def accept_workspace_invitation(body: AcceptWorkspaceInvitationRequest,
                                          identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        row = await store_call(db.accept_workspace_invitation, identity.user_id, body.token)
        return {"workspace": serialize_row(row)}

    @app.post("/api/v1/workspaces/{workspace_id}/invitations/{invitation_id}/revoke")
    async def revoke_workspace_invitation(workspace_id: UUID, invitation_id: UUID,
                                          identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        row = await store_call(db.revoke_workspace_invitation, identity.user_id, workspace_id, invitation_id)
        return {"invitation": serialize_row(row)}

    @app.get("/api/v1/workspaces/{workspace_id}/entitlement")
    async def get_workspace_entitlement(workspace_id: UUID, identity=Depends(verified_identity),
                                        db: HostedStore = Depends(ready_store)):
        entitled = await store_call(db.has_paid_entitlement, identity.user_id, workspace_id)
        return {"entitled": bool(entitled), "paid_features_enforced": enforce_paid_features,
                "checkout_available": (
                    bool(live_billing.checkout_enabled)
                    if live_billing.enabled and workspace_id == live_billing.workspace_id
                    else bool(billing_settings.enabled and test_checkout_allowed(workspace_id))
                ),
                "campaign_delivery_available": campaign_pilot and workspace_id == pilot_workspace}

    @app.get("/api/v1/workspaces/{workspace_id}/jobs", dependencies=[Depends(require_paid_workspace)])
    async def list_jobs(workspace_id: UUID, identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        rows = await store_call(db.list_jobs, identity.user_id, workspace_id)
        return {"jobs": [serialize_row(row) for row in rows]}

    @app.get("/api/v1/workspaces/{workspace_id}/contacts", dependencies=[Depends(require_paid_workspace)])
    async def list_contacts(workspace_id: UUID, identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        rows = await store_call(db.list_contacts, identity.user_id, workspace_id)
        return {"contacts": [serialize_row(row) for row in rows]}

    @app.get("/api/v1/workspaces/{workspace_id}/companies", dependencies=[Depends(require_paid_workspace)])
    async def list_companies(workspace_id: UUID, identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        rows = await store_call(db.list_companies, identity.user_id, workspace_id)
        return {"companies": [serialize_row(row) for row in rows]}

    @app.post("/api/v1/workspaces/{workspace_id}/contacts", status_code=201, dependencies=[Depends(require_paid_workspace)])
    async def create_contact(workspace_id: UUID, body: CreateContactRequest, identity=Depends(verified_identity),
                             db: HostedStore = Depends(ready_store)):
        row = await store_call(db.create_contact, identity.user_id, workspace_id,
                               full_name=body.full_name, email=body.email, company=body.company)
        return {"contact": serialize_row(row)}

    @app.get("/api/v1/workspaces/{workspace_id}/contacts/{contact_id}/permission", dependencies=[Depends(require_paid_workspace)])
    async def get_contact_permission(workspace_id: UUID, contact_id: UUID,
                                     identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        row = await store_call(db.get_contact_permission, identity.user_id, workspace_id, contact_id)
        return {"permission": serialize_row(row), "outbound_allowed": False}

    @app.post("/api/v1/workspaces/{workspace_id}/contacts/{contact_id}/permission", status_code=201, dependencies=[Depends(require_paid_workspace)])
    async def record_contact_permission(workspace_id: UUID, contact_id: UUID, body: ContactPermissionRequest,
                                        identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        row = await store_call(db.record_contact_permission, identity.user_id, workspace_id, contact_id,
                               action=body.action, source=body.source, evidence=body.evidence)
        return {"permission": serialize_row(row), "outbound_allowed": False}

    @app.get("/api/v1/workspaces/{workspace_id}/gmail")
    async def gmail_status(workspace_id: UUID, identity=Depends(verified_identity),
                           db: HostedStore = Depends(ready_store)):
        if not gmail_ready:
            workspace = await store_call(db.get_workspace, identity.user_id, workspace_id)
            if workspace["role"] not in ("owner", "admin"):
                raise HTTPException(403, "Only a workspace manager can manage the mailbox")
            return {"connection_available": False, "email": None, "connected_at": None}
        mailbox = await store_call(db.gmail_status, identity.user_id, workspace_id)
        return {"connection_available": True, **serialize_row(mailbox)}

    @app.post("/api/v1/workspaces/{workspace_id}/gmail/begin", dependencies=[Depends(require_paid_workspace)])
    async def begin_gmail(workspace_id: UUID, identity=Depends(verified_identity),
                          db: HostedStore = Depends(ready_store)):
        if not gmail_oauth or not credential_vault:
            raise HTTPException(503, "Gmail connection is not configured")
        state, verifier, authorization_url = gmail_oauth.begin(reply_metadata=reply_sync and workspace_id == pilot_workspace)
        await store_call(db.begin_gmail_connection, identity.user_id, workspace_id,
                         state_hash=hashlib.sha256(state.encode()).hexdigest(),
                         verifier_ciphertext=credential_vault.seal_gmail(workspace_id, "verifier", verifier))
        return {"authorization_url": authorization_url, "state": state}

    @app.post("/api/v1/workspaces/{workspace_id}/gmail/complete", dependencies=[Depends(require_paid_workspace)])
    async def complete_gmail(workspace_id: UUID, body: CompleteGmailRequest, identity=Depends(verified_identity),
                             db: HostedStore = Depends(ready_store)):
        if not gmail_oauth or not credential_vault:
            raise HTTPException(503, "Gmail connection is not configured")
        state_hash = hashlib.sha256(body.state.encode()).hexdigest()
        encrypted = await store_call(db.claim_gmail_connection, identity.user_id, workspace_id, state_hash=state_hash)
        try:
            verifier = credential_vault.open_gmail(workspace_id, "verifier", encrypted)
            email, refresh_token = await asyncio.to_thread(
                gmail_oauth.exchange, code=body.code.get_secret_value(), verifier=verifier,
            )
        except CredentialDecryptionError:
            raise HTTPException(409, "The Gmail connection could not be completed. Connect Gmail again.") from None
        except GmailAuthorizationError as error:
            raise HTTPException(400, str(error)) from None
        mailbox = await store_call(
            db.finish_gmail_connection, identity.user_id, workspace_id, state_hash=state_hash, email=email,
            refresh_ciphertext=credential_vault.seal_gmail(workspace_id, "refresh", refresh_token),
        )
        return {"connection_available": True, **serialize_row(mailbox)}

    @app.post("/api/v1/workspaces/{workspace_id}/gmail/test", dependencies=[Depends(require_paid_workspace)])
    async def gmail_self_test(workspace_id: UUID, identity=Depends(verified_identity),
                              db: HostedStore = Depends(ready_store)):
        if not gmail_oauth or not credential_vault:
            raise HTTPException(503, "Gmail connection is not configured")
        mailbox = await store_call(db.reserve_gmail_self_test, identity.user_id, workspace_id)
        try:
            refresh_token = credential_vault.open_gmail(workspace_id, "refresh", mailbox["refresh_ciphertext"])
            await asyncio.to_thread(gmail_oauth.send_self_test, email=mailbox["email"], refresh_token=refresh_token)
        except CredentialDecryptionError:
            raise HTTPException(409, "Reconnect Gmail to send a test message.") from None
        except GmailAuthorizationError as error:
            raise HTTPException(502, str(error)) from None
        return {"accepted": True}

    @app.delete("/api/v1/workspaces/{workspace_id}/gmail")
    async def disconnect_gmail(workspace_id: UUID, identity=Depends(verified_identity),
                               db: HostedStore = Depends(ready_store)):
        if not gmail_ready:
            raise HTTPException(503, "Gmail connection is not configured")
        await store_call(db.disconnect_gmail, identity.user_id, workspace_id)
        return {"disconnected": True}

    @app.get("/api/v1/workspaces/{workspace_id}/campaign-deliveries", dependencies=[Depends(require_paid_workspace)])
    async def campaign_delivery_list(workspace_id: UUID, identity=Depends(verified_identity),
                                     db: HostedStore = Depends(ready_store)):
        rows = await store_call(db.campaign_deliveries, identity.user_id, workspace_id)
        return {"deliveries": [serialize_row(row) for row in rows],
                "pilot_enabled": campaign_pilot and workspace_id == pilot_workspace,
                "pilot_recipient": pilot_recipient if campaign_pilot and workspace_id == pilot_workspace else None,
                "reply_sync_enabled": reply_sync and workspace_id == pilot_workspace}

    @app.post("/api/v1/workspaces/{workspace_id}/drafts/{draft_id}/send", dependencies=[Depends(require_paid_workspace)])
    async def send_campaign_draft(workspace_id: UUID, draft_id: UUID, body: SendCampaignRequest,
                                  identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        if not campaign_pilot or workspace_id != pilot_workspace or not gmail_oauth or not credential_vault:
            raise HTTPException(503, "Campaign sending is not enabled on this deployment")
        attempt = await store_call(db.reserve_campaign_delivery, identity.user_id, workspace_id,
                                   draft_id=draft_id, pilot_recipient=pilot_recipient, subject=body.subject)
        delivery_id = attempt["delivery_id"]
        message_id, thread_id = None, None
        try:
            token = credential_vault.open_gmail(workspace_id, "refresh", attempt["refresh_ciphertext"])
            message_id, thread_id = await asyncio.to_thread(
                gmail_oauth.send_message, sender=attempt["sender"], recipient=attempt["recipient"],
                refresh_token=token, subject=body.subject, body=body.body,
                message_id=f"<{delivery_id}@zyvello.pro>",
            )
        except (CredentialDecryptionError, GmailAuthorizationError):
            # A timeout after Gmail received the request can look like a failure.
            # Keep the reservation so repeated requests never send another copy.
            pass
        await store_call(db.finish_campaign_delivery, identity.user_id, workspace_id,
                         delivery_id=delivery_id, message_id=message_id, thread_id=thread_id)
        return {"delivery_id": str(delivery_id), "status": "accepted" if message_id else "unconfirmed"}

    @app.post("/api/v1/workspaces/{workspace_id}/campaign-deliveries/{delivery_id}/sync",
              dependencies=[Depends(require_paid_workspace)])
    async def sync_campaign_reply(workspace_id: UUID, delivery_id: UUID, identity=Depends(verified_identity),
                                  db: HostedStore = Depends(ready_store)):
        if not reply_sync or workspace_id != pilot_workspace or not gmail_oauth or not credential_vault:
            raise HTTPException(503, "Gmail reply sync is not enabled on this deployment")
        delivery = await store_call(db.reserve_campaign_reply_sync, identity.user_id, workspace_id, delivery_id)
        try:
            token = credential_vault.open_gmail(workspace_id, "refresh", delivery["refresh_ciphertext"])
            received_at = await asyncio.to_thread(
                gmail_oauth.newest_reply, refresh_token=token, thread_id=delivery["gmail_thread_id"],
                sent_id=delivery["gmail_message_id"], recipient=delivery["recipient"], sent_after=delivery["created_at"],
            )
        except CredentialDecryptionError:
            raise HTTPException(409, "Reconnect Gmail to check replies.") from None
        except GmailAuthorizationError as error:
            raise HTTPException(502, str(error)) from None
        if received_at:
            await store_call(db.record_campaign_reply, identity.user_id, workspace_id,
                             delivery_id=delivery_id, received_at=received_at)
        return {"reply_found": received_at is not None}

    @app.get("/api/v1/workspaces/{workspace_id}/credentials/groq", dependencies=[Depends(require_paid_workspace)])
    async def groq_credential_status(workspace_id: UUID, identity=Depends(verified_identity),
                                     db: HostedStore = Depends(ready_store)):
        row = await store_call(db.get_groq_credential_status, identity.user_id, workspace_id)
        return {"provider": "groq", **serialize_row(row), "ai_execution_enabled": ai_drafts_enabled,
                "credential_storage_ready": credential_vault is not None}

    @app.put("/api/v1/workspaces/{workspace_id}/credentials/groq", dependencies=[Depends(require_paid_workspace)])
    async def save_groq_credential(workspace_id: UUID, body: PutGroqCredentialRequest,
                                   identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        if credential_vault is None:
            raise HTTPException(503, "Credential encryption is not configured")
        try:
            ciphertext = credential_vault.seal(body.api_key.get_secret_value())
        except (CredentialConfigurationError, ValueError):
            raise HTTPException(422, "Invalid provider credential") from None
        row = await store_call(db.put_groq_credential, identity.user_id, workspace_id, ciphertext=ciphertext)
        return {"provider": "groq", **serialize_row(row), "ai_execution_enabled": ai_drafts_enabled,
                "credential_storage_ready": credential_vault is not None}

    @app.delete("/api/v1/workspaces/{workspace_id}/credentials/groq", dependencies=[Depends(require_paid_workspace)])
    async def remove_groq_credential(workspace_id: UUID, identity=Depends(verified_identity),
                                     db: HostedStore = Depends(ready_store)):
        row = await store_call(db.delete_groq_credential, identity.user_id, workspace_id)
        return {"provider": "groq", **row, "ai_execution_enabled": ai_drafts_enabled}

    @app.get("/api/v1/workspaces/{workspace_id}/drafts", dependencies=[Depends(require_paid_workspace)])
    async def list_generated_drafts(workspace_id: UUID, identity=Depends(verified_identity),
                                    db: HostedStore = Depends(ready_store)):
        rows = await store_call(db.list_generated_drafts, identity.user_id, workspace_id)
        return {"drafts": [serialize_row(row) for row in rows], "outbound_sending_enabled": False}

    @app.post("/api/v1/workspaces/{workspace_id}/drafts", status_code=202, dependencies=[Depends(require_paid_workspace)])
    async def enqueue_groq_draft(workspace_id: UUID, body: CreateGroqDraftRequest,
                                 identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        if not ai_drafts_enabled:
            raise HTTPException(503, "Hosted AI drafts are not enabled")
        job = await store_call(db.enqueue_groq_draft, identity.user_id, workspace_id,
                               campaign_id=body.campaign_id, contact_id=body.contact_id,
                               idempotency_key=body.idempotency_key)
        dispatched = False
        if job["created"] and dispatch_job is not None:
            try:
                await asyncio.to_thread(dispatch_job)
                dispatched = True
            except Exception:
                pass
        return {"job": serialize_row(job), "dispatch_requested": dispatched, "sent": False}

    @app.get("/api/v1/workspaces/{workspace_id}/campaigns", dependencies=[Depends(require_paid_workspace)])
    async def list_campaigns(workspace_id: UUID, identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        rows = await store_call(db.list_campaigns, identity.user_id, workspace_id)
        return {"campaigns": [serialize_row(row) for row in rows]}

    @app.post("/api/v1/workspaces/{workspace_id}/campaigns", status_code=201, dependencies=[Depends(require_paid_workspace)])
    async def create_campaign(workspace_id: UUID, body: CreateCampaignRequest, identity=Depends(verified_identity),
                              db: HostedStore = Depends(ready_store)):
        row = await store_call(db.create_campaign, identity.user_id, workspace_id,
                               name=body.name, goal=body.goal)
        return {"campaign": serialize_row(row)}

    @app.get("/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/contacts", dependencies=[Depends(require_paid_workspace)])
    async def list_campaign_contacts(workspace_id: UUID, campaign_id: UUID,
                                     identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        rows = await store_call(db.list_campaign_contacts, identity.user_id, workspace_id, campaign_id)
        return {"contacts": [serialize_row(row) for row in rows]}

    @app.post("/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/contacts", status_code=201, dependencies=[Depends(require_paid_workspace)])
    async def add_campaign_contact(workspace_id: UUID, campaign_id: UUID, body: AddCampaignContactRequest,
                                   identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        row = await store_call(db.add_campaign_contact, identity.user_id, workspace_id, campaign_id, body.contact_id)
        return serialize_row(row)

    @app.post("/api/v1/workspaces/{workspace_id}/jobs", status_code=202, dependencies=[Depends(require_paid_workspace)])
    async def enqueue_job(workspace_id: UUID, body: EnqueueJobRequest, identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        row = await store_call(db.enqueue_health_job, identity.user_id, workspace_id, idempotency_key=body.idempotency_key)
        # Persist first; dispatch is best-effort and may be retried. Never
        # claim completion until a separately leased worker commits the row.
        dispatch_requested = False
        if row["created"] and dispatch_job is not None:
            try:
                await asyncio.to_thread(dispatch_job)
                dispatch_requested = True
            except Exception:
                # Avoid leaking host/infrastructure details in API responses.
                pass
        return {"job": serialize_row(row), "dispatch_requested": dispatch_requested}

    @app.get("/api/v1/pricing")
    async def approved_pricing():
        # Public approved catalog, not an authorization or checkout endpoint.
        # This is deliberately independent of whether provider IDs exist.
        return {
            "currency": "INR", "billing_cycle": "monthly",
            "taxes": "additional_if_applicable", "live_payments_enabled": False,
            "plans": public_monthly_catalog(),
            "future_tiers": [FUTURE_AI_EMPLOYEE],
        }

    @app.get("/api/v1/workspaces/{workspace_id}/billing")
    async def billing_status(workspace_id: UUID, identity=Depends(verified_identity),
                             billing: tuple = Depends(ready_billing)):
        api, _, settings = billing
        rows = await store_call(api.list_subscriptions, identity.user_id, workspace_id)
        return {"subscriptions": [serialize_row(row) for row in rows], "mode": settings.mode}

    @app.get("/api/v1/workspaces/{workspace_id}/billing/plans")
    async def billing_plans(workspace_id: UUID, identity=Depends(verified_identity),
                            billing: tuple = Depends(ready_billing)):
        api, _, settings = billing
        # Membership is resolved by RLS for the provided verified identity.
        await store_call(api.list_subscriptions, identity.user_id, workspace_id)
        if settings.mode == "live":
            return {"mode": "live", "plans": [
                APPROVED_MONTHLY_TIERS["starter"].public(live_checkout_available=settings.checkout_enabled)
            ]}
        # Never advertise historic TEST plans at an unapproved amount.
        # Missing/mismatched provider IDs leave the approved tier visible
        # but its checkout button explicitly disabled.
        return {"mode": "test", "plans": public_monthly_catalog(
            settings.plans if test_checkout_allowed(workspace_id) else None
        )}

    @app.post("/api/v1/workspaces/{workspace_id}/billing/subscriptions", status_code=201)
    async def create_billing_subscription(workspace_id: UUID, body: CreateSubscriptionRequest,
                                          identity=Depends(verified_identity), billing: tuple = Depends(ready_billing)):
        api, gateway, settings = billing
        if settings.mode == "test" and not test_checkout_allowed(workspace_id):
            await store_call(api.list_subscriptions, identity.user_id, workspace_id)
            raise HTTPException(403, "Checkout is unavailable for this workspace")
        if not settings.checkout_enabled:
            raise HTTPException(403, "Live pilot checkout is disabled")
        if body.plan_code not in APPROVED_MONTHLY_TIERS:
            raise HTTPException(422, "Unknown or unavailable billing plan")
        plan = settings.plans.get(body.plan_code)
        if not approved_monthly_plan(plan, body.plan_code):
            raise HTTPException(409, "Approved plan is not configured at the exact INR price")
        reservation = await store_call(api.reserve_subscription, identity.user_id, workspace_id,
                                       plan=plan, idempotency_key=body.idempotency_key, provider_mode=settings.mode)
        provider_id = reservation.get("provider_subscription_id")
        if not reservation["created"] and not provider_id:
            # Provider calls have no documented general idempotency header.
            # An ambiguous first request must be reconciled, not charged twice.
            return JSONResponse(status_code=202, content={"status": "pending_reconciliation"})
        if reservation["created"]:
            try:
                provider_id = await asyncio.to_thread(gateway.create_subscription,
                                                      plan=plan, local_intent_id=str(reservation["id"]))
            except BillingUnavailable:
                raise HTTPException(503, "Gateway unavailable; contact support before retrying") from None
            await store_call(api.finalize_subscription, identity.user_id, reservation["id"], provider_id)
        return {
            "mode": settings.mode, "key_id": settings.key_id,
            "subscription_id": provider_id, "intent_id": str(reservation["id"]),
            "plan": plan.public(), "status": "checkout_ready",
        }

    @app.post("/api/v1/workspaces/{workspace_id}/billing/subscriptions/{subscription_id}/verify")
    async def verify_billing_checkout(workspace_id: UUID, subscription_id: UUID,
                                      body: VerifySubscriptionCheckoutRequest,
                                      identity=Depends(verified_identity), billing: tuple = Depends(ready_billing)):
        api, _, settings = billing
        sub = await store_call(api.get_checkout_subscription, identity.user_id, workspace_id, subscription_id)
        require_billing_mode(sub, settings)
        if not sub["provider_subscription_id"]:
            raise HTTPException(409, "Subscription has not been created")
        try:
            verify_checkout_signature(payment_id=body.razorpay_payment_id,
                                      stored_subscription_id=sub["provider_subscription_id"],
                                      signature=body.razorpay_signature,
                                      key_secret=settings.key_secret)
        except BillingRejected:
            raise HTTPException(400, "Invalid checkout signature") from None
        if settings.mode == "test":
            await store_call(api.mark_checkout_verified, identity.user_id, subscription_id,
                             body.razorpay_payment_id)
        return {"authorization_verified": True, "entitlement_pending_webhook": True}

    @app.post("/api/v1/workspaces/{workspace_id}/billing/subscriptions/{subscription_id}/cancel")
    async def cancel_billing_subscription(workspace_id: UUID, subscription_id: UUID,
                                          identity=Depends(verified_identity),
                                          billing: tuple = Depends(ready_billing)):
        api, gateway, settings = billing
        sub = await store_call(api.get_manageable_subscription, identity.user_id,
                               workspace_id, subscription_id)
        require_billing_mode(sub, settings)
        if sub["status"] in ("cancelled", "completed", "expired"):
            return {"status": "already_ended", "entitlement_pending_webhook": False}
        if not sub["provider_subscription_id"]:
            raise HTTPException(409, "Subscription is awaiting provider reconciliation")
        try:
            provider_status = await asyncio.to_thread(
                gateway.cancel_subscription,
                subscription_id=sub["provider_subscription_id"],
                provider_plan_id=sub["provider_plan_id"],
                at_cycle_end=True,
            )
        except BillingRejected:
            raise HTTPException(400, "Invalid cancellation request") from None
        except BillingUnavailable:
            raise HTTPException(503, "Cancellation status could not be confirmed; contact support") from None
        return {"status": "cancellation_requested", "provider_status": provider_status,
                "at_cycle_end": True, "entitlement_pending_webhook": True}

    async def record_razorpay_webhook(request: Request, settings: BillingSettings,
                                      signature: str | None, event_id: str | None):
        if not settings.enabled or billing_webhooks is None:
            raise HTTPException(503, "Billing webhook not configured")
        if request.headers.get("content-length", "").isdigit() and int(request.headers["content-length"]) > MAX_WEBHOOK_BODY_BYTES:
            raise HTTPException(413, "Webhook payload too large")
        # Do not let a misleading/missing Content-Length force Starlette to
        # buffer an arbitrarily large unauthenticated body before validation.
        chunks: list[bytes] = []
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > MAX_WEBHOOK_BODY_BYTES:
                raise HTTPException(413, "Webhook payload too large")
            chunks.append(chunk)
        raw = b"".join(chunks)
        try:
            verify_webhook_signature(raw_body=raw, signature=signature or "",
                                     webhook_secret=settings.webhook_secret)
            event = parse_verified_webhook(raw, event_id or "")
        except BillingRejected:
            raise HTTPException(400, "Invalid Razorpay webhook") from None
        if settings.mode == "live" and event["provider_plan_id"] != settings.plans["starter"].razorpay_plan_id:
            return {"received": True}
        await store_call(billing_webhooks.record, event, provider_mode=settings.mode, workspace_id=settings.workspace_id)
        return {"received": True}

    @app.post("/api/v1/billing/razorpay/webhook")
    async def razorpay_webhook(request: Request,
                              x_razorpay_signature: str | None = Header(default=None),
                              x_razorpay_event_id: str | None = Header(default=None)):
        return await record_razorpay_webhook(request, billing_settings, x_razorpay_signature, x_razorpay_event_id)

    @app.post("/api/v1/billing/razorpay/webhook/live")
    async def razorpay_live_webhook(request: Request,
                                   x_razorpay_signature: str | None = Header(default=None),
                                   x_razorpay_event_id: str | None = Header(default=None)):
        return await record_razorpay_webhook(request, live_billing, x_razorpay_signature, x_razorpay_event_id)

    @app.get("/api/v1/capabilities")
    async def capabilities():
        return {
            "stage": "private_beta",
            "workspace_api": bool(store),
            "contacts_enabled": bool(store),
            "campaign_drafts_enabled": bool(store),
            "consent_audit_enabled": bool(store),
            "encrypted_groq_key_storage_ready": bool(store and credential_vault),
            "job_kinds": ["workspace_health"] + (["groq_draft"] if ai_drafts_enabled else []),
            "ai_enabled": ai_drafts_enabled,
            "gmail_connection_enabled": bool(store and gmail_ready),
            "gmail_campaign_pilot_enabled": bool(store and campaign_pilot),
            "gmail_reply_sync_enabled": bool(store and reply_sync),
            "outbound_sending_enabled": False,
            "payments_enabled": False,
            "razorpay_test_billing_ready": bool(billing_settings.enabled and billing_api and billing_webhooks),
        }

    # Optional *staging-only* static preview. This does not mount the legacy
    # dashboard or its local owner-auth routes, and does not send messages.
    if static_dir and Path(static_dir).is_dir():
        app.mount("/static/landing", StaticFiles(directory=static_dir), name="staging-static")
        # Hosted builds are compiled with --base / for standalone hosting.
        # Modal must expose their immutable assets at the same absolute URLs.
        for prefix in ("assets", "fonts", "static"):
            asset_dir = Path(static_dir) / prefix
            if asset_dir.is_dir():
                app.mount(f"/{prefix}", StaticFiles(directory=asset_dir), name=f"hosted-{prefix}")

        for script_name in ("mailboxes.js", "sidebar.js"):
            script_path = Path(static_dir) / script_name
            if script_path.is_file():
                app.add_api_route(
                    f"/{script_name}",
                    lambda name=script_name: FileResponse(Path(static_dir) / name, media_type="text/javascript"),
                    methods=["GET"],
                    include_in_schema=False,
                )

        if Path(static_dir, "favicon.svg").is_file():
            @app.get("/static/favicon.svg", include_in_schema=False)
            async def favicon() -> FileResponse:
                return FileResponse(Path(static_dir) / "favicon.svg", media_type="image/svg+xml")

        for page in ("/login", "/signup", "/app", "/welcome", "/subscription-policy"):
            app.add_api_route(
                page,
                lambda: FileResponse(
                    Path(static_dir) / "index.html",
                    media_type="text/html",
                    headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
                ),
                methods=["GET"],
                include_in_schema=False,
            )

    return app
