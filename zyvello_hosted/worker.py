"""Recoverable, database-leased Zyvello staging worker.

Health diagnostics are always available. Optional BYOK Groq work creates
reviewable drafts only, never performs email sending, prospect scraping or
mailbox operations. Unrecognized kinds fail without running code.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from .credentials import CredentialVault
from .groq_drafts import GroqDraftClient

logger = logging.getLogger("zyvello.worker")


class HostedWorker:
    def __init__(self, dsn: str, *, enable_ai_drafts: bool = False,
                 encryption_keys: str | None = None,
                 groq_client: GroqDraftClient | None = None) -> None:
        if not dsn.startswith(("postgresql://", "postgres://")):
            raise ValueError("A PostgreSQL worker connection URL is required")
        self._dsn = dsn
        self._name = "zyvello-" + str(uuid4())[:12]
        self._ai_enabled = enable_ai_drafts
        self._vault = CredentialVault(encryption_keys) if enable_ai_drafts else None
        self._groq = (groq_client or GroqDraftClient()) if enable_ai_drafts else None

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(self._dsn, connect_timeout=5, row_factory=dict_row)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT current_user AS role")
                if cursor.fetchone()["role"] != "zyvello_worker_staging":
                    raise RuntimeError("Unexpected worker database role")
            conn.commit()
            return conn
        except Exception:
            conn.close()
            raise

    def _claim(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.transaction():
                # An abandoned final attempt can never become runnable again;
                # mark it dead instead of leaving a permanent running lease.
                conn.execute("""
                    UPDATE zyvello.jobs
                    SET status='dead', lease_until=NULL, leased_by=NULL,
                        last_error=COALESCE(last_error, 'Worker lease expired'),
                        finished_at=now(), updated_at=now()
                    WHERE status='running' AND lease_until <= now()
                      AND attempts >= max_attempts
                """)
                cursor = conn.execute("""
                    WITH candidate AS (
                        SELECT id FROM zyvello.jobs
                        WHERE (
                            (status='queued' AND run_after<=now())
                            OR (status='running' AND lease_until<=now())
                        ) AND attempts < max_attempts
                        ORDER BY run_after, created_at
                        FOR UPDATE SKIP LOCKED LIMIT 1
                    )
                    UPDATE zyvello.jobs AS j
                    SET status='running', attempts=j.attempts+1,
                        lease_until=now() + interval '90 seconds',
                        leased_by=%s, updated_at=now()
                    FROM candidate WHERE j.id=candidate.id
                    RETURNING j.id, j.workspace_id, j.kind, j.payload,
                              j.attempts, j.max_attempts
                """, (self._name,))
                return cursor.fetchone()

    def _complete(self, job_id: Any, result: dict[str, Any] | None, error: str | None) -> None:
        from psycopg.types.json import Jsonb
        with self._connect() as conn:
            with conn.transaction():
                if error is None:
                    cursor = conn.execute("""
                        UPDATE zyvello.jobs
                        SET status='succeeded', result=%s, last_error=NULL,
                            lease_until=NULL, leased_by=NULL, finished_at=now(), updated_at=now()
                        WHERE id=%s AND status='running' AND leased_by=%s
                    """, (Jsonb(result or {}), job_id, self._name))
                else:
                    # Controlled text only. Never persist exception tracebacks,
                    # keys, external response bodies or customer input.
                    cursor = conn.execute("""
                        UPDATE zyvello.jobs
                        SET status=CASE WHEN attempts>=max_attempts THEN 'dead' ELSE 'queued' END,
                            run_after=now() + (15 * power(2, LEAST(attempts, 6))) * interval '1 second',
                            last_error=%s, result=NULL, lease_until=NULL, leased_by=NULL,
                            finished_at=CASE WHEN attempts>=max_attempts THEN now() ELSE NULL END,
                            updated_at=now()
                        WHERE id=%s AND status='running' AND leased_by=%s
                    """, (error[:200], job_id, self._name))
                if cursor.rowcount != 1:
                    raise RuntimeError("Job lease was lost before completion")

    def _execute(self, job: dict[str, Any]) -> dict[str, Any]:
        if job["kind"] == "workspace_health":
            return {
                "outcome": "workspace_queue_healthy",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "note": "This diagnostic did not invoke AI or send messages",
            }
        if job["kind"] == "groq_draft" and self._ai_enabled:
            return self._generate_draft(job)
        raise ValueError("Unsupported or disabled job kind")

    def _generate_draft(self, job: dict[str, Any]) -> dict[str, Any]:
        assert self._vault is not None and self._groq is not None
        workspace_id = job["workspace_id"]
        payload = job["payload"]
        try:
            campaign_id = UUID(payload["campaign_id"])
            contact_id = UUID(payload["contact_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Invalid draft job metadata") from error
        with self._connect() as conn:
            with conn.transaction():
                old = conn.execute("SELECT id FROM zyvello.generated_drafts WHERE job_id=%s",
                                   (job["id"],)).fetchone()
                if old:
                    return {"outcome": "draft_already_created", "draft_id": str(old["id"]), "sent": False}
                cursor = conn.execute("""
                    SELECT c.full_name, c.company, ca.goal, s.ciphertext
                    FROM zyvello.campaign_contacts cc
                    JOIN zyvello.contacts c ON c.workspace_id=cc.workspace_id AND c.id=cc.contact_id
                    JOIN zyvello.campaigns ca ON ca.workspace_id=cc.workspace_id AND ca.id=cc.campaign_id
                    JOIN zyvello.workspace_provider_secrets s
                      ON s.workspace_id=cc.workspace_id AND s.provider='groq'
                    WHERE cc.workspace_id=%s AND cc.campaign_id=%s AND cc.contact_id=%s
                """, (workspace_id, campaign_id, contact_id))
                details = cursor.fetchone()
                if not details:
                    raise ValueError("Campaign, contact or credential was removed")
                blocked = conn.execute("""
                    SELECT 1 FROM zyvello.contact_permission_events
                    WHERE workspace_id=%s AND contact_id=%s
                      AND action IN ('opted_out','suppressed') LIMIT 1
                """, (workspace_id, contact_id)).fetchone()
                if blocked:
                    return {"outcome": "suppressed_contact", "sent": False}
                # Releasing the DB connection before the external HTTP call
                # avoids holding row locks or consuming a Postgres connection.
                encrypted = details.pop("ciphertext")
        key = self._vault.open(encrypted)
        draft = self._groq.generate(api_key=key, contact_name=details["full_name"],
                                    company=details["company"], campaign_goal=details["goal"])
        with self._connect() as conn:
            with conn.transaction():
                # A suppression may arrive while the model is processing.
                blocked = conn.execute("""
                    SELECT 1 FROM zyvello.contact_permission_events
                    WHERE workspace_id=%s AND contact_id=%s
                      AND action IN ('opted_out','suppressed') LIMIT 1
                """, (workspace_id, contact_id)).fetchone()
                if blocked:
                    return {"outcome": "suppressed_contact", "sent": False}
                cursor = conn.execute("""
                    INSERT INTO zyvello.generated_drafts
                        (workspace_id, campaign_id, contact_id, job_id, draft_text)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (job_id) DO NOTHING
                    RETURNING id
                """, (workspace_id, campaign_id, contact_id, job["id"], draft))
                row = cursor.fetchone()
                if not row:
                    row = conn.execute("SELECT id FROM zyvello.generated_drafts WHERE job_id=%s",
                                       (job["id"],)).fetchone()
                if not row:
                    raise RuntimeError("Draft persistence failed")
                return {"outcome": "draft_created", "draft_id": str(row["id"]), "sent": False}

    def process_batch(self, *, limit: int = 10) -> dict[str, int]:
        succeeded = 0
        failed = 0
        for _ in range(max(0, min(limit, 10))):
            job = self._claim()
            if job is None:
                break
            try:
                result = self._execute(job)
            except Exception as error:
                logger.warning("Safe staging job handler failed for %s: %s", job["id"], type(error).__name__)
                self._complete(job["id"], None, "Job could not be processed")
                failed += 1
            else:
                self._complete(job["id"], result, None)
                succeeded += 1
        return {"succeeded": succeeded, "failed": failed}
