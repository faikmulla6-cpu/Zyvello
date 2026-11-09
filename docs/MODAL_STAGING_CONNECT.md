# Zyvello → Modal workspace: isolated staging connection

Status: **isolated staging probe deployed and verified on 9 November 2026**.

## Verified account state (9 November 2026)

- A new Modal workspace `zyvello` exists, separate from ClipKaro's `faikmulla6-cpu` workspace.
- It is on Starter with **$1/month included compute credits** and a **Modal-enforced $1 usage limit**.
- No payment method was added. The `zyvello-staging` probe is deployed in the
  workspace's `main` environment; it intentionally does not run the product.
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
4. Verify workspace context using `modal token info --profile zyvello`
   and `modal app list --profile zyvello --json`. **Stop if either identifies
   ClipKaro's workspace.** Do not paste credentials into GitHub, chat, logs
   or source files. The computer's default profile remains ClipKaro.
5. With this repository checked out, run
   `modal deploy --profile zyvello --env main modal_zyvello_staging.py`.
   The explicit profile is mandatory. Do not use unattended auto-deploy.
6. Test `/healthz` and `/readyz` at the URL returned by Modal. Confirm only the liveness endpoint is healthy; readiness must remain `503`.
7. Review compute usage. On the $1 Starter cap, stop after the connectivity smoke test.

## Verified deployment

- Dashboard: https://modal.com/apps/zyvello/main/deployed/zyvello-staging
- Runtime health: https://zyvello--zyvello-staging-web.us-east.modal.run/healthz
  returned HTTP 200 with `{ "status": "alive" }`.
- Readiness: https://zyvello--zyvello-staging-web.us-east.modal.run/readyz
  returned HTTP 503 with `{ "status": "not_ready" }`, as designed.
- The new 7-day `zyvello` CLI credential was verified without changing the
  existing default Modal profile. The token itself is never stored in Git.
- Neon `Zyvello Staging` is a separate PostgreSQL project in Singapore. Its
  Managed Auth is provisioned; public signup is disabled and verification uses
  email OTP. The versioned hosted tables are in
  `hosted/migrations/0001_workspace_jobs.sql`.

This probe is **not an authorization** to point `zyvello.pro` at Modal or expose
the existing local dashboard. Production needs PostgreSQL-backed application
services, hosted sessions, workspace-level access controls, encrypted
credentials, durable worker jobs, suppression/idempotency, and full
end-to-end tests. The `/readyz` endpoint must remain HTTP 503 until then.

Official references:
- https://modal.com/docs/guide/workspaces
- https://modal.com/docs/guide/webhooks
- https://modal.com/docs/guide/environments
- https://modal.com/docs/cli/latest/deploy
