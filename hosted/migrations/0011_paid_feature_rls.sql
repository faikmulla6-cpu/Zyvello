-- Zyvello hosted step 3: database-level subscription enforcement (v0011).
-- Staging/test only; live charges, outbound email and AI remain disabled.
-- Non-destructive: no customer, billing or webhook records are modified.
BEGIN;

-- One shared entitlement predicate for API authorization and RLS writes.
-- Caller must already be a verified Neon Auth principal, scoped transactionally
-- by the restricted zyvello_api_staging PostgreSQL role.
CREATE FUNCTION zyvello.is_api_workspace_entitled(p_workspace_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT zyvello.is_workspace_member(p_workspace_id)
       AND EXISTS (
           SELECT 1 FROM zyvello.billing_subscriptions AS sub
           WHERE sub.workspace_id = p_workspace_id
             AND sub.status = 'active'
             AND sub.checkout_verified IS TRUE
             AND sub.paid_count > 0
             AND sub.paid_through > CURRENT_TIMESTAMP
             AND sub.provider_subscription_id IS NOT NULL
       )
$$;
REVOKE ALL ON FUNCTION zyvello.is_api_workspace_entitled(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zyvello.is_api_workspace_entitled(uuid) TO zyvello_api_staging;

-- Keep workspace/account onboarding, subscription checkout, cancellation,
-- opt-outs, permission cleanup and administrative settings available when
-- unpaid. Paid operational writes, however, fail closed at the database.
ALTER POLICY contacts_api_insert ON zyvello.contacts
    WITH CHECK (
      zyvello.is_workspace_manager(workspace_id)
      AND zyvello.is_api_workspace_entitled(workspace_id)
      AND created_by_auth_user_id = zyvello.current_auth_user_id()
    );

ALTER POLICY campaigns_api_insert ON zyvello.campaigns
    WITH CHECK (
      zyvello.is_workspace_manager(workspace_id)
      AND zyvello.is_api_workspace_entitled(workspace_id)
      AND created_by_auth_user_id = zyvello.current_auth_user_id()
      AND status = 'draft'
    );

ALTER POLICY campaign_contacts_api_insert ON zyvello.campaign_contacts
    WITH CHECK (
      zyvello.is_workspace_manager(workspace_id)
      AND zyvello.is_api_workspace_entitled(workspace_id)
    );

ALTER POLICY jobs_api_insert_draft ON zyvello.jobs
    WITH CHECK (
      zyvello.is_workspace_manager(workspace_id)
      AND zyvello.is_api_workspace_entitled(workspace_id)
      AND kind = 'groq_draft'
      AND status = 'queued'
      AND attempts = 0 AND max_attempts <= 3
      AND payload ? 'campaign_id' AND payload ? 'contact_id'
      AND leased_by IS NULL AND lease_until IS NULL
      AND last_error IS NULL AND result IS NULL AND finished_at IS NULL
    );

ALTER POLICY jobs_api_insert_health ON zyvello.jobs
    WITH CHECK (
      zyvello.is_workspace_member(workspace_id)
      AND zyvello.is_api_workspace_entitled(workspace_id)
      AND kind = 'workspace_health'
      AND status = 'queued'
      AND attempts = 0 AND max_attempts <= 3
      AND leased_by IS NULL AND lease_until IS NULL
      AND last_error IS NULL AND result IS NULL AND finished_at IS NULL
    );

ALTER POLICY provider_secret_api_insert ON zyvello.workspace_provider_secrets
    WITH CHECK (
      zyvello.is_workspace_manager(workspace_id)
      AND zyvello.is_api_workspace_entitled(workspace_id)
      AND updated_by_auth_user_id = zyvello.current_auth_user_id()
    );

ALTER POLICY provider_secret_api_update ON zyvello.workspace_provider_secrets
    USING (zyvello.is_workspace_manager(workspace_id))
    WITH CHECK (
      zyvello.is_workspace_manager(workspace_id)
      AND zyvello.is_api_workspace_entitled(workspace_id)
      AND updated_by_auth_user_id = zyvello.current_auth_user_id()
    );

-- Worker may inspect/finish already-queued work when a subscription expires,
-- but must never persist NEW generated content for an unpaid workspace.
ALTER POLICY drafts_worker_insert ON zyvello.generated_drafts
    WITH CHECK (zyvello.is_worker_workspace_entitled(workspace_id));

INSERT INTO zyvello.schema_migrations(version) VALUES (11)
ON CONFLICT (version) DO NOTHING;
COMMIT;
