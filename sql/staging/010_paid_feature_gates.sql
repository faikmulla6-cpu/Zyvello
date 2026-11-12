-- Zyvello hosted staging | Migration 010
-- Goal: deny premium feature creation to unpaid or expired workspaces at the
-- database boundary, even if a hosted API route forgets its entitlement check.
--
-- Based on the 2026-11-12 Neon Staging schema (migration versions 1..9).
-- Review and execute using the database owner in a change window; never run
-- automatically against a customer/production database.
--
-- Required server-side defenses still include verified Neon Auth identity,
-- transaction-local zyvello.auth_user_id, billing provider webhook signature
-- verification, server-owned plan prices, feature quotas and worker checks.
-- Database RLS is defense in depth, not a substitute for these controls.
BEGIN;

CREATE OR REPLACE FUNCTION zyvello.is_api_workspace_entitled(p_workspace_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO pg_catalog
AS $$
    SELECT zyvello.is_workspace_member(p_workspace_id)
       AND EXISTS (
           SELECT 1
             FROM zyvello.billing_subscriptions AS b
            WHERE b.workspace_id = p_workspace_id
              AND b.status = 'active'
              AND b.checkout_verified IS TRUE
              AND b.paid_count > 0
              AND b.paid_through > CURRENT_TIMESTAMP
              AND b.provider_subscription_id IS NOT NULL
       )
$$;

-- Keep the SECURITY DEFINER surface available only to the API role.
REVOKE ALL ON FUNCTION zyvello.is_api_workspace_entitled(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zyvello.is_api_workspace_entitled(uuid)
    TO zyvello_api_staging;

-- Creating contacts, campaigns and their associations is a paid feature.
-- Reading previously-created data and recording opt-outs remain permitted
-- according to existing tenant policies, including after cancellation.
ALTER POLICY contacts_api_insert ON zyvello.contacts
WITH CHECK (
    zyvello.is_workspace_manager(workspace_id)
    AND created_by_auth_user_id = zyvello.current_auth_user_id()
    AND zyvello.is_api_workspace_entitled(workspace_id)
);

ALTER POLICY campaigns_api_insert ON zyvello.campaigns
WITH CHECK (
    zyvello.is_workspace_manager(workspace_id)
    AND created_by_auth_user_id = zyvello.current_auth_user_id()
    AND status = 'draft'
    AND zyvello.is_api_workspace_entitled(workspace_id)
);

ALTER POLICY campaign_contacts_api_insert ON zyvello.campaign_contacts
WITH CHECK (
    zyvello.is_workspace_manager(workspace_id)
    AND zyvello.is_api_workspace_entitled(workspace_id)
);

-- Both job kinds consume worker resources; neither can be enqueued for a
-- workspace without a verified, current paid subscription.
ALTER POLICY jobs_api_insert_draft ON zyvello.jobs
WITH CHECK (
    zyvello.is_workspace_manager(workspace_id)
    AND zyvello.is_api_workspace_entitled(workspace_id)
    AND kind = 'groq_draft'
    AND status = 'queued'
    AND attempts = 0
    AND max_attempts <= 3
    AND payload ? 'campaign_id'
    AND payload ? 'contact_id'
    AND leased_by IS NULL
    AND lease_until IS NULL
    AND last_error IS NULL
    AND result IS NULL
    AND finished_at IS NULL
);

ALTER POLICY jobs_api_insert_health ON zyvello.jobs
WITH CHECK (
    zyvello.is_workspace_member(workspace_id)
    AND zyvello.is_api_workspace_entitled(workspace_id)
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

-- A worker must not persist a generated draft after entitlement expiry.
-- Workers still need to check entitlement BEFORE incurring model usage,
-- and when retrying, not just when saving the result.
ALTER POLICY drafts_worker_insert ON zyvello.generated_drafts
WITH CHECK (zyvello.is_worker_workspace_entitled(workspace_id));

-- Existing ability to revoke/delete saved provider credentials remains,
-- but setting or replacing credentials is a premium feature.
ALTER POLICY provider_secret_api_insert ON zyvello.workspace_provider_secrets
WITH CHECK (
    zyvello.is_workspace_manager(workspace_id)
    AND updated_by_auth_user_id = zyvello.current_auth_user_id()
    AND zyvello.is_api_workspace_entitled(workspace_id)
);

ALTER POLICY provider_secret_api_update ON zyvello.workspace_provider_secrets
WITH CHECK (
    zyvello.is_workspace_manager(workspace_id)
    AND updated_by_auth_user_id = zyvello.current_auth_user_id()
    AND zyvello.is_api_workspace_entitled(workspace_id)
);

INSERT INTO zyvello.schema_migrations (version)
VALUES (10)
ON CONFLICT (version) DO NOTHING;

COMMIT;
