# Zyvello hosted SaaS — launch readiness (10 November 2026)

Status: **private Modal staging only / NOT APPROVED FOR LIVE PAYMENTS OR CUSTOMERS**.

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

- **Automatic monthly renewal testing intentionally skipped at the owner's request.** This does not disable actual recurring billing.
- Renewal charge failures, refunds, disputes/chargebacks, delayed/out-of-order delivery against the deployed ledger, and end-of-cycle cancellation need an agreed operational policy before taking live money.
- Complete Neon Auth recovery, session revocation, multiple invited users, and two independently authenticated customer sessions must be exercised end-to-end in the browser. Earlier synthetic two-tenant RLS tests passed, but they are not a substitute for these flows.
- Customer-facing outreach, mailbox connections, permission/consent handling, AI key storage/calls, sending controls, and provider rate-limit recovery are **disabled in hosted staging**; the existing local Groq, Claude, and Codex integrations are preserved, not ported into the multi-tenant runtime.
- Background processing currently runs only a safe workspace-health diagnostic. Sending jobs, queue starvation/restarts at customer load, and retry/reconciliation alerts are not production-proven.
- Actual backup restore, automated alerts, ongoing cost/compute monitoring, retention/erasure workflows, privacy/refund/terms disclosures, tax/compliance review, and rollback rehearsal have not been evidenced.
- The general legacy test suite was not runnable in the Windows checkout because `pytest` is missing from the selected Python interpreter. This is an environment gap, not a confirmed application failure.
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
