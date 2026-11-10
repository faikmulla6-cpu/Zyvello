-- Private-beta BYOK secret vault and immutable contact permission ledger.
-- No outbound sending, mailbox access, or AI execution is enabled here.
-- Apply on an isolated Neon child branch first, then review before staging.

CREATE TABLE zyvello.workspace_provider_secrets (
    workspace_id UUID NOT NULL REFERENCES zyvello.workspaces(id) ON DELETE CASCADE,
    provider TEXT NOT NULL CHECK (provider = 'groq'),
    ciphertext TEXT NOT NULL CHECK (char_length(ciphertext) BETWEEN 100 AND 2048),
    updated_by_auth_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, provider)
);

CREATE TABLE zyvello.contact_permission_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL,
    contact_id UUID NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('claimed_consent','opted_out','suppressed')),
    source TEXT NOT NULL CHECK (char_length(trim(source)) BETWEEN 3 AND 100),
    evidence TEXT NOT NULL DEFAULT '' CHECK (char_length(evidence) <= 1000),
    changed_by_auth_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (workspace_id, contact_id)
        REFERENCES zyvello.contacts(workspace_id, id) ON DELETE CASCADE
);

CREATE INDEX permission_events_recent_idx ON zyvello.contact_permission_events
    (workspace_id, contact_id, created_at DESC, id DESC);

ALTER TABLE zyvello.workspace_provider_secrets ENABLE ROW LEVEL SECURITY;
ALTER TABLE zyvello.contact_permission_events ENABLE ROW LEVEL SECURITY;

-- Scoped roles may inherit default table grants: explicitly deny first.
REVOKE ALL ON zyvello.workspace_provider_secrets, zyvello.contact_permission_events
    FROM zyvello_api_staging, zyvello_worker_staging, zyvello_billing_staging;

-- The API may observe only whether a provider is configured. Ciphertext is
-- deliberately excluded from its SELECT grant. Only the worker can read it.
GRANT SELECT (workspace_id, provider, updated_at)
    ON zyvello.workspace_provider_secrets TO zyvello_api_staging;
GRANT INSERT (workspace_id, provider, ciphertext, updated_by_auth_user_id),
    UPDATE (ciphertext, updated_by_auth_user_id, updated_at), DELETE
    ON zyvello.workspace_provider_secrets TO zyvello_api_staging;
GRANT SELECT (workspace_id, provider, ciphertext)
    ON zyvello.workspace_provider_secrets TO zyvello_worker_staging;

GRANT SELECT, INSERT ON zyvello.contact_permission_events TO zyvello_api_staging;

CREATE POLICY provider_secret_api_read ON zyvello.workspace_provider_secrets
    FOR SELECT TO zyvello_api_staging
    USING (zyvello.is_workspace_manager(workspace_id));
CREATE POLICY provider_secret_api_insert ON zyvello.workspace_provider_secrets
    FOR INSERT TO zyvello_api_staging
    WITH CHECK (zyvello.is_workspace_manager(workspace_id)
        AND updated_by_auth_user_id=zyvello.current_auth_user_id());
CREATE POLICY provider_secret_api_update ON zyvello.workspace_provider_secrets
    FOR UPDATE TO zyvello_api_staging
    USING (zyvello.is_workspace_manager(workspace_id))
    WITH CHECK (zyvello.is_workspace_manager(workspace_id)
        AND updated_by_auth_user_id=zyvello.current_auth_user_id());
CREATE POLICY provider_secret_api_delete ON zyvello.workspace_provider_secrets
    FOR DELETE TO zyvello_api_staging
    USING (zyvello.is_workspace_manager(workspace_id));
CREATE POLICY provider_secret_worker_read ON zyvello.workspace_provider_secrets
    FOR SELECT TO zyvello_worker_staging USING (true);

CREATE POLICY permission_events_api_read ON zyvello.contact_permission_events
    FOR SELECT TO zyvello_api_staging
    USING (zyvello.is_workspace_member(workspace_id));
CREATE POLICY permission_events_api_insert ON zyvello.contact_permission_events
    FOR INSERT TO zyvello_api_staging
    WITH CHECK (zyvello.is_workspace_manager(workspace_id)
        AND changed_by_auth_user_id=zyvello.current_auth_user_id());

-- No DELETE or UPDATE grant on consent events: audit history is append-only.
-- A customer reporting claimed consent does not equal independently verified
-- consent and never unlocks sending. Actual sends remain feature-disabled.
INSERT INTO zyvello.schema_migrations(version) VALUES (6)
ON CONFLICT (version) DO NOTHING;
