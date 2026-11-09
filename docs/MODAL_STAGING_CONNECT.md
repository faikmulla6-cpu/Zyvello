# Zyvello → Modal workspace: isolated staging connection

Status: **code prepared; credentials and deployment not configured**.

## Verified account state (9 November 2026)

- A new Modal workspace `zyvello` exists, separate from ClipKaro's `faikmulla6-cpu` workspace.
- It is on Starter with **$1/month included compute credits** and a **Modal-enforced $1 usage limit**.
- No payment method was added and no new Zyvello app has been deployed.
- This is a small, capped staging allowance, **not** a reliable production worker budget.

## What this change contains

- `modal_zyvello_staging.py` builds a cold-start, scale-to-zero FastAPI probe.
- `GET /healthz` returns `200` for basic runtime connectivity.
- `GET /readyz` deliberately returns `503` until hosted identity, PostgreSQL, tenant isolation and durable jobs are implemented.
- There is **no** handler for customer registration, dashboard access, AI requests, email sending or scheduled work.
- The existing local `harvey` runtime, all AI providers, authentication and SQLite data are untouched.

## Manual connection and deploy checklist

1. In Modal, switch to the **zyvello** workspace. Check its Usage & billing page, including the enforced $1 limit.
2. On the deployment machine, install the official Modal CLI through a supported Python environment and run `modal setup`.
3. In the Modal sign-in page, explicitly select the `zyvello` workspace—not `faikmulla6-cpu`.
4. Verify workspace context using `modal token info`, `modal profile current`, and `modal app list --json`. **Stop if any command identifies ClipKaro's workspace.** Do not paste credentials into GitHub, chat, logs or source files.
5. With this repository checked out, run `modal deploy --env main modal_zyvello_staging.py` only after reviewing the selected profile. Do not use unattended auto-deploy.
6. Test `/healthz` and `/readyz` at the URL returned by Modal. Confirm only the liveness endpoint is healthy; readiness must remain `503`.
7. Review compute usage. On the $1 Starter cap, stop after the connectivity smoke test.

This probe is **not an authorization** to point `zyvello.pro` at Modal or expose the existing local dashboard. Production needs PostgreSQL, hosted sessions, workspace-level access controls, encrypted credentials, durable worker jobs, suppression/idempotency, and full end-to-end tests.

Official references:
- https://modal.com/docs/guide/workspaces
- https://modal.com/docs/guide/webhooks
- https://modal.com/docs/guide/environments
- https://modal.com/docs/cli/latest/deploy
