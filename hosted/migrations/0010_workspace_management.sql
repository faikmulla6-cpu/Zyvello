-- Hosted Zyvello workspace lifecycle (v0010).
-- Apply first to a temporary Neon branch. No billing or outbound behavior changes.
-- Caller connection MUST be zyvello_api_staging, never schema owner.
BEGIN;

ALTER TABLE zyvello.workspaces
  ADD COLUMN IF NOT EXISTS settings jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE zyvello.workspaces
  ADD CONSTRAINT workspace_settings_object_check CHECK (jsonb_typeof(settings) = 'object');

CREATE OR REPLACE FUNCTION zyvello.is_workspace_owner(p_workspace_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path TO pg_catalog AS $$
  SELECT EXISTS (
    SELECT 1 FROM zyvello.memberships m
    WHERE m.workspace_id = p_workspace_id
      AND m.auth_user_id = zyvello.current_auth_user_id()
      AND m.role = 'owner'
  )
$$;
REVOKE ALL ON FUNCTION zyvello.is_workspace_owner(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zyvello.is_workspace_owner(uuid) TO zyvello_api_staging;

-- Prevent a malicious API query from editing immutable ownership/slug IDs.
REVOKE UPDATE ON zyvello.workspaces FROM zyvello_api_staging;
GRANT UPDATE(name, settings) ON zyvello.workspaces TO zyvello_api_staging;
CREATE POLICY workspaces_api_manage
ON zyvello.workspaces FOR UPDATE TO zyvello_api_staging
USING (zyvello.is_workspace_manager(id))
WITH CHECK (zyvello.is_workspace_manager(id));

-- Team roster is visible only to members of the same workspace.
CREATE POLICY memberships_api_read_team
ON zyvello.memberships FOR SELECT TO zyvello_api_staging
USING (zyvello.is_workspace_member(workspace_id));

-- Owner may promote/demote only non-owner entries. Owner never loses ownership.
REVOKE UPDATE ON zyvello.memberships FROM zyvello_api_staging;
GRANT UPDATE(role) ON zyvello.memberships TO zyvello_api_staging;
CREATE POLICY memberships_api_edit_nonowners
ON zyvello.memberships FOR UPDATE TO zyvello_api_staging
USING (role IN ('admin','member') AND zyvello.is_workspace_owner(workspace_id))
WITH CHECK (role IN ('admin','member') AND zyvello.is_workspace_owner(workspace_id));
CREATE POLICY memberships_api_remove_nonowners
ON zyvello.memberships FOR DELETE TO zyvello_api_staging
USING (
  role IN ('admin','member')
  AND (
    zyvello.is_workspace_owner(workspace_id)
    OR auth_user_id = zyvello.current_auth_user_id()
  )
);

-- Expose verified teammates' display emails without granting the API role
-- general SELECT rights on neon_auth tables.
CREATE OR REPLACE FUNCTION zyvello.workspace_member_directory(p_workspace_id uuid)
RETURNS TABLE(auth_user_id text, role text, email text, created_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO pg_catalog AS $$
 SELECT m.auth_user_id, m.role, u.email::text, m.created_at
 FROM zyvello.memberships m
 JOIN neon_auth."user" u ON u.id::text = m.auth_user_id
 WHERE m.workspace_id = p_workspace_id
   AND zyvello.is_workspace_member(p_workspace_id)
 ORDER BY m.created_at, m.auth_user_id
$$;
REVOKE ALL ON FUNCTION zyvello.workspace_member_directory(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zyvello.workspace_member_directory(uuid)
 TO zyvello_api_staging;

-- Invitations are explicitly accepted by a signed-in recipient; no silent
-- membership grants, credentials, or outbound email are involved.
CREATE TABLE IF NOT EXISTS zyvello.workspace_invitations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES zyvello.workspaces(id) ON DELETE CASCADE,
  invitee_email text NOT NULL CHECK (
    invitee_email = lower(invitee_email)
    AND char_length(invitee_email) BETWEEN 3 AND 254
    AND invitee_email ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$'
  ),
  role text NOT NULL CHECK (role IN ('admin','member')),
  token_sha256 text NOT NULL UNIQUE CHECK (token_sha256 ~ '^[a-f0-9]{64}$'),
  invited_by_auth_user_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  accepted_at timestamptz,
  revoked_at timestamptz,
  CONSTRAINT invitation_expiry_bounds CHECK (
    expires_at > created_at AND expires_at <= created_at + interval '7 days 1 minute'
  ),
  CONSTRAINT invitation_not_accepted_and_revoked CHECK (
    accepted_at IS NULL OR revoked_at IS NULL
  )
);
CREATE INDEX IF NOT EXISTS workspace_invitations_pending_idx
 ON zyvello.workspace_invitations(workspace_id, invitee_email)
 WHERE accepted_at IS NULL AND revoked_at IS NULL;
ALTER TABLE zyvello.workspace_invitations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON zyvello.workspace_invitations FROM PUBLIC, zyvello_api_staging, zyvello_worker_staging, zyvello_billing_staging;
GRANT SELECT, INSERT ON zyvello.workspace_invitations TO zyvello_api_staging;
GRANT UPDATE(revoked_at) ON zyvello.workspace_invitations TO zyvello_api_staging;
CREATE POLICY workspace_invites_manager_read ON zyvello.workspace_invitations
 FOR SELECT TO zyvello_api_staging
 USING (zyvello.is_workspace_manager(workspace_id));
CREATE POLICY workspace_invites_manager_create ON zyvello.workspace_invitations
 FOR INSERT TO zyvello_api_staging
 WITH CHECK (
   zyvello.is_workspace_manager(workspace_id)
   AND invited_by_auth_user_id = zyvello.current_auth_user_id()
   AND accepted_at IS NULL AND revoked_at IS NULL
 );
CREATE POLICY workspace_invites_manager_revoke ON zyvello.workspace_invitations
 FOR UPDATE TO zyvello_api_staging
 USING (zyvello.is_workspace_manager(workspace_id) AND accepted_at IS NULL)
 WITH CHECK (zyvello.is_workspace_manager(workspace_id) AND accepted_at IS NULL);

-- The only way an invite may create an 'admin'/'member' membership is a
-- one-time database function that verifies the actual Neon Auth user email.
-- The browser is never trusted to submit an auth user ID or email as identity.
CREATE OR REPLACE FUNCTION zyvello.accept_workspace_invitation(p_token_sha256 text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO pg_catalog AS $$
DECLARE
  v_actor text := zyvello.current_auth_user_id();
  v_email text;
  v_invite_id uuid;
  v_workspace_id uuid;
  v_role text;
BEGIN
  IF v_actor IS NULL OR p_token_sha256 !~ '^[a-f0-9]{64}$' THEN
    RETURN NULL;
  END IF;
  SELECT lower(u.email) INTO v_email
    FROM neon_auth."user" u
    WHERE u.id::text = v_actor AND u."emailVerified" = TRUE;
  IF v_email IS NULL THEN RETURN NULL; END IF;

  SELECT i.id, i.workspace_id, i.role
   INTO v_invite_id, v_workspace_id, v_role
   FROM zyvello.workspace_invitations i
   WHERE i.token_sha256 = p_token_sha256
     AND i.invitee_email = v_email
     AND i.accepted_at IS NULL
     AND i.revoked_at IS NULL
     AND i.expires_at > CURRENT_TIMESTAMP
   FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL; END IF;

  -- A concurrent acceptance does not add duplicate members or escalate roles.
  INSERT INTO zyvello.memberships(workspace_id, auth_user_id, role)
  VALUES (v_workspace_id, v_actor, v_role)
  ON CONFLICT (workspace_id, auth_user_id) DO NOTHING;
  UPDATE zyvello.workspace_invitations
     SET accepted_at = CURRENT_TIMESTAMP
   WHERE id = v_invite_id;
  RETURN v_workspace_id;
END;
$$;
REVOKE ALL ON FUNCTION zyvello.accept_workspace_invitation(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zyvello.accept_workspace_invitation(text)
 TO zyvello_api_staging;

-- A workspace may be permanently deleted only while empty, solely by its
-- verified owner. Do not cascade away paid subscriptions, customer contacts,
-- operational jobs or audit history. Populated workspace/account deletion
-- needs a separate retention and billing lifecycle.
CREATE OR REPLACE FUNCTION zyvello.delete_empty_workspace(p_workspace_id uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO pg_catalog AS $$
DECLARE
    v_actor text := zyvello.current_auth_user_id();
BEGIN
    IF v_actor IS NULL OR NOT zyvello.is_workspace_owner(p_workspace_id) THEN
        RETURN FALSE;
    END IF;
    -- Serializes against concurrent FK-dependent inserts while we check.
    PERFORM 1 FROM zyvello.workspaces WHERE id=p_workspace_id FOR UPDATE;
    IF NOT FOUND THEN RETURN FALSE; END IF;
    IF EXISTS (SELECT 1 FROM zyvello.billing_subscriptions WHERE workspace_id=p_workspace_id)
      OR EXISTS (SELECT 1 FROM zyvello.jobs WHERE workspace_id=p_workspace_id)
      OR EXISTS (SELECT 1 FROM zyvello.contacts WHERE workspace_id=p_workspace_id)
      OR EXISTS (SELECT 1 FROM zyvello.campaigns WHERE workspace_id=p_workspace_id)
      OR EXISTS (SELECT 1 FROM zyvello.workspace_provider_secrets WHERE workspace_id=p_workspace_id)
      OR EXISTS (SELECT 1 FROM zyvello.workspace_invitations WHERE workspace_id=p_workspace_id)
      OR EXISTS (SELECT 1 FROM zyvello.memberships
                 WHERE workspace_id=p_workspace_id AND auth_user_id<>v_actor)
    THEN
      RETURN FALSE;
    END IF;
    DELETE FROM zyvello.workspaces WHERE id=p_workspace_id;
    RETURN FOUND;
END;
$$;
REVOKE ALL ON FUNCTION zyvello.delete_empty_workspace(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zyvello.delete_empty_workspace(uuid) TO zyvello_api_staging;

INSERT INTO zyvello.schema_migrations(version) VALUES (10)
ON CONFLICT (version) DO NOTHING;
COMMIT;
