-- Workspace-scoped contact directory and inert campaign drafts. No sends.
-- Apply only after v0003 on a throwaway branch, then staging.

CREATE TABLE zyvello.contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES zyvello.workspaces(id) ON DELETE CASCADE,
    full_name TEXT NOT NULL CHECK (char_length(trim(full_name)) BETWEEN 1 AND 160),
    email TEXT NOT NULL CHECK (
        char_length(email) BETWEEN 3 AND 254
        AND email = lower(email)
        AND email !~ '[[:space:]]'
        AND email ~ '^[^@]+@[^@]+\.[^@]+$'
    ),
    company TEXT NOT NULL DEFAULT '' CHECK (char_length(company) <= 160),
    created_by_auth_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id)
);

CREATE UNIQUE INDEX contacts_workspace_email_unique
    ON zyvello.contacts (workspace_id, email);
CREATE INDEX contacts_workspace_created_idx
    ON zyvello.contacts (workspace_id, created_at DESC);

CREATE TABLE zyvello.campaigns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES zyvello.workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (char_length(trim(name)) BETWEEN 1 AND 160),
    goal TEXT NOT NULL DEFAULT '' CHECK (char_length(goal) <= 1000),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status = 'draft'),
    created_by_auth_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id)
);

CREATE INDEX campaigns_workspace_created_idx
    ON zyvello.campaigns (workspace_id, created_at DESC);

CREATE TABLE zyvello.campaign_contacts (
    workspace_id UUID NOT NULL REFERENCES zyvello.workspaces(id) ON DELETE CASCADE,
    campaign_id UUID NOT NULL,
    contact_id UUID NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, campaign_id, contact_id),
    FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES zyvello.campaigns(workspace_id, id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, contact_id)
        REFERENCES zyvello.contacts(workspace_id, id) ON DELETE CASCADE
);

CREATE INDEX campaign_contacts_workspace_contact_idx
    ON zyvello.campaign_contacts (workspace_id, contact_id);

-- A member may see the directory, but only an owner/admin may add records.
CREATE OR REPLACE FUNCTION zyvello.is_workspace_manager(p_workspace_id UUID)
RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT EXISTS (
        SELECT 1 FROM zyvello.memberships AS m
        WHERE m.workspace_id = p_workspace_id
          AND m.auth_user_id = NULLIF(current_setting('zyvello.auth_user_id', true), '')
          AND m.role IN ('owner', 'admin')
    )
$$;

REVOKE ALL ON FUNCTION zyvello.is_workspace_manager(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zyvello.is_workspace_manager(UUID) TO zyvello_api_staging;

ALTER TABLE zyvello.contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE zyvello.campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE zyvello.campaign_contacts ENABLE ROW LEVEL SECURITY;

-- Neon scoped read_write roles may receive broad default privileges on new
-- tables; revoke those first, then grant only the two operations supported.
REVOKE ALL ON zyvello.contacts, zyvello.campaigns,
    zyvello.campaign_contacts FROM zyvello_api_staging;
GRANT SELECT, INSERT ON zyvello.contacts, zyvello.campaigns,
    zyvello.campaign_contacts TO zyvello_api_staging;
REVOKE ALL ON zyvello.contacts, zyvello.campaigns,
    zyvello.campaign_contacts FROM zyvello_worker_staging;

CREATE POLICY contacts_api_read ON zyvello.contacts
    FOR SELECT TO zyvello_api_staging
    USING (zyvello.is_workspace_member(workspace_id));
CREATE POLICY contacts_api_insert ON zyvello.contacts
    FOR INSERT TO zyvello_api_staging
    WITH CHECK (
        zyvello.is_workspace_manager(workspace_id)
        AND created_by_auth_user_id = zyvello.current_auth_user_id()
    );

CREATE POLICY campaigns_api_read ON zyvello.campaigns
    FOR SELECT TO zyvello_api_staging
    USING (zyvello.is_workspace_member(workspace_id));
CREATE POLICY campaigns_api_insert ON zyvello.campaigns
    FOR INSERT TO zyvello_api_staging
    WITH CHECK (
        zyvello.is_workspace_manager(workspace_id)
        AND created_by_auth_user_id = zyvello.current_auth_user_id()
        AND status = 'draft'
    );

CREATE POLICY campaign_contacts_api_read ON zyvello.campaign_contacts
    FOR SELECT TO zyvello_api_staging
    USING (zyvello.is_workspace_member(workspace_id));
CREATE POLICY campaign_contacts_api_insert ON zyvello.campaign_contacts
    FOR INSERT TO zyvello_api_staging
    WITH CHECK (zyvello.is_workspace_manager(workspace_id));

INSERT INTO zyvello.schema_migrations(version) VALUES (4)
ON CONFLICT (version) DO NOTHING;
