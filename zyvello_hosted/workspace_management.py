"""Verified-account workspace lifecycle, business settings, and safe team invitations.

These methods intentionally use the shared HostedStore.transaction() boundary:
the connection is the restricted API role and its identity is assigned from a
verified Neon Auth JWT in a transaction-local PostgreSQL setting.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Any
from uuid import UUID, uuid4

class WorkspaceManagementMixin:
    def bootstrap_workspace(self, auth_user_id: str) -> dict[str, Any]:
        """Idempotently give a newly verified user a free business workspace."""
        from .repository import StoreUnavailable
        with self.transaction(auth_user_id) as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (auth_user_id,))
            cursor.execute("""
                SELECT w.id, w.slug, w.name, w.settings, m.role, w.created_at
                FROM zyvello.workspaces w
                JOIN zyvello.memberships m ON m.workspace_id = w.id
                WHERE m.auth_user_id = %s
                  AND m.role = 'owner'
                  AND w.created_by_auth_user_id = %s
                ORDER BY w.created_at, w.id LIMIT 1
            """, (auth_user_id, auth_user_id))
            found = cursor.fetchone()
            if found:
                return {"workspace": found, "created": False}
            # Random slug avoids exposing a predictable user-id fingerprint.
            # The advisory lock makes concurrent bootstrap requests idempotent.
            slug = "business-" + secrets.token_hex(12)
            workspace_id = uuid4()
            cursor.execute("""
                INSERT INTO zyvello.workspaces(id, slug, name, created_by_auth_user_id)
                VALUES (%s, %s, 'My Business', %s)
            """, (workspace_id, slug, auth_user_id))
            cursor.execute("""
                INSERT INTO zyvello.memberships(workspace_id, auth_user_id, role)
                VALUES (%s, %s, 'owner')
            """, (workspace_id, auth_user_id))
            cursor.execute("""
                SELECT w.id, w.slug, w.name, w.settings, m.role, w.created_at
                FROM zyvello.workspaces w
                JOIN zyvello.memberships m ON m.workspace_id = w.id
                WHERE w.id = %s AND m.auth_user_id = %s
            """, (workspace_id, auth_user_id))
            row = cursor.fetchone()
            if row is None:
                raise StoreUnavailable("Workspace bootstrap was not confirmed")
            return {"workspace": row, "created": True}

    def update_workspace(self, auth_user_id: str, workspace_id: UUID,
                         *, name: str | None = None,
                         settings: dict[str, Any] | None = None) -> dict[str, Any]:
        from .repository import StoreForbidden, StoreNotFound
        from psycopg.types.json import Jsonb
        if name is not None:
            name = name.strip()
            if not 1 <= len(name) <= 200:
                raise ValueError("Workspace name must be 1-200 characters")
        if name is None and settings is None:
            raise ValueError("No workspace updates supplied")
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            cursor.execute("SELECT zyvello.is_workspace_manager(%s) AS allowed", (workspace_id,))
            if not cursor.fetchone()["allowed"]:
                raise StoreForbidden("Only owners and admins may edit settings")
            cursor.execute("""
                UPDATE zyvello.workspaces
                SET name = COALESCE(%s, name),
                    settings = CASE WHEN %s::jsonb IS NULL
                               THEN settings ELSE settings || %s::jsonb END
                WHERE id = %s
                RETURNING id, slug, name, settings, created_at
            """, (name, Jsonb(settings) if settings is not None else None,
                  Jsonb(settings) if settings is not None else None, workspace_id))
            row = cursor.fetchone()
            if not row:
                raise StoreNotFound("Workspace not found")
            cursor.execute("""
                SELECT role FROM zyvello.memberships
                WHERE workspace_id=%s AND auth_user_id=%s
            """, (workspace_id, auth_user_id))
            row["role"] = cursor.fetchone()["role"]
            return row

    def delete_empty_workspace(self, auth_user_id: str, workspace_id: UUID) -> dict[str, Any]:
        """Delete only an unused workspace with no paid billing or audit history."""
        from .repository import StoreConflict, StoreForbidden
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            cursor.execute("SELECT zyvello.is_workspace_owner(%s) AS allowed", (workspace_id,))
            if not cursor.fetchone()["allowed"]:
                raise StoreForbidden("Only the owner may delete a workspace")
            cursor.execute("SELECT zyvello.delete_empty_workspace(%s) AS deleted", (workspace_id,))
            if not cursor.fetchone()["deleted"]:
                raise StoreConflict(
                    "Workspace cannot be deleted with billing history, members, "
                    "invitations, contacts or jobs. Contact support for data retention."
                )
            return {"deleted": True}

    def list_workspace_members(self, auth_user_id: str, workspace_id: UUID) -> list[dict[str, Any]]:
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            cursor.execute("""
                SELECT auth_user_id, email, role, created_at
                FROM zyvello.workspace_member_directory(%s)
            """, (workspace_id,))
            return cursor.fetchall()

    def set_member_role(self, auth_user_id: str, workspace_id: UUID, member_id: str,
                        *, role: str) -> dict[str, Any]:
        from .repository import StoreForbidden, StoreNotFound
        if role not in ("admin", "member"):
            raise ValueError("Owners cannot be assigned through this endpoint")
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            cursor.execute("SELECT zyvello.is_workspace_owner(%s) AS owner", (workspace_id,))
            if not cursor.fetchone()["owner"]:
                raise StoreForbidden("Only the owner can change team roles")
            cursor.execute("""
                UPDATE zyvello.memberships SET role=%s
                WHERE workspace_id=%s AND auth_user_id=%s AND role IN ('member','admin')
                RETURNING auth_user_id, role, created_at
            """, (role, workspace_id, member_id))
            row = cursor.fetchone()
            if not row:
                raise StoreNotFound("Member is not editable")
            return row

    def remove_workspace_member(self, auth_user_id: str, workspace_id: UUID,
                                member_id: str) -> dict[str, str]:
        from .repository import StoreForbidden, StoreNotFound
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            cursor.execute("SELECT zyvello.is_workspace_owner(%s) AS owner", (workspace_id,))
            is_owner = cursor.fetchone()["owner"]
            if not is_owner and member_id != auth_user_id:
                raise StoreForbidden("Only owners can remove other members")
            cursor.execute("""
                DELETE FROM zyvello.memberships
                WHERE workspace_id=%s AND auth_user_id=%s AND role IN ('member','admin')
                RETURNING auth_user_id
            """, (workspace_id, member_id))
            row = cursor.fetchone()
            if not row:
                raise StoreNotFound("Member cannot be removed")
            return {"removed_auth_user_id": row["auth_user_id"]}

    @staticmethod
    def _require_workspace_manager(cursor: Any, workspace_id: UUID) -> None:
        from .repository import StoreForbidden
        cursor.execute("SELECT zyvello.is_workspace_manager(%s) AS manager", (workspace_id,))
        if not cursor.fetchone()["manager"]:
            raise StoreForbidden("Owner or admin role required")

    def list_workspace_invitations(self, auth_user_id: str, workspace_id: UUID) -> list[dict[str, Any]]:
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            self._require_workspace_manager(cursor, workspace_id)
            cursor.execute("""
                SELECT id, invitee_email, role, created_at, expires_at,
                       accepted_at, revoked_at
                FROM zyvello.workspace_invitations
                WHERE workspace_id=%s
                ORDER BY created_at DESC LIMIT 100
            """, (workspace_id,))
            return cursor.fetchall()

    def create_workspace_invitation(self, auth_user_id: str, workspace_id: UUID,
                                    *, email: str, role: str) -> dict[str, Any]:
        from .repository import EMAIL_RE, StoreConflict, StoreLimit
        email = email.strip().lower()
        if role not in ("member", "admin") or len(email) > 254 or not EMAIL_RE.fullmatch(email):
            raise ValueError("Invalid invitee email or team role")
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            self._require_workspace_manager(cursor, workspace_id)
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 4))", (str(workspace_id),))
            cursor.execute("""
                SELECT count(*) AS n FROM zyvello.workspace_invitations
                WHERE workspace_id=%s AND accepted_at IS NULL
                  AND revoked_at IS NULL AND expires_at>now()
            """, (workspace_id,))
            if cursor.fetchone()["n"] >= 20:
                raise StoreLimit("Maximum pending workspace invitations reached")
            cursor.execute("""
                SELECT 1 FROM zyvello.workspace_invitations
                WHERE workspace_id=%s AND invitee_email=%s AND accepted_at IS NULL
                  AND revoked_at IS NULL AND expires_at>now() LIMIT 1
            """, (workspace_id, email))
            if cursor.fetchone():
                raise StoreConflict("A pending invitation already exists for this email")
            cursor.execute("""
                INSERT INTO zyvello.workspace_invitations
                   (workspace_id, invitee_email, role, token_sha256,
                    invited_by_auth_user_id, expires_at)
                VALUES (%s, %s, %s, %s, %s, now() + interval '7 days')
                RETURNING id, invitee_email, role, created_at, expires_at
            """, (workspace_id, email, role, token_hash, auth_user_id))
            return {"invitation": cursor.fetchone(), "token": token}

    def revoke_workspace_invitation(self, auth_user_id: str, workspace_id: UUID,
                                    invite_id: UUID) -> dict[str, Any]:
        from .repository import StoreNotFound
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            self._require_workspace_manager(cursor, workspace_id)
            cursor.execute("""
                UPDATE zyvello.workspace_invitations SET revoked_at=now()
                WHERE workspace_id=%s AND id=%s
                  AND revoked_at IS NULL AND accepted_at IS NULL
                RETURNING id, revoked_at
            """, (workspace_id, invite_id))
            row = cursor.fetchone()
            if not row:
                raise StoreNotFound("Invitation not found or already used")
            return row

    def accept_workspace_invitation(self, auth_user_id: str, token: str) -> dict[str, Any]:
        from .repository import StoreNotFound
        if not 32 <= len(token) <= 128 or not token.isascii():
            raise ValueError("Invalid invitation token")
        hashed = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self.transaction(auth_user_id) as cursor:
            cursor.execute("""
                SELECT zyvello.accept_workspace_invitation(%s) AS workspace_id
            """, (hashed,))
            row = cursor.fetchone()
            workspace_id = row["workspace_id"] if row else None
            if workspace_id is None:
                raise StoreNotFound("Invitation invalid, expired or intended for another account")
        return self.get_workspace(auth_user_id, workspace_id)
