-- Zyvello hosted SaaS staging RLS, version 0002.
-- Prerequisite: provision a non-owner PostgreSQL login named
-- zyvello_api_staging with DML privileges limited to the zyvello schema.
-- Application MUST verify the Neon Auth EdDSA JWT first, then run API SQL in
-- ONE transaction beginning with SET LOCAL zyvello.auth_user_id = <JWT sub>.
-- Never hand a database login or arbitrary SQL execution to a browser.

-- This metadata table was missing RLS in 0001; the scoped API login would
-- otherwise be able to change applied migration history.
ALTER TABLE zyvello.schema_migrations ENABLE ROW LEVEL SECURITY;

-- Missing or empty request identity means no tenant access.
CREATE OR REPLACE FUNCTION zyvello.current_auth_user_id()
RETURNS TEXT
LANGUAGE sql STABLE
SET search_path = pg_catalog
AS $$
    SELECT NULLIF(current_setting('zyvello.auth_user_id', true), '')
$$;

-- Avoid recursive membership-table RLS by evaluating these checks as the
-- database owner; all references are schema-qualified, search_path fixed.
-- These functions return a boolean only and never expose membership rows.
CREATE OR REPLACE FUNCTION zyvello.is_workspace_member(p_workspace_id UUID)
RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT EXISTS (
        SELECT 1 FROM zyvello.memberships AS m
        WHERE m.workspace_id = p_workspace_id
          AND m.auth_user_id = NULLIF(current_setting('zyvello.auth_user_id', true), '')
    )
$$;

-- Prevent arbitrary roles from invoking definer-owned checks.
REVOKE ALL ON FUNCTION zyvello.current_auth_user_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION zyvello.is_workspace_member(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zyvello.current_auth_user_id() TO zyvello_api_staging;
GRANT EXECUTE ON FUNCTION zyvello.is_workspace_member(UUID) TO zyvello_api_staging;

-- Read-only membership-scoped API surface. No INSERT, UPDATE or DELETE
-- policies are granted on operational tables yet; job execution is off.
CREATE POLICY workspaces_api_read ON zyvello.workspaces
    FOR SELECT TO zyvello_api_staging
    USING (zyvello.is_workspace_member(id));

CREATE POLICY memberships_api_read_own ON zyvello.memberships
    FOR SELECT TO zyvello_api_staging
    USING (auth_user_id = zyvello.current_auth_user_id());

CREATE POLICY jobs_api_read ON zyvello.jobs
    FOR SELECT TO zyvello_api_staging
    USING (zyvello.is_workspace_member(workspace_id));

INSERT INTO zyvello.schema_migrations(version) VALUES (2)
ON CONFLICT (version) DO NOTHING;
