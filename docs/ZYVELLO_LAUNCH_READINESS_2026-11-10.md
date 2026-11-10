# Zyvello hosted SaaS — launch readiness (10 November 2026)

Status: **private Modal staging only / NOT APPROVED FOR LIVE PAYMENTS OR CUSTOMERS**.

## Subsequent draft/consent foundation — code reviewed, not yet promoted

- Added migration 0006 for encrypted per-workspace Groq credentials and an
  append-only contact permission ledger. Imported contacts start with
  `unknown` permission. `claimed_consent` does not authorize delivery, and a
  prior opt-out or suppression cannot be overridden through the API.
- Added migration 0007 for isolated, idempotent Groq draft jobs and
  worker-written drafts. The API limits enqueues to managers, 20 per workspace
  per 24 hours, and 5 simultaneous pending jobs. The worker has read access
  only to selected contact fields (no email addresses); it can generate
  drafts but has no email sending or mailbox permissions.
- The credentials API encrypts API keys using a server-supplied Fernet
  keyring, supports encryption-key rotation, and never returns ciphertext or
  plaintext to browser code. The API database role cannot read ciphertext.
- The Groq adapter has a fixed official endpoint and an allowlisted model,
  bounded timeouts/tokens, sanitized errors, and no externally supplied URL.
  The pasted key in the owner conversation must be revoked; do not place it
  into Modal secrets or code. Only a freshly rotated key is acceptable.
- Unit suite: **40 hosted tests passed**, including key rotation, malicious
  ciphertext, tenant denial, consent enforcement, idempotent draft scheduling,
  and mock Groq provider failure paths. No real provider call was made.
- On Neon temporary child branch `br-blue-hall-ao5fb5n8`, migration 0006 was
  prepared successfully and migration 0007 applied transactionally for tests.
  Actual API and worker role SQL confirmed: API cannot SELECT ciphertext;
  worker cannot SELECT contact email; cross-tenant API draft read returns zero;
  worker can insert drafts with matching workspace/job foreign keys.
- **Neither migration has been promoted to the active staging database.** A
  targeted approval is required to complete the prepared migration, after
  which the second migration and end-to-end staging tests must follow. The
  hosted AI flag stays off, and no key has been installed. Do not deploy code
  with new API endpoints before its schema is present.
- Gmail mailbox OAuth has not been configured. Use the narrow Gmail `gmail.send`
  scope and complete Google's sensitive-scope verification where required.
  Do not collect Gmail refresh tokens before the consent screen, redirect URI,
  ownership, encrypted token storage, revocation, and unsubscribe handling
  are implemented and tested.

## Subsequent security and renewal work — deployed to Modal staging

- Hardened the Razorpay webhook request path with bounded streaming before
  signature verification, rather than unbounded buffering of a forged payload.
- Added `no-store` API caching and response security headers (no sniffing,
  framing, camera/microphone/geolocation permissions, or referrer leakage).
- Added a tested billing event reconciliation function: captured second-period
  renewals can extend access, unsuccessful renewals cannot extend it, older
  events cannot roll periods backward, and `cancelled`/`completed`/`expired`
  states cannot be reactivated by a later charged webhook.
- **32 isolated hosted tests passed locally**, including six new billing
  lifecycle cases and API security-header checks. These are deterministic
  simulations, **not** a second live Test Mode renewal capture.
- Added a hosted account password-recovery UI using Neon's documented
  `forgetPassword.emailOtp` and `emailOtp.resetPassword` client methods. The
  TypeScript and hosted Vite production build passed. The deployed browser
  shows the recovery entry point and email form, but an actual email receipt
  and password reset are still to be tested with an approved account.
- The user has now authorized automatic-renewal testing in Razorpay **Test
  Mode**. An actual second scheduled monthly charge, provider retry schedule,
  refund/chargeback handling, and deployed-database replay remain unverified.
- The **complete legacy-plus-hosted pytest suite** passed in an isolated
  desktop Python environment: **1,533 passed, 184 skipped, 0 failed** (5m38s).
  Skipped cases are not counted as passing; this was a local suite, not the
  GitHub-hosted CI pipeline or production environment.
- The revised `zyvello-staging` app deployed successfully in Modal. Live
  staging HTTP returned **401** for unauthenticated `/api/v1/me`, with
  `Cache-Control: no-store`, `X-Frame-Options: DENY`, and no-sniff/referrer/
  permissions headers. `/api/v1/capabilities` confirms no AI execution,
  outbound sending, or live charging. `/readyz` remains **503 by design**;
  the launch blockers have not been lifted.
