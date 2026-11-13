-- Zyvello hosted staging v0009: make workers check current paid status.
-- No customer data is changed; do not grant raw billing-table reads to workers.
-- Security-definer returns a single boolean without customer/payment data.
CREATE FUNCTION zyvello.is_worker_workspace_entitled(p_workspace_id UUID)
RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog
AS $entitled$
    SELECT EXISTS (
        SELECT 1 FROM zyvello.billing_subscriptions AS sub
        WHERE sub.workspace_id = p_workspace_id
          AND sub.status = 'active'
          AND sub.checkout_verified = TRUE
          AND sub.paid_count > 0
          AND sub.paid_through > CURRENT_TIMESTAMP
    )
$entitled$;
REVOKE ALL ON FUNCTION zyvello.is_worker_workspace_entitled(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zyvello.is_worker_workspace_entitled(UUID)
    TO zyvello_worker_staging;
INSERT INTO zyvello.schema_migrations(version) VALUES (9)
ON CONFLICT (version) DO NOTHING;
