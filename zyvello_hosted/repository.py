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
MAX_CONTACTS_PER_WORKSPACE = 1000
MAX_CAMPAIGNS_PER_WORKSPACE = 50
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class StoreUnavailable(Exception):
    pass


class StoreConflict(Exception):
    pass


class StoreNotFound(Exception):
    pass


class StoreForbidden(Exception):
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
        except psycopg.errors.InsufficientPrivilege as error:
            raise StoreForbidden("You cannot modify this workspace") from error

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

    @staticmethod
    def _require_workspace(cursor: Any, workspace_id: UUID) -> None:
        # The workspaces RLS policy returns nothing for a nonmember.
        cursor.execute("SELECT 1 FROM zyvello.workspaces WHERE id=%s", (workspace_id,))
        if not cursor.fetchone():
            raise StoreNotFound("Workspace not found")

    def list_contacts(self, auth_user_id: str, workspace_id: UUID) -> list[dict[str, Any]]:
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            cursor.execute("""
                SELECT id, full_name, email, company, created_at
                FROM zyvello.contacts WHERE workspace_id=%s
                ORDER BY created_at DESC, id DESC LIMIT 100
            """, (workspace_id,))
            return cursor.fetchall()

    def create_contact(self, auth_user_id: str, workspace_id: UUID, *, full_name: str,
                       email: str, company: str = "") -> dict[str, Any]:
        from psycopg.errors import UniqueViolation
        full_name, company, email = full_name.strip(), company.strip(), email.strip().lower()
        if not 1 <= len(full_name) <= 160 or len(company) > 160:
            raise ValueError("Invalid contact name or company")
        if not 3 <= len(email) <= 254 or not EMAIL_RE.fullmatch(email):
            raise ValueError("Invalid contact email")
        try:
            with self.transaction(auth_user_id) as cursor:
                self._require_workspace(cursor, workspace_id)
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 2))", (str(workspace_id),))
                cursor.execute("SELECT count(*) AS n FROM zyvello.contacts WHERE workspace_id=%s", (workspace_id,))
                if cursor.fetchone()["n"] >= MAX_CONTACTS_PER_WORKSPACE:
                    raise StoreLimit("Contact limit reached for private beta")
                cursor.execute("""
                    INSERT INTO zyvello.contacts
                        (workspace_id, full_name, email, company, created_by_auth_user_id)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, full_name, email, company, created_at
                """, (workspace_id, full_name, email, company, auth_user_id))
                return cursor.fetchone()
        except UniqueViolation as error:
            raise StoreConflict("This email already exists in the workspace") from error

    def list_campaigns(self, auth_user_id: str, workspace_id: UUID) -> list[dict[str, Any]]:
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            cursor.execute("""
                SELECT id, name, goal, status, created_at
                FROM zyvello.campaigns WHERE workspace_id=%s
                ORDER BY created_at DESC, id DESC LIMIT 100
            """, (workspace_id,))
            return cursor.fetchall()

    def create_campaign(self, auth_user_id: str, workspace_id: UUID, *, name: str,
                        goal: str = "") -> dict[str, Any]:
        name, goal = name.strip(), goal.strip()
        if not 1 <= len(name) <= 160 or len(goal) > 1000:
            raise ValueError("Invalid campaign name or goal")
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 3))", (str(workspace_id),))
            cursor.execute("SELECT count(*) AS n FROM zyvello.campaigns WHERE workspace_id=%s", (workspace_id,))
            if cursor.fetchone()["n"] >= MAX_CAMPAIGNS_PER_WORKSPACE:
                raise StoreLimit("Draft campaign limit reached for private beta")
            cursor.execute("""
                INSERT INTO zyvello.campaigns
                    (workspace_id, name, goal, created_by_auth_user_id)
                VALUES (%s, %s, %s, %s)
                RETURNING id, name, goal, status, created_at
            """, (workspace_id, name, goal, auth_user_id))
            return cursor.fetchone()

    @staticmethod
    def _require_campaign(cursor: Any, workspace_id: UUID, campaign_id: UUID) -> None:
        cursor.execute("SELECT 1 FROM zyvello.campaigns WHERE workspace_id=%s AND id=%s",
                       (workspace_id, campaign_id))
        if not cursor.fetchone():
            raise StoreNotFound("Campaign not found")

    def list_campaign_contacts(self, auth_user_id: str, workspace_id: UUID,
                               campaign_id: UUID) -> list[dict[str, Any]]:
        with self.transaction(auth_user_id) as cursor:
            self._require_campaign(cursor, workspace_id, campaign_id)
            cursor.execute("""
                SELECT c.id, c.full_name, c.email, c.company, cc.added_at
                FROM zyvello.campaign_contacts cc
                JOIN zyvello.contacts c ON c.workspace_id=cc.workspace_id
                    AND c.id=cc.contact_id
                WHERE cc.workspace_id=%s AND cc.campaign_id=%s
                ORDER BY cc.added_at DESC LIMIT 100
            """, (workspace_id, campaign_id))
            return cursor.fetchall()

    def add_campaign_contact(self, auth_user_id: str, workspace_id: UUID,
                             campaign_id: UUID, contact_id: UUID) -> dict[str, Any]:
        with self.transaction(auth_user_id) as cursor:
            self._require_campaign(cursor, workspace_id, campaign_id)
            cursor.execute("SELECT 1 FROM zyvello.contacts WHERE workspace_id=%s AND id=%s",
                           (workspace_id, contact_id))
            if not cursor.fetchone():
                raise StoreNotFound("Contact not found")
            cursor.execute("""
                INSERT INTO zyvello.campaign_contacts(workspace_id, campaign_id, contact_id)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING contact_id
            """, (workspace_id, campaign_id, contact_id))
            return {"created": bool(cursor.fetchone()), "contact_id": contact_id}

    def get_groq_credential_status(self, auth_user_id: str, workspace_id: UUID) -> dict[str, Any]:
        with self.transaction(auth_user_id) as cursor:
            # Membership and manager checks both occur in RLS; non-managers
            # must never be told that a secret exists for their workspace.
            self._require_workspace(cursor, workspace_id)
            cursor.execute("SELECT provider, updated_at FROM zyvello.workspace_provider_secrets "
                           "WHERE workspace_id=%s AND provider='groq'", (workspace_id,))
            row = cursor.fetchone()
            return {"configured": bool(row), "updated_at": row["updated_at"] if row else None}

    def put_groq_credential(self, auth_user_id: str, workspace_id: UUID, *, ciphertext: str) -> dict[str, Any]:
        if not 100 <= len(ciphertext) <= 2048:
            raise ValueError("Encrypted credential size is invalid")
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            # The API role intentionally cannot SELECT ciphertext. PostgreSQL
            # UPSERT requires that privilege even when returning only a date.
            # A workspace-scoped lock plus DELETE/INSERT stays atomic, avoids
            # escalating privileges, and supports safe key rotation.
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 7))",
                           (str(workspace_id),))
            cursor.execute("DELETE FROM zyvello.workspace_provider_secrets "
                           "WHERE workspace_id=%s AND provider='groq'", (workspace_id,))
            cursor.execute("""
                INSERT INTO zyvello.workspace_provider_secrets
                    (workspace_id, provider, ciphertext, updated_by_auth_user_id)
                VALUES (%s, 'groq', %s, %s)
                RETURNING updated_at
            """, (workspace_id, ciphertext, auth_user_id))
            return {"configured": True, "updated_at": cursor.fetchone()["updated_at"]}

    def delete_groq_credential(self, auth_user_id: str, workspace_id: UUID) -> dict[str, Any]:
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            cursor.execute("DELETE FROM zyvello.workspace_provider_secrets "
                           "WHERE workspace_id=%s AND provider='groq'", (workspace_id,))
            return {"removed": cursor.rowcount == 1}

    def get_contact_permission(self, auth_user_id: str, workspace_id: UUID,
                               contact_id: UUID) -> dict[str, Any]:
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            cursor.execute("SELECT 1 FROM zyvello.contacts WHERE id=%s AND workspace_id=%s",
                           (contact_id, workspace_id))
            if not cursor.fetchone():
                raise StoreNotFound("Contact not found")
            cursor.execute("""
                SELECT action, source, evidence, created_at
                FROM zyvello.contact_permission_events
                WHERE workspace_id=%s AND contact_id=%s
                ORDER BY created_at DESC, id DESC LIMIT 1
            """, (workspace_id, contact_id))
            return cursor.fetchone() or {"action": "unknown", "source": None,
                                         "evidence": None, "created_at": None}

    def record_contact_permission(self, auth_user_id: str, workspace_id: UUID,
                                  contact_id: UUID, *, action: str, source: str,
                                  evidence: str = "") -> dict[str, Any]:
        if action not in {"claimed_consent", "opted_out", "suppressed"}:
            raise ValueError("Unsupported permission action")
        source, evidence = source.strip(), evidence.strip()
        if not 3 <= len(source) <= 100 or len(evidence) > 1000:
            raise ValueError("Invalid permission evidence")
        if action == "claimed_consent" and len(evidence) < 10:
            raise ValueError("Claimed consent requires a verifiable evidence reference")
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            cursor.execute("SELECT 1 FROM zyvello.contacts WHERE id=%s AND workspace_id=%s",
                           (contact_id, workspace_id))
            if not cursor.fetchone():
                raise StoreNotFound("Contact not found")
            # Suppression is sticky: an owner cannot silently overwrite an
            # opt-out with a self-asserted 'consent' event. Re-consent needs a
            # separately verified workflow not implemented in private beta.
            cursor.execute("""
                SELECT 1 FROM zyvello.contact_permission_events
                WHERE workspace_id=%s AND contact_id=%s
                  AND action IN ('opted_out', 'suppressed') LIMIT 1
            """, (workspace_id, contact_id))
            if action == "claimed_consent" and cursor.fetchone():
                raise StoreForbidden("Previously suppressed contact cannot be opted back in here")
            cursor.execute("""
                INSERT INTO zyvello.contact_permission_events
                    (workspace_id, contact_id, action, source, evidence, changed_by_auth_user_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING action, source, evidence, created_at
            """, (workspace_id, contact_id, action, source, evidence, auth_user_id))
            return cursor.fetchone()

    def list_generated_drafts(self, auth_user_id: str, workspace_id: UUID) -> list[dict[str, Any]]:
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            cursor.execute("""
                SELECT id, campaign_id, contact_id, job_id, draft_text, created_at
                FROM zyvello.generated_drafts WHERE workspace_id=%s
                ORDER BY created_at DESC LIMIT 50
            """, (workspace_id,))
            return cursor.fetchall()

    def enqueue_groq_draft(self, auth_user_id: str, workspace_id: UUID, *,
                           campaign_id: UUID, contact_id: UUID,
                           idempotency_key: str) -> dict[str, Any]:
        from psycopg.types.json import Jsonb

        if not 8 <= len(idempotency_key) <= 100 or not re.fullmatch(r"[A-Za-z0-9_-]+", idempotency_key):
            raise ValueError("Invalid idempotency key")
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            # A user with read-only membership cannot enqueue provider spend.
            cursor.execute("SELECT zyvello.is_workspace_manager(%s) AS manager", (workspace_id,))
            if not cursor.fetchone()["manager"]:
                raise StoreForbidden("Only a workspace manager can create drafts")
            self._require_campaign(cursor, workspace_id, campaign_id)
            cursor.execute("""
                SELECT 1 FROM zyvello.campaign_contacts
                WHERE workspace_id=%s AND campaign_id=%s AND contact_id=%s
            """, (workspace_id, campaign_id, contact_id))
            if not cursor.fetchone():
                raise StoreNotFound("Contact is not included in this draft campaign")
            cursor.execute("SELECT 1 FROM zyvello.workspace_provider_secrets "
                           "WHERE workspace_id=%s AND provider='groq'", (workspace_id,))
            if not cursor.fetchone():
                raise StoreConflict("Configure a workspace Groq key first")
            cursor.execute("""
                SELECT 1 FROM zyvello.contact_permission_events
                WHERE workspace_id=%s AND contact_id=%s
                  AND action IN ('opted_out','suppressed') LIMIT 1
            """, (workspace_id, contact_id))
            if cursor.fetchone():
                raise StoreForbidden("Suppressed contacts cannot receive outreach drafts")
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 8))",
                           (str(workspace_id),))
            cursor.execute("""
                SELECT id, status FROM zyvello.jobs WHERE workspace_id=%s
                  AND kind='groq_draft' AND idempotency_key=%s
            """, (workspace_id, idempotency_key))
            existing = cursor.fetchone()
            if existing:
                return {"id": existing["id"], "status": existing["status"], "created": False}
            cursor.execute("""
                SELECT count(*) AS n FROM zyvello.jobs
                WHERE workspace_id=%s AND kind='groq_draft'
                  AND created_at > now() - interval '24 hours'
            """, (workspace_id,))
            if cursor.fetchone()["n"] >= 20:
                raise StoreLimit("Daily AI draft limit reached")
            cursor.execute("""
                SELECT count(*) AS n FROM zyvello.jobs
                WHERE workspace_id=%s AND status IN ('queued','running')
            """, (workspace_id,))
            if cursor.fetchone()["n"] >= MAX_PENDING_JOBS_PER_WORKSPACE:
                raise StoreLimit("Too many pending jobs")
            job_id = uuid4()
            cursor.execute("""
                INSERT INTO zyvello.jobs
                    (id,workspace_id,kind,idempotency_key,payload,max_attempts)
                VALUES (%s,%s,'groq_draft',%s,%s,2)
            """, (job_id, workspace_id, idempotency_key,
                  Jsonb({"campaign_id": str(campaign_id), "contact_id": str(contact_id)})))
            return {"id": job_id, "status": "queued", "created": True}

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
