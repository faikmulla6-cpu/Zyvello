# Zyvello staging-to-production release runbook

Status: **draft procedure only; do not deploy to customers or enable real charges.**
Owner: Faik. Updated: 10 November 2026. Branch: `hosted-saas-foundation` (draft PR #1).

## Current verified baseline

- Windows isolated full pytest: **1,541 passed, 184 skipped, 0 failed** on 10 Nov 2026; dependency deprecations remain.
- Read-only deployed Modal smoke: **seven checks passed** after the hosted UI rebuild, including 401 without auth, safe headers, disabled AI/sending/payments, and `/readyz` 503. A new check rejects a legacy single-owner frontend bundle.
- Neon migrations 0006/0007 were tested on a child branch, then **applied to active Zyvello staging after approval**. Snapshot `snap-withered-pond-ao17zdul` preceded the update; active schema version is 7 with restricted-role verification.
- Zyvello-only Google project `zyvello-outreach` has Gmail API enabled and External OAuth branding in Testing with only `gmail.send`. No OAuth client, refresh token, real Groq request, mail send, recovery-email receipt, or scheduled Razorpay renewal has been evidenced.
- Hostinger `zyvello.pro` is not part of this private-branch deployment. Original desktop working tree contains owner changes; preserve those changes.

## Release sequencing (must not skip)

1. Confirm a restorable Neon backup and document database branch/project identifiers. Request targeted approval before changing the active Zyvello staging schema.
2. Promote `hosted/migrations/0006_provider_secrets_and_consent.sql` only after approving/completing the prepared Neon migration; independently validate API cipher-column denial, worker decryption access, append-only permission ledger, cross-tenant isolation, and rollback feasibility.
3. Apply `hosted/migrations/0007_groq_draft_jobs.sql` to that same active **staging** branch in a transaction, check all grants and RLS, and seed disposable test workspaces through official APIs.
4. Store **only a new rotated** Groq API key in authorized server-side Modal secret management. Keep `ZYVELLO_ENABLE_AI_DRAFTS=false` until a real, bounded draft generation and provider-rate-limit test works. Configure `ZYVELLO_CREDENTIAL_ENCRYPTION_KEYS` securely for both API and worker, with a documented key-rotation procedure and separate role-scoped database URLs. Never expose credentials in browser bundles or git.
5. Deploy the vetted `modal_zyvello_staging.py` revision to the separate `zyvello` Modal workspace, not ClipKaro. Before reloading public traffic, verify `GET /healthz`, denied anonymous `GET /api/v1/me`, tenant-scoped credential/draft endpoints, queue lease recovery, no sends, no charges, and `GET /readyz` still guarded.
6. Create a separate Google Cloud Zyvello project under the correct owner after action-specific approval. Configure OAuth brand, redirect URI, minimal Gmail `gmail.send` permission, verification, token encryption/revocation, per-workspace mailbox isolation, unsubscribe/suppression, and sending limits. No real outreach until end-to-end verified permission and mailbox tests.
7. Configure a reputable custom SMTP sender for Neon Auth and verify legitimate signup invitation, OTP, password reset, inbox receipt, session invalidation, and abuse rate limits using two distinct accounts and real mailboxes. Restrict production trusted origins, disable development localhost origins, and perform real tenant-isolation tests.
8. Test the Razorpay **Test Mode** checkout state machine including webhook authenticity, idempotency, initial payment, cancellation, genuinely scheduled renewal, payment failure, refund/dispute policy, and replay. Do **not** enable automatic real charges or paste live keys until owner has approved the commercial terms and final release.
9. Demonstrate backup restore, incident alerts, billing/compute budget guardrails, worker crash recovery, privacy/commercial disclosures for India, and a rollback rehearsal.
10. Only after these pass, obtain a separate explicit release/cutover approval. Switch trusted origins and public DNS/Hostinger routing as a controlled change. Check user login, dashboard, health, consent, provider setup, payment, and worker status from an external client. Keep a documented manual kill switch for outbound sends and payment initiation.

## Commands and safety checks

From a **clean** Zyvello repository checkout with the project's dedicated Modal credentials:

- Local test: `.venv-test/Scripts/python.exe -m pytest -q --basetemp=pytest-ops-20261110` on Windows, or an equivalent isolated virtual environment.
- Build the **hosted** staging frontend before deploying Modal. In PowerShell from `frontend/`, set `VITE_ZYVELLO_HOSTED_MODE=true`, `VITE_ZYVELLO_ENABLE_SIGNUP=false`, and `VITE_NEON_AUTH_URL` to the *public staging* Neon Auth URL, then execute `npm.cmd ci` and `npm.cmd run build:app`. A default build without these variables ships the wrong single-owner login UI; this failure was observed and corrected on 10 Nov.
- Read-only deployed smoke: `python hosted/ci/smoke_staging.py`, which checks the hosted login JS bundle as well as six API/feature-gate responses. Also inspect `/login`, `/signup`, and unauthenticated `/app` in a browser.
- Review the current revision: `git diff --check`, `git status --short`, and `git log -1 --oneline`.
- Inspect the active Neon branch, migration table, role grants, and secret names **without printing secret values**.
- Deploy to staging only after both schema versions and the role/API checks pass; never blindly redeploy the current branch to a database missing 0006 or 0007.

## Incident and rollback decision tree

- **Authorization/tenant leak:** stop related API traffic and disable associated feature flags immediately; preserve audit evidence; revoke exposed keys; restore only after cross-tenant regression passes.
- **Provider key leaked:** revoke at the provider, rotate the Modal secret, redeploy the affected server-side function; never log or reveal the value.
- **AI queue rate/timeout:** disable `ZYVELLO_ENABLE_AI_DRAFTS`, preserve idempotent jobs, and retry only according to recorded lease state.
- **Outreach consent problem:** leave outbound sending disabled; suppress affected contacts; audit permission events before any restart.
- **Webhook/payment mismatch:** disable checkout initiation; reconcile against signed Razorpay events and provider dashboard; never fabricate ledger success or issue live refunds without explicit owner decision.
- **Schema migration failure:** stop deployment and isolate the failure; use the verified database restore/branch procedure, not ad-hoc destructive SQL. App rollback must be compatible with the actual schema version.
- **Unexpected production readiness 200 before sign-off:** stop rollout and restore the launch gate; `/readyz` must remain 503 until all gates are met.

## Approvals still required

- **Already approved and completed:** active Neon staging migrations 0006/0007 (after snapshot) and separate Google Cloud project + Gmail API + Testing-mode OAuth consent.
- Still pending: verified privacy/terms pages and a secure callback/token store before creating a Gmail OAuth client, consenting test users, or requesting broader permissions. Do not enable real sending yet.
- Verified custom SMTP credentials and domain DNS sender authentication (SPF/DKIM/DMARC).
- Actual rotated provider API credentials, stored server-side only.
- Final live payment and public customer cutover approval.
