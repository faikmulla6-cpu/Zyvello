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
