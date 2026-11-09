"""PostgreSQL-backed hosted tenant repository.

Every request connection uses the *non-owner* API role and a transaction-local
identity value derived only from a verified Neon JWT. No browser-provided
workspace ID or user ID is ever trusted for authorization.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Iterator
from uuid import UUID, uuid4


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")
MAX_WORKSPACES_PER_USER = 3
MAX_PENDING_JOBS_PER_WORKSPACE = 5


class StoreUnavailable(Exception):
    pass


class StoreConflict(Exception):
    pass


class StoreNotFound(Exception):
    pass


class StoreLimit(Exception):
    pass


class HostedStore:
    def __init__(self, dsn: str) -> None:
        if not dsn.startswith(("postgresql://", "postgres://")):
            raise ValueError("A PostgreSQL connection URL is required")
        self._dsn = dsn

    @contextmanager
    def transaction(self, auth_user_id: str) -> Iterator[Any]:
        if not auth_user_id or len(auth_user_id) > 200:
            raise ValueError("Verified identity is required")
        import psycopg
        from psycopg.rows import dict_row

        # Short-lived connections permit Neon to scale to zero. This API is
        # deliberately limited to small staging traffic; pool when measured.
        try:
            with psycopg.connect(self._dsn, connect_timeout=5, row_factory=dict_row) as conn:
                with conn.transaction():
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT current_user AS role")
                        if cursor.fetchone()["role"] != "zyvello_api_staging":
                            raise StoreUnavailable("Unexpected database role")
                        cursor.execute("SELECT set_config('zyvello.auth_user_id', %s, true)", (auth_user_id,))
                        yield cursor
        except psycopg.OperationalError as error:
            raise StoreUnavailable("Database connection failed") from error

    def ping(self) -> None:
        import psycopg
        try:
            with psycopg.connect(self._dsn, connect_timeout=5) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT current_user")
                    if cursor.fetchone()[0] != "zyvello_api_staging":
                        raise StoreUnavailable("Unexpected database role")
        except psycopg.OperationalError as error:
            raise StoreUnavailable("Database connection failed") from error

    def list_workspaces(self, auth_user_id: str) -> list[dict[str, Any]]:
        with self.transaction(auth_user_id) as cursor:
            cursor.execute("""
                SELECT w.id, w.slug, w.name, m.role, w.created_at
                FROM zyvello.workspaces AS w
                JOIN zyvello.memberships AS m ON m.workspace_id=w.id
                WHERE m.auth_user_id=%s
                ORDER BY w.created_at DESC LIMIT 20
            """, (auth_user_id,))
            return cursor.fetchall()

    def create_workspace(self, auth_user_id: str, *, slug: str, name: str) -> dict[str, Any]:
        from psycopg.errors import UniqueViolation
        if not SLUG_RE.fullmatch(slug):
            raise ValueError("Workspace slug must be 3-63 lowercase letters, digits or hyphens")
        name = name.strip()
        if not 1 <= len(name) <= 200:
            raise ValueError("Workspace name must be between 1 and 200 characters")
        workspace_id = uuid4()
        try:
            with self.transaction(auth_user_id) as cursor:
                # Serializes the user quota check across concurrent API calls.
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (auth_user_id,))
                cursor.execute("SELECT count(*) AS n FROM zyvello.memberships WHERE auth_user_id=%s", (auth_user_id,))
                if cursor.fetchone()["n"] >= MAX_WORKSPACES_PER_USER:
                    raise StoreLimit("Workspace limit reached for private beta")
                # Do not RETURNING before inserting membership: RLS SELECT
                # deliberately hides an unjoined workspace.
                cursor.execute("""
                    INSERT INTO zyvello.workspaces (id, slug, name, created_by_auth_user_id)
                    VALUES (%s, %s, %s, %s)
                """, (workspace_id, slug, name, auth_user_id))
                cursor.execute("""
                    INSERT INTO zyvello.memberships (workspace_id, auth_user_id, role)
                    VALUES (%s, %s, 'owner')
                """, (workspace_id, auth_user_id))
                cursor.execute("""
                    SELECT w.id, w.slug, w.name, m.role, w.created_at
                    FROM zyvello.workspaces w JOIN zyvello.memberships m ON m.workspace_id=w.id
                    WHERE w.id=%s AND m.auth_user_id=%s
                """, (workspace_id, auth_user_id))
                row = cursor.fetchone()
                if not row:
                    raise StoreUnavailable("Workspace creation could not be confirmed")
                return row
        except UniqueViolation as error:
            raise StoreConflict("Workspace slug already exists") from error

    def get_workspace(self, auth_user_id: str, workspace_id: UUID) -> dict[str, Any]:
        with self.transaction(auth_user_id) as cursor:
            cursor.execute("""
                SELECT w.id, w.slug, w.name, m.role, w.created_at
                FROM zyvello.workspaces w JOIN zyvello.memberships m ON m.workspace_id=w.id
                WHERE w.id=%s AND m.auth_user_id=%s
            """, (workspace_id, auth_user_id))
            row = cursor.fetchone()
            if not row:
                raise StoreNotFound("Workspace not found")
            return row

    def list_jobs(self, auth_user_id: str, workspace_id: UUID, *, limit: int = 25) -> list[dict[str, Any]]:
        with self.transaction(auth_user_id) as cursor:
            cursor.execute("SELECT id FROM zyvello.workspaces WHERE id=%s", (workspace_id,))
            if not cursor.fetchone():
                raise StoreNotFound("Workspace not found")
            cursor.execute("""
                SELECT id, kind, status, attempts, max_attempts, result, last_error,
                       created_at, updated_at, finished_at
                FROM zyvello.jobs WHERE workspace_id=%s
                ORDER BY created_at DESC LIMIT %s
            """, (workspace_id, min(max(limit, 1), 100)))
            return cursor.fetchall()

    def enqueue_health_job(self, auth_user_id: str, workspace_id: UUID, *, idempotency_key: str) -> dict[str, Any]:
        if not 1 <= len(idempotency_key) <= 200:
            raise ValueError("Idempotency key must be between 1 and 200 characters")
        with self.transaction(auth_user_id) as cursor:
            cursor.execute("SELECT id FROM zyvello.workspaces WHERE id=%s", (workspace_id,))
            if not cursor.fetchone():
                raise StoreNotFound("Workspace not found")
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 1))", (str(workspace_id),))
            cursor.execute("""
                SELECT id, status FROM zyvello.jobs
                WHERE workspace_id=%s AND kind='workspace_health' AND idempotency_key=%s
            """, (workspace_id, idempotency_key))
            old = cursor.fetchone()
            if old:
                return {"id": old["id"], "status": old["status"], "created": False}
            cursor.execute("""
                SELECT count(*) AS n FROM zyvello.jobs
                WHERE workspace_id=%s AND status IN ('queued','running')
            """, (workspace_id,))
            if cursor.fetchone()["n"] >= MAX_PENDING_JOBS_PER_WORKSPACE:
                raise StoreLimit("Too many pending jobs. Try again when one completes")
            job_id = uuid4()
            cursor.execute("""
                INSERT INTO zyvello.jobs (id, workspace_id, kind, idempotency_key, payload)
                VALUES (%s, %s, 'workspace_health', %s, '{}'::jsonb)
            """, (job_id, workspace_id, idempotency_key))
            return {"id": job_id, "status": "queued", "created": True}


def serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert database values for FastAPI without leaking internal objects."""
    from datetime import date, datetime
    return {
        key: value.isoformat() if isinstance(value, (datetime, date)) else str(value) if isinstance(value, UUID) else value
        for key, value in row.items()
    }
