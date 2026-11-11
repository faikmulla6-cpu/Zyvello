"""Isolated hosted staging API for verified Zyvello users.

This module must never import the local owner-authenticated dashboard or the
single-tenant SQLite state manager. Neither is safe to expose as a hosted SaaS.
"""

import asyncio
import os
from pathlib import Path
from typing import Callable
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .auth import IdentityProviderUnavailable, NeonTokenVerifier, TokenRejected
from .credentials import CredentialConfigurationError, CredentialVault
from .billing_store import BillingApiStore, BillingWebhookStore
from .razorpay_billing import (MAX_WEBHOOK_BODY_BYTES, BillingRejected, BillingSettings,
                               BillingUnavailable, RazorpayGateway, parse_verified_webhook,
                               verify_checkout_signature, verify_webhook_signature)
from .repository import HostedStore, StoreConflict, StoreForbidden, StoreLimit, StoreNotFound, StoreUnavailable, serialize_row


class CreateWorkspaceRequest(BaseModel):
    slug: str = Field(min_length=3, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
    name: str = Field(min_length=1, max_length=200)


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
    credential_vault_override: CredentialVault | None = None,
    enable_ai_drafts_override: bool | None = None,
    allow_workspace_creation_override: bool | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Zyvello staging workspace API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    verifier = NeonTokenVerifier(auth_base_url) if auth_base_url else None
    store = store_override or (HostedStore(database_url) if database_url else None)
    billing_api = billing_api_override or (BillingApiStore(database_url) if database_url else None)
    billing_webhooks = billing_webhook_override or (BillingWebhookStore(billing_database_url) if billing_database_url else None)
    billing_settings = billing_settings_override or BillingSettings.from_env()
    billing_gateway = billing_gateway_override or (RazorpayGateway(billing_settings) if billing_settings.enabled else None)
    credential_vault = credential_vault_override
    if credential_vault is None and os.getenv("ZYVELLO_CREDENTIAL_ENCRYPTION_KEYS"):
        credential_vault = CredentialVault(os.environ["ZYVELLO_CREDENTIAL_ENCRYPTION_KEYS"])
    ai_drafts_enabled = (enable_ai_drafts_override if enable_ai_drafts_override is not None
                         else os.getenv("ZYVELLO_ENABLE_AI_DRAFTS", "false").lower() == "true") and bool(credential_vault)
    # Closed private beta: no self-provisioning from a merely valid OTP identity.
    allow_workspace_creation = (allow_workspace_creation_override if allow_workspace_creation_override is not None
                                else os.getenv("ZYVELLO_ENABLE_NEW_WORKSPACES", "false").lower() == "true")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://zyvello--zyvello-staging-web.us-east.modal.run",
            "https://peachpuff-curlew-471332.hostingersite.com",
        ],
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )

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
            return await asyncio.to_thread(verifier.verify, authorization[7:])
        except TokenRejected:
            raise HTTPException(401, "Invalid or unverified identity", headers={"WWW-Authenticate": "Bearer"}) from None
        except IdentityProviderUnavailable:
            raise HTTPException(503, "Identity provider is temporarily unavailable") from None

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

    def ready_billing() -> tuple[BillingApiStore, RazorpayGateway]:
        if not billing_settings.enabled or billing_api is None or billing_gateway is None or billing_webhooks is None:
            raise HTTPException(503, "Test-mode billing is not configured")
        return billing_api, billing_gateway

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"service": "zyvello-staging", "status": "alive"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reason": "Hosted outreach and scheduled recovery are intentionally disabled before launch",
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
            raise HTTPException(403, "New workspaces require a private-beta invitation")
        row = await store_call(db.create_workspace, identity.user_id, name=body.name, slug=body.slug)
        return {"workspace": serialize_row(row)}

    @app.get("/api/v1/workspaces/{workspace_id}")
    async def get_workspace(workspace_id: UUID, identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        row = await store_call(db.get_workspace, identity.user_id, workspace_id)
        return {"workspace": serialize_row(row)}

    @app.get("/api/v1/workspaces/{workspace_id}/jobs")
    async def list_jobs(workspace_id: UUID, identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        rows = await store_call(db.list_jobs, identity.user_id, workspace_id)
        return {"jobs": [serialize_row(row) for row in rows]}

    @app.get("/api/v1/workspaces/{workspace_id}/contacts")
    async def list_contacts(workspace_id: UUID, identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        rows = await store_call(db.list_contacts, identity.user_id, workspace_id)
        return {"contacts": [serialize_row(row) for row in rows]}

    @app.post("/api/v1/workspaces/{workspace_id}/contacts", status_code=201)
    async def create_contact(workspace_id: UUID, body: CreateContactRequest, identity=Depends(verified_identity),
                             db: HostedStore = Depends(ready_store)):
        row = await store_call(db.create_contact, identity.user_id, workspace_id,
                               full_name=body.full_name, email=body.email, company=body.company)
        return {"contact": serialize_row(row)}

    @app.get("/api/v1/workspaces/{workspace_id}/contacts/{contact_id}/permission")
    async def get_contact_permission(workspace_id: UUID, contact_id: UUID,
                                     identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        row = await store_call(db.get_contact_permission, identity.user_id, workspace_id, contact_id)
        return {"permission": serialize_row(row), "outbound_allowed": False}

    @app.post("/api/v1/workspaces/{workspace_id}/contacts/{contact_id}/permission", status_code=201)
    async def record_contact_permission(workspace_id: UUID, contact_id: UUID, body: ContactPermissionRequest,
                                        identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        row = await store_call(db.record_contact_permission, identity.user_id, workspace_id, contact_id,
                               action=body.action, source=body.source, evidence=body.evidence)
        return {"permission": serialize_row(row), "outbound_allowed": False}

    @app.get("/api/v1/workspaces/{workspace_id}/credentials/groq")
    async def groq_credential_status(workspace_id: UUID, identity=Depends(verified_identity),
                                     db: HostedStore = Depends(ready_store)):
        row = await store_call(db.get_groq_credential_status, identity.user_id, workspace_id)
        return {"provider": "groq", **serialize_row(row), "ai_execution_enabled": ai_drafts_enabled}

    @app.put("/api/v1/workspaces/{workspace_id}/credentials/groq")
    async def save_groq_credential(workspace_id: UUID, body: PutGroqCredentialRequest,
                                   identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        if credential_vault is None:
            raise HTTPException(503, "Credential encryption is not configured")
        try:
            ciphertext = credential_vault.seal(body.api_key.get_secret_value())
        except (CredentialConfigurationError, ValueError):
            raise HTTPException(422, "Invalid provider credential") from None
        row = await store_call(db.put_groq_credential, identity.user_id, workspace_id, ciphertext=ciphertext)
        return {"provider": "groq", **serialize_row(row), "ai_execution_enabled": ai_drafts_enabled}

    @app.delete("/api/v1/workspaces/{workspace_id}/credentials/groq")
    async def remove_groq_credential(workspace_id: UUID, identity=Depends(verified_identity),
                                     db: HostedStore = Depends(ready_store)):
        row = await store_call(db.delete_groq_credential, identity.user_id, workspace_id)
        return {"provider": "groq", **row, "ai_execution_enabled": ai_drafts_enabled}

    @app.get("/api/v1/workspaces/{workspace_id}/drafts")
    async def list_generated_drafts(workspace_id: UUID, identity=Depends(verified_identity),
                                    db: HostedStore = Depends(ready_store)):
        rows = await store_call(db.list_generated_drafts, identity.user_id, workspace_id)
        return {"drafts": [serialize_row(row) for row in rows], "outbound_sending_enabled": False}

    @app.post("/api/v1/workspaces/{workspace_id}/drafts", status_code=202)
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

    @app.get("/api/v1/workspaces/{workspace_id}/campaigns")
    async def list_campaigns(workspace_id: UUID, identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        rows = await store_call(db.list_campaigns, identity.user_id, workspace_id)
        return {"campaigns": [serialize_row(row) for row in rows]}

    @app.post("/api/v1/workspaces/{workspace_id}/campaigns", status_code=201)
    async def create_campaign(workspace_id: UUID, body: CreateCampaignRequest, identity=Depends(verified_identity),
                              db: HostedStore = Depends(ready_store)):
        row = await store_call(db.create_campaign, identity.user_id, workspace_id,
                               name=body.name, goal=body.goal)
        return {"campaign": serialize_row(row)}

    @app.get("/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/contacts")
    async def list_campaign_contacts(workspace_id: UUID, campaign_id: UUID,
                                     identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        rows = await store_call(db.list_campaign_contacts, identity.user_id, workspace_id, campaign_id)
        return {"contacts": [serialize_row(row) for row in rows]}

    @app.post("/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/contacts", status_code=201)
    async def add_campaign_contact(workspace_id: UUID, campaign_id: UUID, body: AddCampaignContactRequest,
                                   identity=Depends(verified_identity), db: HostedStore = Depends(ready_store)):
        row = await store_call(db.add_campaign_contact, identity.user_id, workspace_id, campaign_id, body.contact_id)
        return serialize_row(row)

    @app.post("/api/v1/workspaces/{workspace_id}/jobs", status_code=202)
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

    @app.get("/api/v1/workspaces/{workspace_id}/billing")
    async def billing_status(workspace_id: UUID, identity=Depends(verified_identity),
                             billing: tuple = Depends(ready_billing)):
        api, _ = billing
        rows = await store_call(api.list_subscriptions, identity.user_id, workspace_id)
        return {"subscriptions": [serialize_row(row) for row in rows], "mode": "test"}

    @app.get("/api/v1/workspaces/{workspace_id}/billing/plans")
    async def billing_plans(workspace_id: UUID, identity=Depends(verified_identity),
                            billing: tuple = Depends(ready_billing)):
        api, _ = billing
        # Membership is resolved by RLS for the provided verified identity.
        await store_call(api.list_subscriptions, identity.user_id, workspace_id)
        return {"mode": "test", "plans": [plan.public() for plan in billing_settings.plans.values()]}

    @app.post("/api/v1/workspaces/{workspace_id}/billing/subscriptions", status_code=201)
    async def create_billing_subscription(workspace_id: UUID, body: CreateSubscriptionRequest,
                                          identity=Depends(verified_identity), billing: tuple = Depends(ready_billing)):
        api, gateway = billing
        plan = billing_settings.plans.get(body.plan_code)
        if plan is None:
            raise HTTPException(422, "Unknown billing plan")
        reservation = await store_call(api.reserve_subscription, identity.user_id, workspace_id,
                                       plan=plan, idempotency_key=body.idempotency_key)
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
            "mode": "test", "key_id": billing_settings.key_id,
            "subscription_id": provider_id, "intent_id": str(reservation["id"]),
            "plan": plan.public(), "status": "checkout_ready",
        }

    @app.post("/api/v1/workspaces/{workspace_id}/billing/subscriptions/{subscription_id}/verify")
    async def verify_billing_checkout(workspace_id: UUID, subscription_id: UUID,
                                      body: VerifySubscriptionCheckoutRequest,
                                      identity=Depends(verified_identity), billing: tuple = Depends(ready_billing)):
        api, _ = billing
        sub = await store_call(api.get_checkout_subscription, identity.user_id, workspace_id, subscription_id)
        if not sub["provider_subscription_id"]:
            raise HTTPException(409, "Subscription has not been created")
        try:
            verify_checkout_signature(payment_id=body.razorpay_payment_id,
                                      stored_subscription_id=sub["provider_subscription_id"],
                                      signature=body.razorpay_signature,
                                      key_secret=billing_settings.key_secret)
        except BillingRejected:
            raise HTTPException(400, "Invalid checkout signature") from None
        await store_call(api.mark_checkout_verified, identity.user_id, subscription_id,
                         body.razorpay_payment_id)
        return {"authorization_verified": True, "entitlement_pending_webhook": True}

    @app.post("/api/v1/billing/razorpay/webhook")
    async def razorpay_webhook(request: Request,
                              x_razorpay_signature: str | None = Header(default=None),
                              x_razorpay_event_id: str | None = Header(default=None)):
        if not billing_settings.enabled or billing_webhooks is None:
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
            verify_webhook_signature(raw_body=raw, signature=x_razorpay_signature or "",
                                     webhook_secret=billing_settings.webhook_secret)
            event = parse_verified_webhook(raw, x_razorpay_event_id or "")
        except BillingRejected:
            raise HTTPException(400, "Invalid Razorpay webhook") from None
        await store_call(billing_webhooks.record, event)
        return {"received": True}

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
            "outbound_sending_enabled": False,
            "payments_enabled": False,
            "razorpay_test_billing_ready": bool(billing_settings.enabled and billing_api and billing_webhooks),
        }

    # Optional *staging-only* static preview. This does not mount the legacy
    # dashboard or its local owner-auth routes, and does not send messages.
    if static_dir and Path(static_dir).is_dir():
        app.mount("/static/landing", StaticFiles(directory=static_dir), name="staging-static")

        if Path(static_dir, "favicon.svg").is_file():
            @app.get("/static/favicon.svg", include_in_schema=False)
            async def favicon() -> FileResponse:
                return FileResponse(Path(static_dir) / "favicon.svg", media_type="image/svg+xml")

        for page in ("/login", "/signup", "/app", "/welcome"):
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
