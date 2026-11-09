"""Isolated hosted staging API for verified Zyvello users.

This module must never import the local owner-authenticated dashboard or the
single-tenant SQLite state manager. Neither is safe to expose as a hosted SaaS.
"""

import asyncio
from pathlib import Path
from typing import Callable
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import IdentityProviderUnavailable, NeonTokenVerifier, TokenRejected
from .repository import HostedStore, StoreConflict, StoreLimit, StoreNotFound, StoreUnavailable, serialize_row


class CreateWorkspaceRequest(BaseModel):
    slug: str = Field(min_length=3, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
    name: str = Field(min_length=1, max_length=200)


class EnqueueJobRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)


def create_app(
    *, auth_base_url: str | None = None,
    static_dir: str | None = None,
    database_url: str | None = None,
    dispatch_job: Callable[[], None] | None = None,
    store_override: HostedStore | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Zyvello staging workspace API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    verifier = NeonTokenVerifier(auth_base_url) if auth_base_url else None
    store = store_override or (HostedStore(database_url) if database_url else None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://zyvello--zyvello-staging-web.us-east.modal.run"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )

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
        except StoreLimit as error:
            raise HTTPException(429, str(error), headers={"Retry-After": "30"}) from None
        except StoreUnavailable:
            raise HTTPException(503, "Workspace database is temporarily unavailable") from None

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

    @app.get("/api/v1/capabilities")
    async def capabilities():
        return {
            "stage": "private_beta",
            "workspace_api": bool(store),
            "job_kinds": ["workspace_health"],
            "ai_enabled": False,
            "outbound_sending_enabled": False,
            "payments_enabled": False,
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
