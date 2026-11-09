"""Minimal Modal deployment probe, NOT the customer-facing Zyvello backend.

Only deploy with a token scoped to the dedicated `zyvello` Modal workspace.
No AI calls, sending, schedules, secrets, customer state, or persistent workers.
"""

import modal


app = modal.App("zyvello-staging")
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi>=0.115,<1")
    .add_local_python_source("zyvello_hosted")
)


@app.function(image=image, min_containers=0, max_containers=1, timeout=30)
@modal.asgi_app()
def web():
    from zyvello_hosted.health import create_app

    return create_app()
