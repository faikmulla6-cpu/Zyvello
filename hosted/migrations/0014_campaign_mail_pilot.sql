-- Delivery records and one-attempt reservations for the Gmail campaign pilot.
BEGIN;

CREATE TABLE zyvello.campaign_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL,
    campaign_id UUID NOT NULL,
    contact_id UUID NOT NULL,
    draft_id UUID NOT NULL REFERENCES zyvello.generated_drafts(id) ON DELETE CASCADE,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 160),
    status TEXT NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved','accepted','unconfirmed')),
    gmail_message_id TEXT,
    gmail_thread_id TEXT,
    last_sync_at TIMESTAMPTZ,
    last_reply_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, campaign_id, contact_id),
    FOREIGN KEY (workspace_id, campaign_id, contact_id)
        REFERENCES zyvello.campaign_contacts(workspace_id, campaign_id, contact_id) ON DELETE CASCADE
);
CREATE INDEX campaign_deliveries_workspace_recent
    ON zyvello.campaign_deliveries (workspace_id, created_at DESC);
ALTER TABLE zyvello.campaign_deliveries ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON zyvello.campaign_deliveries
    FROM PUBLIC, zyvello_api_staging, zyvello_worker_staging, zyvello_billing_staging;
GRANT SELECT ON zyvello.campaign_deliveries TO zyvello_api_staging;
GRANT UPDATE (status, gmail_message_id, gmail_thread_id, last_reply_at)
    ON zyvello.campaign_deliveries TO zyvello_api_staging;
CREATE POLICY delivery_manager_read ON zyvello.campaign_deliveries
    FOR SELECT TO zyvello_api_staging USING (zyvello.is_workspace_manager(workspace_id));
CREATE POLICY delivery_manager_update ON zyvello.campaign_deliveries
    FOR UPDATE TO zyvello_api_staging USING (zyvello.is_workspace_manager(workspace_id))
    WITH CHECK (zyvello.is_workspace_manager(workspace_id));

CREATE FUNCTION zyvello.reserve_campaign_delivery(
    p_workspace uuid, p_draft uuid, p_pilot_email text, p_subject text
)
RETURNS TABLE (delivery_id uuid, sender text, recipient text, refresh_ciphertext text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(p_workspace::text, 14));
    IF NOT zyvello.is_workspace_manager(p_workspace)
        OR NOT zyvello.is_api_workspace_entitled(p_workspace) THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM zyvello.campaign_deliveries AS sent
        WHERE sent.workspace_id=p_workspace AND sent.created_at > now() - interval '24 hours') THEN
        RETURN;
    END IF;

    RETURN QUERY
    WITH target AS (
        SELECT d.id, d.campaign_id, d.contact_id, c.email,
               m.email AS sender, m.refresh_ciphertext AS secret
        FROM zyvello.generated_drafts AS d
        JOIN zyvello.campaign_contacts AS cc
          ON (cc.workspace_id, cc.campaign_id, cc.contact_id) = (d.workspace_id, d.campaign_id, d.contact_id)
        JOIN zyvello.contacts AS c ON (c.workspace_id,c.id) = (d.workspace_id,d.contact_id)
        JOIN zyvello.gmail_mailboxes AS m ON m.workspace_id=d.workspace_id
        WHERE d.workspace_id=p_workspace AND d.id=p_draft AND c.email=p_pilot_email
          AND lower(m.email) <> c.email
          AND EXISTS (SELECT 1 FROM zyvello.contact_permission_events AS e
              WHERE e.workspace_id=p_workspace AND e.contact_id=c.id AND e.action='claimed_consent')
          AND NOT EXISTS (SELECT 1 FROM zyvello.contact_permission_events AS e
              WHERE e.workspace_id=p_workspace AND e.contact_id=c.id AND e.action IN ('opted_out','suppressed'))
    ), reserved AS (
        INSERT INTO zyvello.campaign_deliveries AS entry
            (workspace_id,campaign_id,contact_id,draft_id,sender,recipient,subject)
        SELECT p_workspace,t.campaign_id,t.contact_id,t.id,t.sender,t.email,p_subject FROM target AS t
        ON CONFLICT (workspace_id,campaign_id,contact_id) DO NOTHING
        RETURNING entry.id
    )
    SELECT r.id,t.sender,t.email,t.secret FROM reserved AS r CROSS JOIN target AS t;
END;
$$;

CREATE FUNCTION zyvello.reserve_campaign_reply_sync(p_workspace uuid, p_delivery uuid)
RETURNS TABLE (gmail_message_id text, gmail_thread_id text, recipient text,
               created_at timestamptz, refresh_ciphertext text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog AS $$
BEGIN
    IF NOT zyvello.is_workspace_manager(p_workspace)
        OR NOT zyvello.is_api_workspace_entitled(p_workspace) THEN
        RETURN;
    END IF;
    RETURN QUERY
    UPDATE zyvello.campaign_deliveries AS d SET last_sync_at=now()
    FROM zyvello.gmail_mailboxes AS m
    WHERE d.workspace_id=p_workspace AND d.id=p_delivery AND d.status='accepted'
      AND d.gmail_thread_id IS NOT NULL AND d.gmail_message_id IS NOT NULL
      AND m.workspace_id=p_workspace AND lower(m.email)=lower(d.sender)
      AND (d.last_sync_at IS NULL OR d.last_sync_at <= now() - interval '30 seconds')
    RETURNING d.gmail_message_id,d.gmail_thread_id,d.recipient,d.created_at,m.refresh_ciphertext;
END;
$$;

REVOKE ALL ON FUNCTION zyvello.reserve_campaign_delivery(uuid,uuid,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION zyvello.reserve_campaign_reply_sync(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zyvello.reserve_campaign_delivery(uuid,uuid,text,text) TO zyvello_api_staging;
GRANT EXECUTE ON FUNCTION zyvello.reserve_campaign_reply_sync(uuid,uuid) TO zyvello_api_staging;
INSERT INTO zyvello.schema_migrations(version) VALUES (14);
COMMIT;
