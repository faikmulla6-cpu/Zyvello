# Zyvello production-readiness gate audit — 12 November 2026

**Release state: NO-GO**. Business model approved: anyone may create a verified
free account and up to the configured workspace quota, explore the interface,
and use billing; **all operational features require independently verified,
active, paid entitlement**. Production public cutover, live payments, outbound
email, and AI execution stay off. Public GitHub repository remains public and
draft PR #1 remains unmerged.

## Verified in staging / code

- Email/password signup enabled in staging Neon Managed Auth; email
  verification required; password-reset revocation configured.
- Managed Auth JWT EdDSA verification plus database-side post-reset timestamp
  and session checks, with least-privilege API role. Browser-session removal
  passed. Real old-JWT replay while unexpired **not yet run**.
- Tenant RLS isolation checked with two existing workspace identities via
  restricted SQL; actual two-account HTTP bearer smoke still needs two fresh
  authorized JWTs.
- API operational routes are paid-gated, including contacts, campaigns, jobs,
  draft generation and credentials. Free workspaces can read metadata and
  billing. Entitlement rechecked per request from signed-webhook-backed ledger.
- Staging web bundle rebuilt from frontend/dist and served at its real asset
  URLs. Live smoke confirmed current bundle, security headers, anonymous denial,
  readiness gate 503 and disabled external sending and live charging.
- Captured payment webhook reconciliation now provides independent signed
  provider payment evidence even when a browser checkout callback is lost.
  Wrong-amount, uncaptured, invalid-ID events never provide entitlement.
  This is a tested **code path**, not an observed actual Razorpay renewal.
- Worker paid-entitlement database function v9 verified under separate
  zyvello_worker_staging role, without raw billing SELECT. All background jobs
  require paid entitlement; AI work rechecks before model call and persistence.
  The function was tested first on isolated development branch.
- Python regression and targeted tests pass locally. GitHub cross-platform
  CI runs are release gates and must be green on the final commit.
- Existing manual encrypted database export and isolated restore were
  previously rehearsed. This is not automated offsite recovery.

## Open launch blockers — do not label these complete

**P0: Actual product usefulness / deliveries**
- Production Gmail/Outlook delegated OAuth, consent-scoped mailbox connection,
  token encryption, safe reconnect and disconnection, human-approved sending,
  retries, unsubscribe/opt-out, bounce/complaint and suppression handling
  are not end-to-end verified. Do not send real outreach to third parties.
- Real customer value workflow (business onboarding, campaign creation,
  prospect data provenance, reviewable AI drafts, deliverability measurement,
  reporting) is not yet demonstrated with real authenticated accounts.
- BYOK AI generator is opt-in/disabled; provider key rotation and token
  budget caps, queue backpressure, failure recovery and model cost control
  need evidence before AI activation.

**P0: Real billing & self-service**
- Razorpay is test-mode only. Complete real TEST checkout with signed webhook,
  first captured charge, authorized renewal, failed charge, expired period,
  cancellation, duplicate/out-of-order webhooks, refund/dispute, and recovery
  after lost browser callback. No actual test charge is claimed here.
- Add safe customer-initiated subscription cancellation, billing retries,
  abandoned creating-intent reconciliation, billing portal/invoices/receipts,
  accepted plan/price catalog, and accurate subscription-status messages.
- Before LIVE mode: merchant KYC and policy approval, taxation/invoicing,
  refund/cancellation terms, consumer disclosures, production secrets and
  webhook rollout with an independently verified pilot.

**P0: Authentication/security & operations**
- Replay a real unexpired pre-reset JWT and a real new JWT against the
  staging API. Verify two live bearer identities and cross-tenant 403/404.
- Stress-test public signup, workspace creation, auth and API rate limiting;
  CAPTCHA/abuse prevention, quota enforcement, invite/share roles and
  malicious retries. Managed Auth creates public accounts intentionally.
- Harden production auth trusted origins and CORS, disable unnecessary
  localhost origins, validate account deletion and data export flows.
- Implement scheduled encrypted **offsite** backups, retention, monitored
  failure alarms, fresh isolated restore, recovery time objective, incident
  playbook, uptime alerts and spending caps. Manual-only backups are not enough.

**P1: Public business launch**
- Publish accurate Terms, Privacy, refund/cancellation and support pages;
  decide data retention, DPDP/commercial email compliance and cross-border
  processing. Set abuse handling and contact/data-subject deletion policy.
- Configure own production domain/DNS/HTTPS, accessible responsive onboarding
  and pricing, meaningful empty states, contact/support, privacy and QA of
  page navigation. These are the requested later frontend-view tasks.
- Run load tests, dependency scans, secret scans, DB performance and
  concurrency checks, branch rollback rehearsal and production change freeze.

## Non-negotiable deployment rules

- Keep `zyvello.pro` unchanged until owner approves final cutover.
- Keep outbound sending and AI off pending controls and user-visible consent.
- Keep Razorpay live keys and real charges disabled pending all payment gates.
- Keep PR #1 as draft and repository public unless owner explicitly changes
  those decisions.
- Do not call a synthetic unit test an end-to-end provider test. Capture
  receipts/response codes from provider and authenticated HTTP smoke only.
- No production-readiness certification until every P0 gate has dated,
  reproducible evidence.

**Highest next task:** verified, repeatable real Razorpay TEST subscription
lifecycle with a captured charge, signed webhook, renewal, failure, expiration,
cancellation, and entitlement switch observed through the real staging API.
