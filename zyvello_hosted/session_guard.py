"""Fail closed on a revoked Neon managed-auth session or password reset.

JWT signatures prove who issued a token, not whether the underlying browser
session is still present. Access is gated with the *restricted* API DB role and
a boolean-only SECURITY DEFINER function. The PostgreSQL credentials and full
session rows are never sent to the client or logged.
"""

from __future__ import annotations

from .auth import VerifiedIdentity
from .repository import StoreUnavailable


class NeonSessionGuard:
    def __init__(self, dsn: str) -> None:
        if not dsn.startswith(("postgresql://", "postgres://")):
            raise ValueError("A PostgreSQL connection URL is required")
        self._dsn = dsn

    def __call__(self, identity: VerifiedIdentity) -> bool:
        if type(identity.issued_at) is not int or identity.issued_at < 1:
            return False

        import psycopg

        try:
            with psycopg.connect(
                self._dsn, connect_timeout=5
            ) as conn:
                with conn.cursor() as cursor:
                    # Neon pooled connections reject startup options=-c...
                    # but accept a transaction-scoped statement timeout.
                    cursor.execute("SET LOCAL statement_timeout = 4000")
                    cursor.execute("SELECT current_user")
                    if cursor.fetchone()[0] != "zyvello_api_staging":
                        raise StoreUnavailable("Unexpected session-check database role")
                    cursor.execute(
                        "SELECT zyvello.is_auth_session_current(%s, %s)",
                        (identity.user_id, identity.issued_at),
                    )
                    row = cursor.fetchone()
                    if row is None or type(row[0]) is not bool:
                        raise StoreUnavailable("Session-check database response was invalid")
                    return row[0]
        except psycopg.Error as cause:
            raise StoreUnavailable("Session verification database unavailable") from cause
