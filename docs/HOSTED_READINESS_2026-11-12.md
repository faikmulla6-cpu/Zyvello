# Hosted Zyvello production gate review — 2026-11-12

Scope: read-only inspection of GitHub `main` and **Neon Zyvello Staging**. No payments,
emails, external outreach, production SQL, or live deployment were executed.

## Product behavior locked

- Anyone may register without an invitation.
- A newly registered account may authenticate and view the interface/workspace.
- Paid automation features must fail closed unless the **server** confirms an
  active, verified, currently paid subscription for that workspace.
- Each business has its own workspace; no cross-business contact, billing,
  job, export or credential access.
- Existing opt-out/suppression, workspace self-service and cleanup rights
  must survive payment cancellation.

## Observed, not inferred

1. Neon Auth has `email_password.enabled=true`, `allow_sign_up=true`,
   and both sign-up verification and required email verification enabled;
   OTP email and password reset were previously exercised separately.
2. Staging has nine recorded migrations, two test workspaces,
   two memberships, two billing subscription records, two jobs and
   three auth users (2026-11-12). Both subscription rows have
   `status='cancelled'`; **there is no confirmed active paid workspace**.
3. All 12 `zyvello` tables have row-level security enabled, but not forced.
   The API role without an authenticated user context saw zero workspaces,
   contacts and subscriptions.
4. With a transaction-local test user context, the API role saw exactly
   its own workspace and membership for each of two staging principals.
   One principal saw 1 contact, 2 subscriptions and 2 jobs, all belonging
   to that principal's workspace; the other saw none.
   These are **read isolation checks only**, not exhaustive mutation tests.
5. An existing worker entitlement function requires an active subscription,
   verified checkout, positive paid count, and an unexpired paid period.
6. Before this patch, API policy `jobs_api_insert_draft` let a workspace
   manager enqueue `groq_draft` without an entitlement predicate. API
   policies also allowed premium contact/campaign creation without it.
   This is the concrete gap addressed by migration 010.
7. The inspected GitHub tree remains a local single-owner Python application,
   with a Vite/React frontend. No deployable hosted customer API, hosted worker
   or schema migrations for the Neon staging tables were found in that tree.
   A separate staging web URL is trusted by Neon Auth; its implementation
   was **not** inspected or externally verified.

## Change proposed — NOT APPLIED

`sql/staging/010_paid_feature_gates.sql` introduces a tenant-aware,
privileged entitlement predicate callable only by the API role and adds
paid-state requirements to premium inserts: contacts, campaigns, campaign
contacts, Groq drafting jobs, workspace health jobs, provider credentials,
and generated-draft persistence by the worker.

The migration intentionally leaves sign-up, workspace creation, subscription
checkout initiation, cancellation cleanup, and opt-out records usable without
an active paid plan. It must be exercised first on an isolated Neon branch.
No main database settings, policies or live subscriptions were altered.

## Required validation before merging or deploying

- On a temporary clone of staging, install migration 010 with the schema
  owner. Confirm that the API role cannot submit premium work for both an
  unpaid workspace and an expired/cancelled workspace.
- In the same clone, create a **synthetic** active subscription using a
  deliberately controlled billing fixture, then confirm only its workspace
  can create premium records and that cancellation/expiry immediately
  disables future paid writes. Roll back/delete the test branch after checks.
- Verify member vs manager permissions, cross-tenant `INSERT`, `SELECT`,
  `UPDATE`, `DELETE`, foreign-key integrity and `SECURITY DEFINER` grants;
  ensure API connections never use the database owner role.
- Verify backend sets `zyvello.auth_user_id` as `SET LOCAL` **only after**
  validating a Neon Auth JWT/session; never accept client-supplied IDs or
  workspace IDs as sufficient proof of identity.
- Verify worker checks entitlement **before** spending AI/API credits,
  retries after crashes, leases, idempotency and stale payment state.
- Implement server-authoritative plan IDs, prices and quotas; verify
  payment-provider webhook HMAC signatures, event deduplication, ordering,
  subscription cancellation and refund effects. Never grant paid access
  on client checkout success alone.
- E2E: open signup -> verified login -> unpaid dashboard -> payment
  test mode -> paid workspace -> isolated campaign -> paused/cancelled
  subscription -> access revoked -> account/data export/opt-out.
- Validate secret encryption/key rotation, real recovery/restore, runtime
  deployment, HTTPS trusted origins, alerts, logging, tests, and rollback.
- Run controlled outbound email tests, opt-out handling, reply recovery and
  approval-mode safeguards before unattended customer email automation.

## Release decision

**NOT PRODUCTION READY.** The patch closes one verified class of database
paywall bypass **when reviewed, tested and applied**. Live payment processing,
hosted API wiring and end-to-end authorization remain unverified. Do not
activate paid checkout or autonomous sending based on this review.
