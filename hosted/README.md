# Hosted database foundation (staging only)

`migrations/0001_workspace_jobs.sql` creates a separate `zyvello` schema for
hosted workspace identities, membership roles and durable job metadata. It does
**not** migrate or replace the local SQLite database, and contains no secrets.

Neon Managed Auth owns its separate `neon_auth` schema. Store the authenticated
Neon Auth user ID in `auth_user_id`; never let the browser choose another user
ID or workspace without server-side membership verification.

The initial tables have PostgreSQL row-level security enabled with **no
policies**. This intentionally denies non-owner database roles until a
least-privilege API/worker role and tested policies are added. Database owners
can still bypass this preliminary RLS configuration; it is *not* a substitute
for request-level workspace authorization. Do not connect the production API
using the database owner credential.

Job rows are inert. No worker consumes the queue yet, and no sending or reply
handling has changed. Before activating workers, implement transactional job
claims with leases, idempotent side effects, retry/dead-letter handling,
workspace-scoped credential access, and crash-recovery tests.

This database is an implementation step, not a release gate pass. The current
Modal `/readyz` endpoint must continue returning 503.

## Hosted identity preview

The React/Vite application has a separate hosted login, signup, email-OTP
verification and session/logout UI, gated by `VITE_ZYVELLO_HOSTED_MODE=true`.
Its default hosted signup switch is **off**; Neon has public signup disabled
too. The original local-owner login and provider adapters are preserved.

Build the staging frontend only with environment values from
`frontend/.env.hosted.example`, replacing the placeholder with the *public*
Neon Auth Base URL. Never place a Neon database password or service credential
in a `VITE_` variable. The frontend's `/app` route is currently only a
safe authenticated holding screen, not a working customer workspace.

The Modal staging API offers `GET /api/v1/me`. It requires a bearer JWT
signed by Neon Managed Auth's Ed25519 key and validates issuer, audience,
expiry and verified email through the public JWKS endpoint. It returns user
identity only; **it grants no membership, access to customer data or jobs**.
Missing/invalid tokens return 401, and JWKS unavailability returns 503.

Once the staging build is deployed with optional static preview assets,
`/login`, `/signup`, `/app` and `/welcome` serve the frontend. HTML is marked
noindex and never cached. Public registration remains disabled.

Remaining requirements: provision a staging test user/invite, configure and
verify trusted origins for the staging browser, exercise sign-in, OTP, session,
logout, expiry and revocation against real Neon, create a least-privilege DB
role, enforce workspace memberships, connect a database-backed API, then
test durable worker jobs. Do not call the staged identity API production ready.
