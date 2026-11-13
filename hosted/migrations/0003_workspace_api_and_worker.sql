-- Zyvello hosted API v0003. Apply to an isolated Neon branch before staging.
-- Both database roles must exist before applying this migration:
-- zyvello_api_staging (request JWT identity, scoped with SET LOCAL)
-- zyvello_worker_staging (trusted background worker, jobs table only).

ALTER TABLE zyvello.jobs
    ADD COLUMN IF NOT EXISTS result JSONB CHECK (result IS NULL OR jsonb_typeof(result) = 'object');
ALTER TABLE zyvello.jobs
    ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;

-- A verified user may create a workspace, but cannot impersonate its creator.
CREATE POLICY workspaces_api_insert_own ON zyvello.workspaces
    FOR INSERT TO zyvello_api_staging
    WITH CHECK (created_by_auth_user_id = zyvello.current_auth_user_id());

-- Membership creation happens in the same transaction as workspace creation.
-- The workspace is not SELECT-visible until this membership exists, so the
-- creator check must bypass the workspaces SELECT policy safely.
CREATE OR REPLACE FUNCTION zyvello.is_workspace_creator(p_workspace_id UUID)
RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT EXISTS (
        SELECT 1 FROM zyvello.workspaces AS w
        WHERE w.id = p_workspace_id
          AND w.created_by_auth_user_id = NULLIF(current_setting('zyvello.auth_user_id', true), '')
    )
$$;

REVOKE ALL ON FUNCTION zyvello.is_workspace_creator(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zyvello.is_workspace_creator(UUID) TO zyvello_api_staging;

CREATE POLICY memberships_api_insert_owner ON zyvello.memberships
    FOR INSERT TO zyvello_api_staging
    WITH CHECK (
        auth_user_id = zyvello.current_auth_user_id()
        AND role = 'owner'
        AND zyvello.is_workspace_creator(workspace_id)
    );

-- The only externally enqueuable job is an internal workspace health check.
-- AI, network prospecting, campaign sending and billing remain disabled.
CREATE POLICY jobs_api_insert_health ON zyvello.jobs
    FOR INSERT TO zyvello_api_staging
    WITH CHECK (
        zyvello.is_workspace_member(workspace_id)
        AND kind = 'workspace_health'
        AND status = 'queued'
        AND attempts = 0
        AND max_attempts <= 3
        AND leased_by IS NULL
        AND lease_until IS NULL
        AND last_error IS NULL
        AND result IS NULL
        AND finished_at IS NULL
    );

-- Worker can see and transition jobs for every tenant, but never gets
-- workspace, membership, identity, or schema migration table grants.
GRANT USAGE ON SCHEMA zyvello TO zyvello_worker_staging;
REVOKE ALL ON ALL TABLES IN SCHEMA zyvello FROM zyvello_worker_staging;
GRANT SELECT, UPDATE ON zyvello.jobs TO zyvello_worker_staging;

CREATE POLICY jobs_worker_read ON zyvello.jobs
    FOR SELECT TO zyvello_worker_staging USING (true);
CREATE POLICY jobs_worker_update ON zyvello.jobs
    FOR UPDATE TO zyvello_worker_staging USING (true) WITH CHECK (true);

INSERT INTO zyvello.schema_migrations(version) VALUES (3)
ON CONFLICT (version) DO NOTHING;
