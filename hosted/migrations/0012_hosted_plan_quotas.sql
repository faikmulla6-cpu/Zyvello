-- Private-beta subscription quotas: ONLY features presently shipped.
-- Guard version 11 before use. This is NOT a live-money authorization.
BEGIN;

DO $$ BEGIN
  IF (SELECT max(version) FROM zyvello.schema_migrations) <> 11 THEN
    RAISE EXCEPTION 'Plan quota migration v12 requires hosted v11';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION zyvello.is_api_workspace_within_limit(
    p_workspace_id uuid, p_kind text)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path TO pg_catalog AS $$
DECLARE
    v_plan text;
    v_max integer;
    v_used bigint;
BEGIN
    IF p_kind NOT IN ('contact','campaign','member') OR p_kind IS NULL THEN
        RETURN FALSE;
    END IF;
    -- Serialize SQL-level quota checks, not only the application's own
    -- INSERT transactions. VOLATILE sees rows inserted earlier in a bulk
    -- statement, so one multi-row INSERT cannot silently exceed a cap.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        p_workspace_id::text,
        CASE p_kind WHEN 'contact' THEN 2 WHEN 'campaign' THEN 3 ELSE 4 END
    ));
    IF NOT zyvello.is_workspace_member(p_workspace_id)
       OR NOT zyvello.is_api_workspace_entitled(p_workspace_id) THEN
        RETURN FALSE;
    END IF;

    SELECT b.plan_code INTO v_plan
    FROM zyvello.billing_subscriptions AS b
    WHERE b.workspace_id = p_workspace_id
      AND b.status = 'active'
      AND b.checkout_verified IS TRUE
      AND b.paid_count > 0
      AND b.paid_through > CURRENT_TIMESTAMP
      AND b.provider_subscription_id IS NOT NULL
    ORDER BY b.created_at DESC
    LIMIT 1;

    IF v_plan NOT IN ('starter', 'growth', 'business') OR v_plan IS NULL THEN
        RETURN FALSE;
    END IF;

    IF p_kind = 'contact' THEN
        v_max := CASE v_plan WHEN 'starter' THEN 100
                 WHEN 'growth' THEN 400 WHEN 'business' THEN 1000 END;
        SELECT count(*) INTO v_used FROM zyvello.contacts
        WHERE workspace_id = p_workspace_id;
    ELSIF p_kind = 'campaign' THEN
        v_max := CASE v_plan WHEN 'starter' THEN 5
                 WHEN 'growth' THEN 20 WHEN 'business' THEN 50 END;
        SELECT count(*) INTO v_used FROM zyvello.campaigns
        WHERE workspace_id = p_workspace_id;
    ELSIF p_kind = 'member' THEN
        v_max := CASE v_plan WHEN 'starter' THEN 1
                 WHEN 'growth' THEN 3 WHEN 'business' THEN 10 END;
        SELECT count(*) INTO v_used FROM zyvello.memberships
        WHERE workspace_id = p_workspace_id;
    ELSE
        RETURN FALSE;
    END IF;
    RETURN v_used < v_max;
END;
$$;
REVOKE ALL ON FUNCTION zyvello.is_api_workspace_within_limit(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zyvello.is_api_workspace_within_limit(uuid,text)
    TO zyvello_api_staging;

-- Even a compromised API cannot bypass a plan's stored-item limit by
-- calling SQL directly. Existing rows remain readable on failure/expiry.
ALTER POLICY contacts_api_insert ON zyvello.contacts
WITH CHECK (
    zyvello.is_workspace_manager(workspace_id)
    AND zyvello.is_api_workspace_within_limit(workspace_id,'contact')
    AND created_by_auth_user_id = zyvello.current_auth_user_id()
);
ALTER POLICY campaigns_api_insert ON zyvello.campaigns
WITH CHECK (
    zyvello.is_workspace_manager(workspace_id)
    AND zyvello.is_api_workspace_within_limit(workspace_id,'campaign')
    AND created_by_auth_user_id = zyvello.current_auth_user_id()
    AND status = 'draft'
);

-- Invitation acceptance uses SECURITY DEFINER because the invited verified
-- recipient does not yet have membership: never rely on a browser email.
CREATE OR REPLACE FUNCTION zyvello.accept_workspace_invitation(p_token_sha256 text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO pg_catalog AS $$
DECLARE
  v_actor text := zyvello.current_auth_user_id();
  v_email text;
  v_invite_id uuid;
  v_workspace_id uuid;
  v_role text;
  v_plan text;
  v_max integer;
  v_count integer;
BEGIN
  IF v_actor IS NULL OR p_token_sha256 !~ '^[a-f0-9]{64}$' THEN RETURN NULL; END IF;
  SELECT lower(u.email) INTO v_email FROM neon_auth."user" u
   WHERE u.id::text=v_actor AND u."emailVerified"=TRUE;
  IF v_email IS NULL THEN RETURN NULL; END IF;
  SELECT i.id,i.workspace_id,i.role
    INTO v_invite_id,v_workspace_id,v_role
    FROM zyvello.workspace_invitations i
   WHERE i.token_sha256=p_token_sha256 AND i.invitee_email=v_email
     AND i.accepted_at IS NULL AND i.revoked_at IS NULL
     AND i.expires_at > CURRENT_TIMESTAMP
   FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL; END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(v_workspace_id::text, 4));
  SELECT b.plan_code INTO v_plan FROM zyvello.billing_subscriptions b
  WHERE b.workspace_id=v_workspace_id
    AND b.status='active' AND b.checkout_verified
    AND b.paid_count>0 AND b.paid_through>CURRENT_TIMESTAMP
    AND b.provider_subscription_id IS NOT NULL
  ORDER BY b.created_at DESC LIMIT 1;
  v_max := CASE v_plan WHEN 'starter' THEN 1
            WHEN 'growth' THEN 3 WHEN 'business' THEN 10 ELSE 0 END;
  SELECT count(*) INTO v_count FROM zyvello.memberships
   WHERE workspace_id=v_workspace_id;

  -- Return NULL but keep invitation pending when the plan is unpaid or full.
  -- Existing members are allowed to redeem their already-issued token safely.
  IF v_count >= v_max AND NOT EXISTS (
      SELECT 1 FROM zyvello.memberships
      WHERE workspace_id=v_workspace_id AND auth_user_id=v_actor
  ) THEN RETURN NULL; END IF;
  IF v_max = 0 THEN RETURN NULL; END IF;

  INSERT INTO zyvello.memberships(workspace_id,auth_user_id,role)
  VALUES (v_workspace_id,v_actor,v_role)
  ON CONFLICT (workspace_id,auth_user_id) DO NOTHING;
  UPDATE zyvello.workspace_invitations
  SET accepted_at=CURRENT_TIMESTAMP WHERE id=v_invite_id;
  RETURN v_workspace_id;
END;
$$;
REVOKE ALL ON FUNCTION zyvello.accept_workspace_invitation(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zyvello.accept_workspace_invitation(text)
    TO zyvello_api_staging;

INSERT INTO zyvello.schema_migrations(version) VALUES (12)
ON CONFLICT (version) DO NOTHING;
COMMIT;
