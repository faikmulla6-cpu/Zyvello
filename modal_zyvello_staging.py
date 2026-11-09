"""Minimal Modal deployment probe, NOT the customer-facing Zyvello backend.

Only deploy with a token scoped to the dedicated `zyvello` Modal workspace.
No AI calls, sending, schedules, secrets, customer state, or persistent workers.
"""

import modal


app = modal.App("zyvello-staging")
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi>=0.115,<1", "PyJWT[crypto]>=2.9,<3")
    .add_local_python_source("zyvello_hosted")
    .add_local_dir("harvey/web/landing", remote_path="/app/zyvello_frontend")
)


@app.function(image=image, min_containers=0, max_containers=1, timeout=30)
@modal.asgi_app()
def web():
    from zyvello_hosted.health import create_app

    # The Neon Auth URL is public (also built into the Vite frontend). No DB
    # password or signing key is embedded here; verification uses public JWKS.
    return create_app(
        auth_base_url="https://ep-restless-cloud-aodhugwx.neonauth.c-2.ap-southeast-1.aws.neon.tech/neondb/auth",
        static_dir="/app/zyvello_frontend",
    )
