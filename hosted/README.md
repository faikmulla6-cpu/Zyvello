# Zyvello hosted SaaS — staging runbook and launch gates

Status: **private staging deployed, not customer-production-ready** (2026-11-09).

The original single-owner Harvey CLI/dashboard, SQLite state, prospecting,
provider integrations and Groq/Claude/Codex code remain in the repository.
None of the legacy engine is imported or executed by the hosted API. Razorpay
and all payment logic are explicitly deferred until the owner supplies keys.

## Current staging architecture

- **UI**: React/Vite app with `VITE_ZYVELLO_HOSTED_MODE=true`, public Neon Auth
  base URL, and public signup disabled. Hosted `/app` shows a private workspace,
  contacts, campaign drafts and safe job history. This is NOT the full Harvey
  product dashboard.
- **Auth**: Neon Managed Better Auth (`neon_auth` schema), email verification,
  signed JWT session, and server-side JWKS signature/issuer/audience/expiry
  verification before every protected API request.
- **API**: Modal FastAPI `zyvello-staging` (`zyvello_hosted/health.py`), using
  only the restricted `zyvello_api_staging` database credential. The browser
  never receives a database URL, signing key or AI key.
- **Data**: Neon PostgreSQL staging `zyvello` schema, `workspaces`,
  `memberships`, `jobs`, `contacts`, `campaigns`, `campaign_contacts`, and
  migrations. Contacts are private and campaigns can only be drafts.
  Database RLS restricts every API
  transaction to the verified JWT subject through transaction-local
  `zyvello.auth_user_id`; parameterized SQL is mandatory.
- **Worker**: Modal `process_jobs` function, using a DIFFERENT restricted
  `zyvello_worker_staging` database credential. It reads/updates only the
  durable jobs queue, claims via `FOR UPDATE SKIP LOCKED`, and retries or
  dead-letters unsuccessful attempts. The sole allowed kind is
  `workspace_health`: a local, inert diagnostic.
- **Costs**: Modal workspace `zyvello` is separate from ClipKaro; no always-on
  containers and no periodic worker schedule currently. Expect cold starts.
  Starter included compute/credits are finite, not a production uptime or
  free-unlimited-use guarantee.

### Database migrations

Apply in order in a transaction per migration. Never point a staging migration
at the ClipKaro database or live Hostinger database:

1. `0001_workspace_jobs.sql`: isolated schema, durable job metadata, RLS
   enabled with no access policies by default.
2. `0002_request_scoped_rls.sql`: authorized user-visible reads, restricted API
   role, and no access to `neon_auth` or migration history.
3. `0003_workspace_api_and_worker.sql`: workspace-creator membership policy,
   allowed safe job writes, job result metadata, separate worker privileges.
4. `0004_contact_campaign_drafts.sql`: workspace-private contact directory,
   inert campaign drafts, and draft/contact assignments. Read access follows
   membership and insert access requires owner/admin membership. The API has
   SELECT/INSERT, not UPDATE/DELETE, for these tables; the worker has none.

Roles `zyvello_api_staging` and `zyvello_worker_staging` must be **non-owner**
Neon scoped roles. Migration 0003 removes worker table grants other than `jobs`
SELECT/UPDATE. Never use the schema owner for API or worker: owner roles can
bypass RLS. `SET LOCAL` is application context, NOT an identity credential;
only verified JWT claims may supply its value.

Migrations through 0004 were applied first to isolated Neon branch
`dev-tenant-rls-20261109`, then staging `main` on 2026-11-09, with schema
versions 1, 2, 3 and 4 confirmed. Synthetic accounts on the isolated branch
verified that a second workspace could not see or insert the first workspace's
records. These are **not** Hostinger's production data.

## Modal deployment

Use the existing **zyvello** CLI profile; ClipKaro has a separate Modal
profile and must never receive these secrets. Two secrets exist in `main`:

| Secret name | Environment variable | Used by |
| --- | --- | --- |
| `zyvello-staging-api-db` | `ZYVELLO_API_DATABASE_URL` | `web` |
| `zyvello-staging-worker-db` | `ZYVELLO_WORKER_DATABASE_URL` | `process_jobs` |

