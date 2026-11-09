"""Safe, intentionally non-production staging surface for Modal connectivity tests.

This module must never import the local owner-authenticated dashboard or the
single-tenant SQLite state manager. Neither is safe to expose as a hosted SaaS.
"""

import asyncio
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import IdentityProviderUnavailable, NeonTokenVerifier, TokenRejected


def create_app(*, auth_base_url: str | None = None, static_dir: str | None = None) -> FastAPI:
    app = FastAPI(
        title="Zyvello staging identity and connectivity",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    verifier = NeonTokenVerifier(auth_base_url) if auth_base_url else None

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"service": "zyvello-staging", "status": "alive"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reason": "Workspace authorization, database API and durable jobs are not connected",
            },
        )

    @app.get("/api/v1/me")
    async def authenticated_identity(authorization: str | None = Header(default=None)) -> dict[str, str]:
        """Verify an identity only; intentionally grant no workspace access."""
        if verifier is None:
            raise HTTPException(503, "Identity verification is not configured")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "A bearer token is required", headers={"WWW-Authenticate": "Bearer"})
        try:
            identity = await asyncio.to_thread(verifier.verify, authorization[7:])
        except TokenRejected:
            raise HTTPException(401, "Invalid or unverified identity", headers={"WWW-Authenticate": "Bearer"}) from None
        except IdentityProviderUnavailable:
            raise HTTPException(503, "Identity provider is temporarily unavailable") from None
        return {"user_id": identity.user_id, "email": identity.email, "status": "identity_verified"}

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
