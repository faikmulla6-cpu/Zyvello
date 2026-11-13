-- Draft-only background AI jobs. No mailbox tokens or sending permissions.
-- Requires 0006 and a separate verified Neon child-branch migration test.

ALTER TABLE zyvello.jobs
    ADD CONSTRAINT jobs_workspace_id_unique UNIQUE (workspace_id, id);

CREATE TABLE zyvello.generated_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL,
    campaign_id UUID NOT NULL,
    contact_id UUID NOT NULL,
    job_id UUID NOT NULL UNIQUE,
    draft_text TEXT NOT NULL CHECK (char_length(draft_text) BETWEEN 1 AND 5000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES zyvello.campaigns(workspace_id, id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, contact_id)
        REFERENCES zyvello.contacts(workspace_id, id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, job_id)
        REFERENCES zyvello.jobs(workspace_id, id) ON DELETE CASCADE
);

CREATE INDEX drafts_workspace_created_idx ON zyvello.generated_drafts
    (workspace_id, created_at DESC);
ALTER TABLE zyvello.generated_drafts ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON zyvello.generated_drafts
    FROM zyvello_api_staging, zyvello_worker_staging, zyvello_billing_staging;
GRANT SELECT ON zyvello.generated_drafts TO zyvello_api_staging;
GRANT SELECT, INSERT ON zyvello.generated_drafts TO zyvello_worker_staging;

CREATE POLICY drafts_api_read ON zyvello.generated_drafts
    FOR SELECT TO zyvello_api_staging
    USING (zyvello.is_workspace_member(workspace_id));
CREATE POLICY drafts_worker_read ON zyvello.generated_drafts
    FOR SELECT TO zyvello_worker_staging USING (true);
CREATE POLICY drafts_worker_insert ON zyvello.generated_drafts
    FOR INSERT TO zyvello_worker_staging WITH CHECK (true);

-- Managers can queue bounded draft work, but never arbitrary job kinds.
CREATE POLICY jobs_api_insert_draft ON zyvello.jobs
    FOR INSERT TO zyvello_api_staging
    WITH CHECK (
        zyvello.is_workspace_manager(workspace_id)
        AND kind='groq_draft' AND status='queued'
        AND attempts=0 AND max_attempts <= 3
        AND payload ? 'campaign_id' AND payload ? 'contact_id'
        AND leased_by IS NULL AND lease_until IS NULL
        AND last_error IS NULL AND result IS NULL AND finished_at IS NULL
    );

-- Read-only worker access to the precise fields needed to construct a draft.
-- Worker is a trusted, separately-scoped internal role, not a user identity.
GRANT SELECT (workspace_id, id, name, goal) ON zyvello.campaigns TO zyvello_worker_staging;
GRANT SELECT (workspace_id, id, full_name, company) ON zyvello.contacts TO zyvello_worker_staging;
GRANT SELECT (workspace_id, campaign_id, contact_id) ON zyvello.campaign_contacts TO zyvello_worker_staging;
GRANT SELECT (id, workspace_id, contact_id, action, created_at)
    ON zyvello.contact_permission_events TO zyvello_worker_staging;

CREATE POLICY campaigns_worker_read ON zyvello.campaigns
    FOR SELECT TO zyvello_worker_staging USING (true);
CREATE POLICY contacts_worker_read ON zyvello.contacts
    FOR SELECT TO zyvello_worker_staging USING (true);
CREATE POLICY campaign_contacts_worker_read ON zyvello.campaign_contacts
    FOR SELECT TO zyvello_worker_staging USING (true);
CREATE POLICY permissions_worker_read ON zyvello.contact_permission_events
    FOR SELECT TO zyvello_worker_staging USING (true);

INSERT INTO zyvello.schema_migrations(version) VALUES (7)
ON CONFLICT (version) DO NOTHING;
