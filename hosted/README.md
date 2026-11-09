# Hosted database foundation (staging only)

`migrations/0001_workspace_jobs.sql` creates a separate `zyvello` schema for
hosted workspace identities, membership roles and durable job metadata. It does
**not** migrate or replace the local SQLite database, and contains no secrets.

Neon Managed Auth owns its separate `neon_auth` schema. Store the authenticated
Neon Auth user ID in `auth_user_id`; never let the browser choose another user
ID or workspace without server-side membership verification.

Migration `0001` enables RLS on the operational tables with no policies by
default. Migration `0002_request_scoped_rls.sql` additionally protects migration
history and allows **read-only** access for the non-owner `zyvello_api_staging`
role only when an authenticated user belongs to the workspace. It grants no
customer mutation permissions. The API role cannot create objects in the
`zyvello` schema or read the `neon_auth` schema.

The API must verify a real Neon JWT first and run a **parameterized**
`SELECT set_config('zyvello.auth_user_id', $1, true)` with the verified JWT
subject inside the same SQL transaction as its data queries. Never interpolate
identity strings into SQL. This application-controlled setting is **not itself a secure
identity credential**. Someone with direct access to the database login can
set it to another ID; never expose a database connection string to the browser
or use it as a substitute for API-side JWT checks. Database owners bypass the
current RLS configuration; do not use the owner credential in the hosted API.

Migration 0002 was first tested on the dedicated Neon
`dev-tenant-rls-20261109` branch using two synthetic users, workspaces and
inert `test_noop` jobs. The API role saw only the matching workspace/job for
each identity and no tenant rows when identity was unset. It saw no migration
history and had zero non-SELECT RLS policies. These are database tests, not
end-to-end customer login or API authorization tests.

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

Remaining requirements: provision a staging test user/invite, exercise sign-in,
OTP, session, logout, expiry and revocation against real Neon, connect the
JWT-verified request context to a database-backed API, and then test the entire
tenant boundary from an authenticated browser. Build separate restricted worker
permissions and durable jobs only after that. Do not call the staged identity
API production ready.
