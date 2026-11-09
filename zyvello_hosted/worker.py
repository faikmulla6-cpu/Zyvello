"""Recoverable, database-leased Zyvello staging worker.

The only registered job is an internal workspace health check. No prospect
collection, AI provider calls, billing, mailbox access, or email sending are
reachable from this worker. Unrecognized jobs go to dead-letter state.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger("zyvello.worker")


class HostedWorker:
    def __init__(self, dsn: str) -> None:
        if not dsn.startswith(("postgresql://", "postgres://")):
            raise ValueError("A PostgreSQL worker connection URL is required")
        self._dsn = dsn
        self._name = "zyvello-" + str(uuid4())[:12]

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
        if job["kind"] != "workspace_health":
            raise ValueError("Unsupported job kind")
        return {
            "outcome": "workspace_queue_healthy",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "note": "This diagnostic did not invoke AI or send messages",
        }

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