Connection strings reside in Modal Secrets, never in git or browser assets.
On Windows select process environment `MODAL_PROFILE=zyvello` and set
`PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8` to avoid console encoding failure.
Build with `VITE_ZYVELLO_ENABLE_SIGNUP=false` and the **public** Neon Auth
URL; deploy `modal_zyvello_staging.py` into Modal environment `main`.
No Hostinger live files or DNS records are modified by this command.

Staging: https://zyvello--zyvello-staging-web.us-east.modal.run/app

## Verified checks (2026-11-09)

- Local Python hosted auth/API tests: **16 passed** at branch commit
  `50074da5421604e4d2e6f13a356a2a5408baec23`.
- Windows TypeScript/Vite production build of the same commit passed.
- Earlier GitHub `hosted-staging-checks` and `tests` workflows were green at
  `af6e075b72c9f31a8db878087809024f642c3551`.
- **Current commit's GitHub CI is unverified**: both workflows reported failure,
  but all jobs have `steps: null` and no logs, indicating they did not run any
  steps. Do not mistake this for a passing test run.
- Neon schema v4 applied on dev then staging; new tables have RLS, the API
  lacks UPDATE/DELETE, and the worker lacks access to contacts/campaigns.
- A real signed-in owner saved a synthetic contact, created a draft campaign,
  attached that contact and reloaded the page. Each persisted in PostgreSQL.
  Staging aggregate counts: 1 test contact, 1 draft, 1 assignment.
- After deployment the Modal worker processed a second `workspace_health`
  diagnostic successfully on its first attempt (no AI, sends or payments).
- An unauthenticated GET to the contact endpoint returned HTTP 401.
- Staging endpoints: `/healthz` 200, `/app` 200,
  `/api/v1/capabilities` 200; unauthenticated `/api/v1/me` and
  `/api/v1/workspaces` 401.
- `/readyz` deliberately returns **503** until all release gates pass.
- Real signed-in account created owner workspace `Zyvello`, queued a
  `workspace_health` job, and Modal worker completed it on first attempt.
  PostgreSQL returned `succeeded`, `workspace_queue_healthy`; the app
  rendered the result after reload. No AI calls, email or payment occurred.

## Security and launch blockers

Do **not** merge this branch into production or point `zyvello.pro` to Modal.
Before unrestricted users and paying customers can use Zyvello:

1. Continue replacing the legacy SQLite business model: contacts and inert
   campaign drafts exist, but clients, outreach sequences, mailbox
   configurations, AI settings, approvals, usage and audit events are pending.
   Define contact editing/export/deletion and retention.
2. Add per-user BYO AI key vault with encryption at rest, redaction and
   rotation. Preserve Groq/Claude/Codex compatibility; do not enable paid
   calls during QA without deliberate opt-in.
3. Design India-first compliant sending: validated mailboxes/domains,
   opt-out/suppression, quotas, abuse prevention, explicit approvals, bounce
   handling, idempotent sends and logs.
4. Add durable worker schedules, crash recovery, workspace-specific job
   permissions, lease-expiry tests and operational monitoring. The current
   best-effort Modal spawn has **no automatic lost-dispatch recovery**.
5. Harden auth signup/invites, reset/revocation, MFA/OAuth as required and
   tenant isolation for each new table and endpoint. Test with two
   independent real users as well as synthetic database identities.
6. Run functional, mobile, accessibility, SEO, security, load, backup/restore
   and failure tests; implement monitoring, alerts, rollback and incident
   response before launch.
7. Configure Hostinger production frontend routing, API origin, TLS,
   trusted domains, CORS and cookie behavior only after staging passes.
8. Once Faik provides Razorpay keys, separately implement and test server
   order creation, verified webhook signatures, idempotency, entitlements
   and reconciliation. **Payment integration is currently absent**.

Staging proves the shared workspace/auth/queue foundation, but is not yet
a production-grade customer outreach service.
