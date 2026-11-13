# Zyvello staging: access JWT revocation guard (2026-11-12)

**Scope: staging only.** Keep PR #1 draft, repository public, public cutover
blocked, outbound sending disabled, and live payment capture disabled.

## Purpose

Neon Managed Auth revokes opaque browser sessions during password reset, but
an EdDSA JWT with a remaining 15-minute lifetime can still verify offline.
The hosted API now performs an additional read-only database check on **every
authenticated endpoint**, including `/api/v1/me`. Failing this check yields
HTTP 401; inability to reach the database or execute the check yields HTTP 503.
The staging API has no fallback to signature-only authentication.

Database migration `0008_auth_session_revocation.sql` installs
`zyvello.is_auth_session_current(auth_user_id, issued_at)`, a boolean-only
security-definer function. The restricted `zyvello_api_staging` role can call
the function but cannot directly read `neon_auth` account or session data.
The function checks that:

- the user is still verified;
- at least one active Neon session already existed when the signed JWT was issued;
- a credential account was **not** updated after the JWT's issue second.

The last condition rejects tokens issued before the password-reset timestamp,
including ones whose EdDSA signatures and expiry are otherwise valid.

## Evidence and limits

- Two-account workspace isolation and revoked QA browser session were already
  observed against staging (see the release gate ledger).
- On an isolated Neon development branch, the restricted role executed the
  boolean check for an unknown identity, returning false.
- On the staging database, the same restricted role rejected a synthetic issue
  time preceding an active session and allowed a later issue time; it still had
  no `USAGE` on the `neon_auth` schema. No customer data was changed.
- Unit tests assert denial (401), database-failure fail-closed behavior (503),
  valid identities, and signing timestamp validation.
- The **real pre-reset JWT replay** is not proven until two live access tokens
  (one minted before reset, one after) are exercised while the older JWT is
  still naturally unexpired. The existing
  `hosted/ci/smoke_session_revocation.py` provides that read-only release gate.
- The managed JWT's documented default claims do not include a cryptographic
  session identifier. This check does **not** promise per-session logout
  revocation if another session of the same user stays active. It specifically
  addresses invalidation after a credential update and when all sessions end.
  If exact per-session JWT revocation becomes mandatory, use an issuer-provided
  session binding or redesign the token protocol.
- Strict second-resolution `iat` checking can briefly reject a newly minted
  token within the same second as a password update; retry authentication
  after a new issue-second rather than weakening the check.
- Neon Managed Auth's independent email-OTP `disableSignUp` server setting
  is not exposed by the available Neon configuration. The API's existing
  membership check prevents an uninvited identity from creating a workspace;
  this is *not* provider-level prevention of Auth user creation.

## Deployment order

1. Test migration 0008 on a Neon child branch.
2. Install the function, revoke PUBLIC EXECUTE, grant restricted API-role
   EXECUTE, and record the migration on the dedicated Zyvello staging database.
3. Run focused tests, the complete Python regression suite, and GitHub CI.
4. Deploy Modal staging; verify `/healthz` is 200, unauthenticated API requests
   are 401, `/readyz` stays intentionally 503, and live sending/paid checkout
   remain disabled.
5. Obtain fresh QA access tokens via an authorized test flow and run the
   two-account and pre/post-reset replay checks before closing final auth gates.
