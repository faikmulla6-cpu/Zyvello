"""Isolated Zyvello staging UI, workspace API and guarded draft worker.

Only deploy with a token scoped to the dedicated `zyvello` Modal workspace.
AI draft work is disabled by default. Outbound messages are never sent.
"""

import os

import modal


app = modal.App("zyvello-staging")
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi>=0.115,<1", "PyJWT[crypto]>=2.9,<3", "psycopg[binary]>=3.2,<4", "httpx>=0.27,<1")
    .add_local_python_source("zyvello_hosted")
    .add_local_dir("harvey/web/landing", remote_path="/app/zyvello_frontend")
    .add_local_file("harvey/web/favicon.svg", remote_path="/app/zyvello_frontend/favicon.svg")
)


@app.function(
    image=image, min_containers=0, max_containers=1, timeout=180,
    secrets=[modal.Secret.from_name("zyvello-staging-worker-db")],
)
def process_jobs():
    from zyvello_hosted.worker import HostedWorker

    url = os.environ.get("ZYVELLO_WORKER_DATABASE_URL")
    if not url:
        raise RuntimeError("Worker database is not configured")
    return HostedWorker(
        url,
        enable_ai_drafts=os.getenv("ZYVELLO_ENABLE_AI_DRAFTS", "false").lower() == "true",
        encryption_keys=os.getenv("ZYVELLO_CREDENTIAL_ENCRYPTION_KEYS"),
    ).process_batch(limit=10)


@app.function(
    image=image, min_containers=0, max_containers=1, timeout=30,
    secrets=[
        modal.Secret.from_name("zyvello-staging-api-db"),
        # Keep the billing reconciliation database credential separate from
        # payment-provider keys so owner configuration cannot overwrite it.
        modal.Secret.from_name("zyvello-staging-billing-db"),
        # Create this Secret with ZYVELLO_RAZORPAY_MODE=disabled first.
        # Billing remains fail-closed until *test* keys and the restricted
        # webhook DB role connection are supplied through Modal (not Hostinger).
        modal.Secret.from_name("zyvello-staging-billing"),
    ],
)
@modal.asgi_app()
def web():
    from zyvello_hosted.health import create_app

    # Both Neon database URLs are injected by dedicated Modal Secrets. Never
    # embed either credential into code or browser assets.
    def dispatch_job() -> None:
        process_jobs.spawn()

    return create_app(
        auth_base_url="https://ep-restless-cloud-aodhugwx.neonauth.c-2.ap-southeast-1.aws.neon.tech/neondb/auth",
        static_dir="/app/zyvello_frontend",
        database_url=os.environ.get("ZYVELLO_API_DATABASE_URL"),
        billing_database_url=os.environ.get("ZYVELLO_BILLING_DATABASE_URL"),
        dispatch_job=dispatch_job,
    )
