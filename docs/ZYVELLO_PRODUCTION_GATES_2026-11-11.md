# Zyvello production gate ledger — 11 November 2026

**Release decision: NO-GO for public customer cutover.** Continue using isolated
Modal staging. Keep public signup, AI execution, outbound email sending, and
live payments disabled. Do not merge draft PR #1 or replace Hostinger's live
`zyvello.pro` site until the remaining gates are verified and Faik gives the
separate cutover decision.

This ledger distinguishes live observations from local tests and items that
have not been proven. It supplements `ZYVELLO_RELEASE_RUNBOOK.md`.

## Evidence confirmed on 11 November

| Area | Observation | Meaning / limit |
| --- | --- | --- |
| Python regression | **1,708 passed, 25 skipped, 0 failed** in an isolated Linux Python environment after excluding injected SOCKS-proxy environment variables | Local suite including the new smoke tests passed; 25 skipped tests and hosted CI are not green checks |
| Hosted unit tests | 25 passed | Synthetic auth/storage/API contracts, not real bearer sessions |
| Two-account smoke tool | 8 local regression tests passed, including cross-tenant leak rejection and no redirect to unknown hosts | Real staging JWT replay/tampering remains pending |
| Frontend | Hosted-mode production TypeScript/Vite build passed with non-production placeholder Auth URL; built assets were not promoted | Deployment must be rebuilt with the actual approved public Auth URL before any publish |
| JavaScript production deps | `npm audit --omit=dev --audit-level=high` returned zero vulnerabilities | Does not guarantee a complete supply-chain security audit |
| Modal staging live UI/API | `/healthz` reports alive; `/login` displays hosted private-beta email OTP, password and recovery choices | Does not prove that accounts can still log in today |
| Live capability gate | `ai_enabled=false`, `outbound_sending_enabled=false`, `payments_enabled=false`, `razorpay_test_billing_ready=true` | Test billing may be configured, but production charging is off |
| Live readiness gate | `/readyz` returns `not_ready`, citing disabled outreach/scheduled recovery | Intentional; not a platform outage |
| Neon backup inventory | One manual pre-migration snapshot (`zyvello-pre-0006-0007-20261110`); active branch automatic snapshot schedule is empty; project subscription is `free_v3` | A current v7 snapshot and a successful isolated restore are **not** evidenced; scheduled Neon snapshots are unavailable on Free |
| Neon Auth config | Custom Resend SMTP, public signup disabled, reset-session revocation configured; trusted origins only include Modal staging, `allow_localhost=true` | Staging configuration, not final production-origin hardening |
| GitHub Actions | At draft branch commit `07252fd`, both `hosted-staging-checks` and `tests` completed successfully: three hosted jobs and all six OS/Python matrix jobs green | **CI execution blocker closed**. This does not prove live provider or auth-session flows |

## Previously verified 11 November by authenticated staging checks

- Neon Auth custom Resend SMTP from `no-reply@auth.zyvello.pro` delivered
  messages to two distinct invited email accounts. OTP login worked for both.
- Account A and Account B had distinct owner workspaces; restricted database
  role and browser checks demonstrated workspace/contact isolation.
- Account A password reset and a subsequent credential rotation succeeded;
  the user confirmed the publicly pasted intermediate password was rotated.
- **Important qualification:** active pre-reset session revocation was not
  exercised. `revoke_sessions_on_password_reset=true` is a configuration
  inspection, not a substitute for that test.
- This historic evidence does not mean the currently running revision has
  passed two independent, real-JWT HTTP requests after today's code changes.

## New repeatable two-token API gate

`hosted/ci/smoke_tenant_authorization.py` makes **GET requests only** against
the pinned staging origin; it rejects redirects, prints no JWT or customer
fields, and never sends email, queues jobs, or changes billing.

To run it, obtain fresh, different, short-lived **access** JWTs from the two
existing test accounts through an authorized secure channel. Set
`ZYVELLO_TEST_TOKEN_A` and `ZYVELLO_TEST_TOKEN_B` in the local process
environment without putting token values in source, CI logs, terminal command
history, screenshots, GitHub comments or chat. If an account owns multiple
workspaces, also set `ZYVELLO_TEST_WORKSPACE_A` or `_B` to its selected UUID.

Then run:

```sh
python hosted/ci/smoke_tenant_authorization.py
```

Acceptance requires two distinct verified subjects and disjoint workspace
memberships. For each account, reads of its own workspace and jobs, contacts,
campaigns and drafts must be HTTP 200; cross-workspace reads must be HTTP
403/404. The encrypted-key status and test-billing routes are checked when
enabled. HTTP 503 for an optional disabled service is **skipped**, not passed.
All responses must include `Cache-Control: no-store`. The script exits nonzero
on leaks or unexpected response codes.

## Release blockers still open

1. **Auth:** execute real dual-JWT API smoke and active-session revocation
   after password reset; verify trusted origins and rate limits for the final
   customer domain. Current staging `allow_localhost` must not leak to
   production.
2. **Credentials/AI:** revoke the previously shared Groq key, install only a
   rotated scoped key in a server-side secret, test encrypted storage, bounded
   genuine generation, throttling, timeouts and queue recovery. Keep AI off
   until a production-reviewed change.
3. **Gmail/OAuth/sending:** privacy and terms pages, verified domain and
   authorized redirects, token storage/revocation, mailbox-scoped permission,
   unsubscribe and suppression, quotas and a controlled end-to-end test.
   No live outreach to third parties during this gate.
4. **Payments:** actual Razorpay Test Mode second-cycle renewal and failure
   paths; replay/out-of-order handling on the deployed ledger; agreed
   cancellation, refund, dispute, tax/invoice and support policies. Never
   insert live keys or enable live charges as an unreviewed trial.
5. **Operations:** prove Neon backup **restore**, rollback rehearsal, alert
   delivery, compute/billing budget guardrails, retention/erasure workflow,
   incident response ownership and support contact.
6. **Final rollout:** recheck frontend's real Hosted Auth base URL and
   production CORS/trusted-origin configuration; controlled DNS/Hostinger
   cutover; public signup, AI, sends and money only as separately approved.

## GitHub visibility

The owner previously chose to leave the repository **public** during this
staging period after discussing an earlier desire to make it private. Do not
silently change repository visibility or claim it was made private. Obtain a
fresh explicit preference before any change. Never add API secrets, JWTs or
SMTP credentials to this public repo.

### Low-cost backup decision

Neon's 4 November 2026 [plan documentation](https://neon.com/docs/introduction/plans#snapshots)
states that Free includes one **manual** snapshot and does not support
automated scheduled snapshots. This project's direct read-back confirms it is
on Free and already has a pre-migration manual snapshot. Do **not** delete that
snapshot or promise free automated snapshots. Before public launch choose
either a paid Neon plan with an approved backup schedule and budget cap, or a
separately managed encrypted database export with documented retention and a
successful isolated restore rehearsal. Neither has been implemented yet.