- Neon Auth branch settings were inspected again: signup is disabled,
  verification is required, and `revoke_sessions_on_password_reset=true`.
  **Production blockers:** the auth email provider is still Neon's shared
  developer SMTP, the trusted origin list contains only the Modal staging URL,
  and `allow_localhost=true`. Configure and verify these appropriately before
  a production domain cutover; do not accidentally change this staging auth
  branch while customers depend on it.

## Verified on the staging path

- Neon Managed Auth session opened an owner workspace in the deployed hosted UI.
- JWT-verified API requires bearer authentication; local-owner endpoints are not exposed by the hosted app.
- Nine `zyvello` tables have RLS enabled; API, worker, and billing roles are not superusers and cannot bypass RLS.
- Restricted billing role cannot read customer contacts, campaigns, memberships, or jobs. Worker role cannot read billing data.
- Safe workspace-health jobs have succeeded using a durable database-backed worker. No AI or outbound email is activated.
- Razorpay **Test Mode** Agency subscription (₹4,999 monthly) completed through checkout; signed activation and captured-charge webhooks applied to Neon. A subsequent immediate Test Mode cancellation applied `subscription.cancelled`; UI correctly revoked paid access despite a future `paid_through` date.
- Staging webhook rejects unsigned POST with HTTP 400. API unit tests cover HMAC tampering, replay of the same event ID, malformed event ID, payload size limit, and failed/uncaptured payment payloads.
- Hosted-focused Python suite: **25 passing tests** on local and Windows staging clone. React/TypeScript/Vite hosted build: **passed**.
- Billing now offers a manual refresh and briefly polls for signed webhook settlement after checkout, instead of showing stale entitlements indefinitely.

## Explicitly not yet verified or not ready

- Automatic monthly renewal **was previously skipped**, but the owner has
  explicitly authorized Test Mode renewal tests now. A synthetic event replay
  must not be described as a genuine second Razorpay renewal charge.
- Renewal charge failures, refunds, disputes/chargebacks, delayed/out-of-order delivery against the deployed ledger, and end-of-cycle cancellation need an agreed operational policy before taking live money.
- Complete Neon Auth recovery, session revocation, multiple invited users, and two independently authenticated customer sessions must be exercised end-to-end in the browser. Earlier synthetic two-tenant RLS tests passed, but they are not a substitute for these flows.
- Customer-facing outreach, mailbox connections, permission/consent handling, AI key storage/calls, sending controls, and provider rate-limit recovery are **disabled in hosted staging**; the existing local Groq, Claude, and Codex integrations are preserved, not ported into the multi-tenant runtime.
- Background processing currently runs only a safe workspace-health diagnostic. Sending jobs, queue starvation/restarts at customer load, and retry/reconciliation alerts are not production-proven.
- Actual backup restore, automated alerts, ongoing cost/compute monitoring, retention/erasure workflows, privacy/refund/terms disclosures, tax/compliance review, and rollback rehearsal have not been evidenced.
- Earlier attempts to run the full suite in the everyday Python environment
  failed during collection because `pytz` and `aiosmtplib` were absent and the
  default pytest temp folder was inaccessible. Both environment issues were
  avoided in a throwaway virtual environment and explicit temp directory; the
  full suite passed there. GitHub Actions remains blocked by account billing,
  so a green hosted CI check is not available.
- `/readyz` deliberately returns 503 while outbound and recovery controls remain disabled. Do not use staging liveness as proof of production readiness.

## Production promotion gates

1. Keep Hostinger's live website and DNS unchanged until all customer-facing features and account recovery pass end-to-end tests in staging.
2. Verify least-privilege DB roles, prepared migrations on an isolated Neon branch, RLS across actual two-account sessions, and a tested backup/restore.
3. Add and test safe per-workspace credentials, outbound consent, rate limits, queue recovery, deduplication, and AI/provider error handling without modifying the existing local provider integrations.
4. Define the India SaaS commercial terms and live Razorpay plan catalogue, operational response to failures/refunds/disputes, and switch to separate **live** credentials only in a reviewed release. Never copy Test Mode secrets or enable payment capture early.
5. Configure monitoring and budget alerts, document deployment/rollback, run a release smoke test, then approve a controlled customer rollout.

## Known staging deployment

- Modal staging app: `zyvello-staging` in the `zyvello` workspace, endpoint `https://zyvello--zyvello-staging-web.us-east.modal.run`.
- Database: dedicated Zyvello staging Neon project `raspy-sea-04857665`, branch `br-ancient-credit-aownmkn7`.
- GitHub: draft PR #1, `hosted-saas-foundation` → `main` in `faikmulla6-cpu/Zyvello`. Do not merge before the gates above.
- Staging frontend must be built with `VITE_ZYVELLO_HOSTED_MODE=true`, the public Neon Auth URL, and `VITE_ZYVELLO_ENABLE_SIGNUP=false` before deployment. Store private DB/payment keys in Modal secrets; never embed them in browser bundles or this document.
