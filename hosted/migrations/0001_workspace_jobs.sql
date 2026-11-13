-- Zyvello hosted SaaS core schema, version 0001.
-- Apply only to the dedicated Zyvello database, never ClipKaro's project.
-- No user registration, outbound sending or worker invocation is enabled here.

CREATE SCHEMA IF NOT EXISTS zyvello;

CREATE TABLE IF NOT EXISTS zyvello.schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS zyvello.workspaces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{2,62}$'),
    name TEXT NOT NULL CHECK (char_length(trim(name)) BETWEEN 1 AND 200),
    created_by_auth_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS zyvello.memberships (
    workspace_id UUID NOT NULL REFERENCES zyvello.workspaces(id) ON DELETE CASCADE,
    auth_user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, auth_user_id)
);

CREATE INDEX IF NOT EXISTS memberships_user_workspace_idx
    ON zyvello.memberships (auth_user_id, workspace_id);

CREATE TABLE IF NOT EXISTS zyvello.jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES zyvello.workspaces(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]{1,63}$'),
    idempotency_key TEXT NOT NULL CHECK (char_length(idempotency_key) BETWEEN 1 AND 200),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(payload) = 'object'),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'dead')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 20),
    run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until TIMESTAMPTZ,
    leased_by TEXT,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, kind, idempotency_key),
    CHECK ((status = 'running') = (lease_until IS NOT NULL AND leased_by IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS jobs_claim_idx
    ON zyvello.jobs (status, run_after, lease_until)
    WHERE status IN ('queued', 'running');

CREATE INDEX IF NOT EXISTS jobs_workspace_created_idx
    ON zyvello.jobs (workspace_id, created_at DESC);

-- Defense in depth: any future non-owner database role gets no access until
-- explicit policies have been designed and verified with multi-tenant tests.
ALTER TABLE zyvello.workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE zyvello.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE zyvello.jobs ENABLE ROW LEVEL SECURITY;

INSERT INTO zyvello.schema_migrations(version) VALUES (1)
ON CONFLICT (version) DO NOTHING;
