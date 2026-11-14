-- Keep historical rows and event IDs in Test Mode.
ALTER TABLE zyvello.billing_subscriptions
    ADD COLUMN provider_mode TEXT NOT NULL DEFAULT 'test' CHECK (provider_mode IN ('test', 'live')),
    ADD CONSTRAINT billing_live_starter_only
        CHECK (provider_mode = 'test' OR (plan_code = 'starter' AND amount_paise = 49900));

ALTER TABLE zyvello.billing_webhook_events
    ADD COLUMN provider_mode TEXT NOT NULL DEFAULT 'test' CHECK (provider_mode IN ('test', 'live')),
    DROP CONSTRAINT billing_webhook_events_pkey,
    ADD PRIMARY KEY (provider_mode, event_id);

-- Live payment confirmation belongs exclusively to the billing webhook role.
CREATE OR REPLACE FUNCTION zyvello.mark_billing_checkout_verified(p_id UUID, p_payment_id TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE changed INTEGER;
BEGIN
    IF p_payment_id !~ '^pay_[A-Za-z0-9]{8,40}$' THEN RETURN FALSE; END IF;
    UPDATE zyvello.billing_subscriptions AS bs
       SET checkout_verified=TRUE, latest_checkout_payment_id=p_payment_id, updated_at=now()
     WHERE bs.id=p_id AND bs.provider_mode='test' AND bs.provider_subscription_id IS NOT NULL
       AND bs.created_by_auth_user_id=zyvello.current_auth_user_id()
       AND zyvello.is_workspace_manager(bs.workspace_id);
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed = 1;
END
$$;

ALTER POLICY billing_api_insert ON zyvello.billing_subscriptions
    WITH CHECK (
        zyvello.is_workspace_owner(workspace_id)
        AND created_by_auth_user_id = zyvello.current_auth_user_id()
        AND status = 'creating' AND provider_subscription_id IS NULL
        AND checkout_verified = FALSE AND paid_through IS NULL AND paid_count = 0
        AND latest_checkout_payment_id IS NULL AND last_event_at IS NULL
    );

INSERT INTO zyvello.schema_migrations(version) VALUES (15)
ON CONFLICT (version) DO NOTHING;
