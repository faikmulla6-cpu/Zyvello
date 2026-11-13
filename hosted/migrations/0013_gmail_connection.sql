-- Workspace-scoped Google OAuth state and one encrypted Gmail mailbox per workspace.
BEGIN;

CREATE TABLE zyvello.gmail_oauth_attempts (
    workspace_id UUID PRIMARY KEY REFERENCES zyvello.workspaces(id) ON DELETE CASCADE,
    auth_user_id TEXT NOT NULL,
    state_hash TEXT NOT NULL UNIQUE CHECK (state_hash ~ '^[a-f0-9]{64}$'),
    verifier_ciphertext TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ
);

CREATE TABLE zyvello.gmail_mailboxes (
    workspace_id UUID PRIMARY KEY REFERENCES zyvello.workspaces(id) ON DELETE CASCADE,
    email TEXT NOT NULL CHECK (char_length(email) BETWEEN 3 AND 254),
    refresh_ciphertext TEXT NOT NULL CHECK (char_length(refresh_ciphertext) BETWEEN 100 AND 4096),
    connected_by_auth_user_id TEXT NOT NULL,
    connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_test_at TIMESTAMPTZ
);

ALTER TABLE zyvello.gmail_oauth_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE zyvello.gmail_mailboxes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON zyvello.gmail_oauth_attempts, zyvello.gmail_mailboxes
    FROM PUBLIC, zyvello_api_staging, zyvello_worker_staging, zyvello_billing_staging;
GRANT SELECT, INSERT, UPDATE, DELETE ON zyvello.gmail_oauth_attempts TO zyvello_api_staging;
GRANT SELECT (workspace_id, email, connected_at), INSERT, DELETE
    ON zyvello.gmail_mailboxes TO zyvello_api_staging;

CREATE POLICY gmail_attempt_api ON zyvello.gmail_oauth_attempts
    FOR ALL TO zyvello_api_staging
    USING (zyvello.is_workspace_manager(workspace_id))
    WITH CHECK (zyvello.is_workspace_manager(workspace_id)
        AND zyvello.is_api_workspace_entitled(workspace_id)
        AND auth_user_id = zyvello.current_auth_user_id());
CREATE POLICY gmail_mailbox_api_read ON zyvello.gmail_mailboxes
    FOR SELECT TO zyvello_api_staging USING (zyvello.is_workspace_manager(workspace_id));
CREATE POLICY gmail_mailbox_api_insert ON zyvello.gmail_mailboxes
    FOR INSERT TO zyvello_api_staging
    WITH CHECK (zyvello.is_workspace_manager(workspace_id)
        AND zyvello.is_api_workspace_entitled(workspace_id)
        AND connected_by_auth_user_id = zyvello.current_auth_user_id());
CREATE POLICY gmail_mailbox_api_delete ON zyvello.gmail_mailboxes
    FOR DELETE TO zyvello_api_staging USING (zyvello.is_workspace_manager(workspace_id));

-- The API cannot read refresh tokens from the table. This operation returns
-- one credential only after reserving a test for the connected mailbox.
CREATE FUNCTION zyvello.reserve_gmail_self_test(p_workspace_id uuid)
RETURNS TABLE(email text, refresh_ciphertext text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog AS $$
BEGIN
    IF NOT zyvello.is_workspace_manager(p_workspace_id)
        OR NOT zyvello.is_api_workspace_entitled(p_workspace_id) THEN
        RETURN;
    END IF;
    RETURN QUERY
        UPDATE zyvello.gmail_mailboxes AS m SET last_test_at = now()
        WHERE m.workspace_id = p_workspace_id
          AND (m.last_test_at IS NULL OR m.last_test_at <= now() - interval '5 minutes')
        RETURNING m.email, m.refresh_ciphertext;
END;
$$;
REVOKE ALL ON FUNCTION zyvello.reserve_gmail_self_test(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zyvello.reserve_gmail_self_test(uuid) TO zyvello_api_staging;

INSERT INTO zyvello.schema_migrations(version) VALUES (13);
COMMIT;
