# Zyvello hosted SaaS — staging runbook and launch gates

Status: **private staging deployed, not customer-production-ready** (2026-11-10).

The original single-owner Harvey CLI/dashboard, SQLite state, prospecting,
provider integrations and Groq/Claude/Codex code remain in the repository.
None of the legacy engine is imported or executed by the hosted API. A
**test-mode-only** Razorpay Subscriptions adapter and protected billing schema
have been prepared on the working branch. Payment initiation remains disabled
until Faik supplies test keys, verified plan prices and IDs, a separate webhook
secret, and a restricted billing database connection. There is no live charging
code path or Hostinger production change.

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
5. `0005_razorpay_test_billing.sql`: per-workspace subscription intents,
   delivery-deduplicated webhook ledger, separate billing reconciler role and
   strict RLS. API owners can create intents but cannot assign entitlements.
   Only reconciled signed captured-payment events with matching amount and
   provider plan can advance `paid_through`.

Roles `zyvello_api_staging`, `zyvello_worker_staging`, and
`zyvello_billing_staging` must be **non-owner**
Neon scoped roles. Migration 0003 removes worker table grants other than `jobs`
SELECT/UPDATE. The billing role must lack permissions on contacts, workspace
tables and migration history; it may read/update only subscription rows and
read/insert only webhook events. Never use the schema owner for hosted services: owner roles can
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
| `zyvello-staging-billing-db` | `ZYVELLO_BILLING_DATABASE_URL` | `web` only, restricted DB role |
| `zyvello-staging-billing` | `ZYVELLO_RAZORPAY_*` (below) | `web` only, Razorpay test configuration |

Connection strings reside in Modal Secrets, never in git or browser assets.
On Windows select process environment `MODAL_PROFILE=zyvello` and set
`PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8` to avoid console encoding failure.
Build with `VITE_ZYVELLO_ENABLE_SIGNUP=false` and the **public** Neon Auth
URL; deploy `modal_zyvello_staging.py` into Modal environment `main`.
No Hostinger live files or DNS records are modified by this command.

Staging: https://zyvello--zyvello-staging-web.us-east.modal.run/app

## Razorpay subscription integration — test only

The new billing module implements these protected endpoints:

- `GET /api/v1/workspaces/{id}/billing`: workspace-scoped subscriptions and
  **derived** entitlement (`status=active` and `paid_through` in future).
- `GET /api/v1/workspaces/{id}/billing/plans`: server-published test plans for
  members of the workspace. Never exposes Razorpay secrets or provider plan IDs.
- `POST /api/v1/workspaces/{id}/billing/subscriptions`: owner only, requires
  plan code plus an idempotency key. The backend validates the actual Razorpay
  plan pricing before creating a subscription. A lost provider response leaves
  a pending-reconciliation intent; it never charges twice on automatic retry.
- `POST /api/v1/workspaces/{id}/billing/subscriptions/{intent_id}/verify`:
  checkout payment signature verification against the **stored** provider
  subscription ID. This never grants paid access.
- `POST /api/v1/billing/razorpay/webhook`: public endpoint that immediately
  rejects unsigned/oversized payloads. HMAC uses the **raw** request body and
  a distinct webhook secret. Database deduplicates the Razorpay event ID;
  only the dedicated billing role can record it and update paid-through state.

Set up **Razorpay Test Mode**, create exact test subscription plans in the
Razorpay Dashboard, and configure a Test Mode webhook at
`https://zyvello--zyvello-staging-web.us-east.modal.run/api/v1/billing/razorpay/webhook`.
Subscribe to `subscription.authenticated`, `subscription.activated`,
`subscription.charged`, `subscription.pending`, `subscription.halted`,
`subscription.paused`, `subscription.resumed`, `subscription.cancelled`,
`subscription.completed`, and `subscription.expired`. Keep the webhook signing
secret distinct from the API key secret. Do not paste secrets into GitHub,
ChatGPT, frontend assets or source code.

Create the named **Modal** Secret `zyvello-staging-billing` in the dedicated
`zyvello` workspace, initially with `ZYVELLO_RAZORPAY_MODE=disabled` so
the staging app can be deployed safely. Once staged schema v5, a separately
created restricted DB role and **test-only** secrets have been verified,
configure the following **payment** variables in that Secret. Store the
restricted Neon billing role DSN in the separate `zyvello-staging-billing-db`
Secret so that billing key rotation does not affect database access:

| Environment variable | Value |
| --- | --- |
| `ZYVELLO_RAZORPAY_MODE` | `test` (anything else stays disabled) |
| `ZYVELLO_RAZORPAY_KEY_ID` | Razorpay **Test** key ID, begins `rzp_test_` |
| `ZYVELLO_RAZORPAY_KEY_SECRET` | Matching test API key secret |
| `ZYVELLO_RAZORPAY_WEBHOOK_SECRET` | Separate secret from Test Mode webhook settings |
| `ZYVELLO_RAZORPAY_PLANS_JSON` | JSON object mapping chosen plan codes to Razorpay Test Mode plan IDs and verified INR paise amounts |

Example JSON *shape only*, not a real pricing decision:

```json
{"starter":{"name":"Example monthly","amount_paise":9900,"razorpay_plan_id":"plan_REPLACE_WITH_TEST_ID","period":"monthly","interval":1,"total_count":12}}
```

Faik must confirm **actual** plan names/prices/cycle and provision the
corresponding plan IDs before this is used. `amount_paise` is INR × 100.
Ensure exact Razorpay amount/currency/period/interval match. Hostinger
environment keys alone do **not** reach Modal; staging runs on Modal. The
frontend receives only the test key ID. Do not set live credentials.

Test acceptance gate before any payment launch: two real signed-in workspaces,
malformed/tampered checkout and webhook signature rejection, replayed webhook,
out-of-order delivery, wrong plan or amount, pending/failed payment, captured
payment, cancellation, and idempotent lost-response simulation. Confirm no
cross-tenant billing visibility. Validate test payment with Razorpay sandbox
and inspect dashboard events, then check database reconciliation. A developer
must separately review entitlements, refunds, tax invoices, retries and
customer cancellation before enabling live payments. This version offers no
live mode even if `rzp_live_` credentials are supplied.

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
8. Razorpay Test Mode subscription code and migration are prepared; configure
   the restricted billing role, Modal Secret, actual plans, Test Mode webhook,
   and complete dashboard-to-database sandbox payment tests. Implement refunds,
   cancellation, invoicing, reconciliation and compliance policy before a
   separate reviewed live-payment release.

Staging proves the shared workspace/auth/queue foundation, but is not yet
a production-grade customer outreach service.
