-- Razorpay Subscriptions: TEST-mode-only ledger, never a payment launcher.
-- Requires separate non-owner role zyvello_billing_staging, created via Neon
-- as a scoped read_write role on the zyvello schema before this migration.
-- Apply on an isolated branch before staging. No real charges are authorized.

CREATE TABLE zyvello.billing_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES zyvello.workspaces(id),
    plan_code TEXT NOT NULL CHECK (char_length(plan_code) BETWEEN 2 AND 40),
    amount_paise INTEGER NOT NULL CHECK (amount_paise BETWEEN 100 AND 10000000),
    provider_plan_id TEXT NOT NULL CHECK (provider_plan_id ~ '^plan_[A-Za-z0-9]{8,40}$'),
    provider_subscription_id TEXT UNIQUE CHECK (
        provider_subscription_id IS NULL OR provider_subscription_id ~ '^sub_[A-Za-z0-9]{8,40}$'
    ),
    status TEXT NOT NULL DEFAULT 'creating' CHECK (status IN
        ('creating','created','authenticated','active','pending','halted',
         'paused','cancelled','completed','expired')),
    checkout_verified BOOLEAN NOT NULL DEFAULT FALSE,
    latest_checkout_payment_id TEXT,
    paid_through TIMESTAMPTZ,
    paid_count INTEGER NOT NULL DEFAULT 0 CHECK (paid_count >= 0),
    last_event_at TIMESTAMPTZ,
    idempotency_key TEXT NOT NULL CHECK (char_length(idempotency_key) BETWEEN 8 AND 100),
    created_by_auth_user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, idempotency_key),
    UNIQUE (workspace_id, id)
);

CREATE INDEX billing_workspace_created_idx ON zyvello.billing_subscriptions(workspace_id, created_at DESC);

CREATE TABLE zyvello.billing_webhook_events (
    event_id TEXT PRIMARY KEY CHECK (char_length(event_id) BETWEEN 4 AND 128),
    event_type TEXT NOT NULL CHECK (char_length(event_type) <= 100),
    provider_subscription_id TEXT NOT NULL,
    body_sha256 TEXT NOT NULL CHECK (char_length(body_sha256)=64),
    event_at TIMESTAMPTZ NOT NULL,
    resolution TEXT NOT NULL CHECK (resolution IN ('applied','ignored','unmatched','plan_mismatch')),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE zyvello.billing_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE zyvello.billing_webhook_events ENABLE ROW LEVEL SECURITY;

-- Scoped Neon roles can inherit generous default privileges: lock these down.
REVOKE ALL ON zyvello.billing_subscriptions, zyvello.billing_webhook_events FROM zyvello_api_staging;
REVOKE ALL ON zyvello.billing_subscriptions, zyvello.billing_webhook_events FROM zyvello_worker_staging;
-- Neon scoped read_write roles initially inherit access to ALL existing
-- tables in the schema, including contacts and migration metadata. Revoke
-- those privileges as well, plus schema creation, before granting billing.
REVOKE ALL ON ALL TABLES IN SCHEMA zyvello FROM zyvello_billing_staging;
REVOKE CREATE ON SCHEMA zyvello FROM zyvello_billing_staging;
ALTER DEFAULT PRIVILEGES IN SCHEMA zyvello
    REVOKE ALL ON TABLES FROM zyvello_billing_staging;

GRANT SELECT, INSERT ON zyvello.billing_subscriptions TO zyvello_api_staging;
GRANT SELECT, UPDATE ON zyvello.billing_subscriptions TO zyvello_billing_staging;
GRANT SELECT, INSERT ON zyvello.billing_webhook_events TO zyvello_billing_staging;

CREATE POLICY billing_api_read ON zyvello.billing_subscriptions
    FOR SELECT TO zyvello_api_staging
    USING (zyvello.is_workspace_member(workspace_id));

CREATE POLICY billing_api_insert ON zyvello.billing_subscriptions
    FOR INSERT TO zyvello_api_staging
    WITH CHECK (
        zyvello.is_workspace_manager(workspace_id)
        AND created_by_auth_user_id = zyvello.current_auth_user_id()
        AND status = 'creating' AND provider_subscription_id IS NULL
        AND checkout_verified = FALSE AND paid_through IS NULL AND paid_count = 0
    );

CREATE POLICY billing_service_read ON zyvello.billing_subscriptions
    FOR SELECT TO zyvello_billing_staging USING (true);
CREATE POLICY billing_service_update ON zyvello.billing_subscriptions
    FOR UPDATE TO zyvello_billing_staging USING (true) WITH CHECK (true);
CREATE POLICY billing_service_events_read ON zyvello.billing_webhook_events
    FOR SELECT TO zyvello_billing_staging USING (true);
CREATE POLICY billing_service_events_insert ON zyvello.billing_webhook_events
    FOR INSERT TO zyvello_billing_staging WITH CHECK (true);

-- The authenticated API cannot UPDATE billing tables directly. These
-- narrowly-scoped functions only finalize the original owner's test intent
-- and never assign paid entitlement.
CREATE FUNCTION zyvello.finalize_billing_subscription(p_id UUID, p_provider_id TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE changed INTEGER;
BEGIN
    IF p_provider_id !~ '^sub_[A-Za-z0-9]{8,40}$' THEN RETURN FALSE; END IF;
    UPDATE zyvello.billing_subscriptions AS bs
       SET provider_subscription_id=p_provider_id, status='created', updated_at=now()
     WHERE bs.id=p_id AND bs.status='creating' AND bs.provider_subscription_id IS NULL
       AND bs.created_by_auth_user_id=zyvello.current_auth_user_id()
       AND zyvello.is_workspace_manager(bs.workspace_id);
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed = 1;
END
$$;

CREATE FUNCTION zyvello.mark_billing_checkout_verified(p_id UUID, p_payment_id TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE changed INTEGER;
BEGIN
    IF p_payment_id !~ '^pay_[A-Za-z0-9]{8,40}$' THEN RETURN FALSE; END IF;
    UPDATE zyvello.billing_subscriptions AS bs
       SET checkout_verified=TRUE, latest_checkout_payment_id=p_payment_id, updated_at=now()
     WHERE bs.id=p_id AND bs.provider_subscription_id IS NOT NULL
       AND bs.created_by_auth_user_id=zyvello.current_auth_user_id()
       AND zyvello.is_workspace_manager(bs.workspace_id);
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed = 1;
END
$$;

REVOKE ALL ON FUNCTION zyvello.finalize_billing_subscription(UUID,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION zyvello.mark_billing_checkout_verified(UUID,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zyvello.finalize_billing_subscription(UUID,TEXT) TO zyvello_api_staging;
GRANT EXECUTE ON FUNCTION zyvello.mark_billing_checkout_verified(UUID,TEXT) TO zyvello_api_staging;

INSERT INTO zyvello.schema_migrations(version) VALUES (5)
ON CONFLICT (version) DO NOTHING;
