# Zyvello Step 3 — subscriptions and payments (13 November 2026)

## Scope and release boundary

**Razorpay TEST mode only.** No live charges, production keys, payment
collection, automatic outgoing email, or AI jobs are enabled. The hosted
staging app continues to advertise `payments_enabled=false`. A passing
synthetic test does NOT prove a real Razorpay payment or renewal.

This increment builds on the hosted workspace implementation and uses
a **separate** migration version 11, not draft PR #2's conflicting version
10. It depends on existing workspace migration version 10.

## Existing, previously confirmed

- Dedicated workspace billing tables, signed provider webhook ledger,
  request-replay/idempotency guards, and restricted billing role.
- Razorpay TEST checkout and signed callback verification, with payment
  entitlement requiring a confirmed captured charge from the provider,
  a currently active subscription and an unexpired paid period.
- Provider cancellation API exists and is owner-only, requesting
  `cancel_at_cycle_end=true`. Final state comes from signed webhooks.
- Actual Razorpay TEST plan, a previous captured charge and subsequent
  cancellation were observed in the earlier read-only provider audit.
- Two currently cancelled staging subscriptions: neither entitled. The
  second unauthorised TEST checkout encountered a provider human challenge
  and was safely cancelled without a charge.

## Changes in this increment

1. Database migration `hosted/migrations/0011_paid_feature_rls.sql`
   defines `zyvello.is_api_workspace_entitled(uuid)`: an independently
   scoped membership check plus active, verified, paid, unexpired,
   provider-linked subscription. Execute is available only to the API role.
2. Enforces entitlement at the database for paid API writes: contacts,
   campaigns, campaign audiences, health/AI draft jobs, and provider
   credential writes. No change to free account onboarding, workspace
   management, billing checkout, consent opt-out cleanup or credential
   deletion. Worker-generated drafts additionally check current worker
   entitlement at insertion.
3. Hosted API uses **the same database entitlement predicate** to ensure
   the HTTP gate and SQL write gate agree; billing list computes the same
   conditions.
4. A halted subscription is treated as recoverable and blocks a second
   checkout to prevent double recurring charges while payment recovery is
   unresolved.
5. Hosted dashboard adds an explicit two-step owner-only TEST subscription
   cancellation control, never requesting immediate cancellation and never
   assuming the browser has changed subscription state. Status messages
   distinguish pending, halted, creating, cancelled, expired and active
   but unverified subscriptions.
6. Added a disposable PostgreSQL CI security suite testing free access
   denied, paid access granted (synthetic only), cross-tenant denial,
   cancellation revocation and expiration revocation. Added a regression
   for old captured-charge events arriving after a newer pending event.

## Validation and rollout gates

- `npm run build:hosted` TypeScript/Vite preflight passed with staging
  configuration on the Step 3 worktree.
- Targeted hosted/workspace/payment contract tests passed (42 tests and
  32 subtests before final newly added regression).
- Schema v11 applied successfully to the isolated Neon dev clone; both
  existing staging QA principal contexts returned no entitlement and the
  restricted API role has no worker-granted access to its new function.
- New disposable PostgreSQL CI test must pass with migrations 1–7, 9, 11;
  do not claim real payment performance from synthetic rows.
- **Before active staging v11 rollout:** all GitHub CI checks green, new
  pre-migration snapshot, no user workspace/billing record changes,
  migration prerequisite version10, after-migration read-back,
  staging Modal deployment and HTTP smoke.
- **Before live payment release:** user-approved plan/catalog/price and
  cancellation/refund policy, Razorpay merchant KYC, invoice/GST and
  privacy terms, webhook reconciliation and manual TEST-dashboard
  successful/failed renewal, refund/dispute handling, reliable support
  procedures and user acceptance via two authenticated accounts.
- The TEST Checkout requires a human to complete provider challenge when
  presented. Do not bypass security controls, collect credentials in
  GitHub, or claim an automated browser exercise as a captured payment.

## Explicit business decisions still needed

- Final plan names, INR prices, monthly/yearly options, features/quotas,
  number of members and workspaces per plan.
- What happens on payment failure: immediately restrict paid features
  (current fail-closed behavior) or allow a defined grace period.
- Refund / cancellation policy, tax invoices, receipts, contact support
  and customer data export/retention timelines.
- Whether a cancelled subscription ends immediately or at the provider
  billing-cycle boundary (currently provider `cancel_at_cycle_end=true`,
  and the signed status is authoritative).

The standalone previous paid-entitlement PR #2 is NOT applied; this
migration number 11 takes the place of those database policy changes.
