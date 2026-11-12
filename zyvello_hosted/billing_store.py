"""Isolated tenant billing repository and signed-webhook ledger.

The request API role cannot grant payment entitlements. A *different* DB role
may reconcile authenticated Razorpay webhook events, and can only access the
two billing tables. No provider event is allowed to create a subscription.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from .razorpay_billing import Plan
from .repository import HostedStore, StoreConflict, StoreForbidden, StoreNotFound, StoreUnavailable


class BillingApiStore(HostedStore):
    def list_subscriptions(self, auth_user_id: str, workspace_id: UUID) -> list[dict[str, Any]]:
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            cursor.execute("""
                SELECT id, plan_code, status, provider_subscription_id,
                       checkout_verified, paid_through, paid_count, created_at,
                       (status='active' AND paid_through>now() AND paid_count>0 AND checkout_verified) AS entitled
                FROM zyvello.billing_subscriptions WHERE workspace_id=%s
                ORDER BY created_at DESC LIMIT 10
            """, (workspace_id,))
            return cursor.fetchall()

    def reserve_subscription(self, auth_user_id: str, workspace_id: UUID, *,
                             plan: Plan, idempotency_key: str) -> dict[str, Any]:
        if not 8 <= len(idempotency_key) <= 100:
            raise ValueError("Invalid billing idempotency key")
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            cursor.execute("""
                SELECT 1 FROM zyvello.memberships
                WHERE workspace_id=%s AND auth_user_id=%s AND role='owner'
            """, (workspace_id, auth_user_id))
            if not cursor.fetchone():
                raise StoreForbidden("Only the workspace owner can manage billing")
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 6))", (str(workspace_id),))
            cursor.execute("""
                SELECT id, plan_code, status, provider_subscription_id
                FROM zyvello.billing_subscriptions
                WHERE workspace_id=%s AND idempotency_key=%s
            """, (workspace_id, idempotency_key))
            previous = cursor.fetchone()
            if previous:
                if previous["plan_code"] != plan.code:
                    raise StoreConflict("Idempotency key belongs to a different plan")
                return {**previous, "created": False}
            cursor.execute("""
                SELECT 1 FROM zyvello.billing_subscriptions WHERE workspace_id=%s
                  AND status IN ('creating','created','authenticated','active','pending','paused')
                LIMIT 1
            """, (workspace_id,))
            if cursor.fetchone():
                raise StoreConflict("This workspace already has a subscription attempt; verify its status first")
            cursor.execute("""
                INSERT INTO zyvello.billing_subscriptions
                  (workspace_id, plan_code, amount_paise, provider_plan_id,
                   idempotency_key, created_by_auth_user_id)
                VALUES (%s,%s,%s,%s,%s,%s)
                RETURNING id, plan_code, status, provider_subscription_id
            """, (workspace_id, plan.code, plan.amount_paise, plan.razorpay_plan_id,
                  idempotency_key, auth_user_id))
            return {**cursor.fetchone(), "created": True}

    def finalize_subscription(self, auth_user_id: str, subscription_id: UUID,
                              provider_subscription_id: str) -> dict[str, Any]:
        with self.transaction(auth_user_id) as cursor:
            cursor.execute("SELECT zyvello.finalize_billing_subscription(%s,%s) AS changed",
                           (subscription_id, provider_subscription_id))
            if not cursor.fetchone()["changed"]:
                raise StoreConflict("Subscription intent could not be finalized")
            cursor.execute("""
                SELECT id, plan_code, status, provider_subscription_id
                FROM zyvello.billing_subscriptions WHERE id=%s
            """, (subscription_id,))
            result = cursor.fetchone()
            if not result:
                raise StoreUnavailable("Subscription could not be confirmed")
            return result

    def get_checkout_subscription(self, auth_user_id: str, workspace_id: UUID,
                                  subscription_id: UUID) -> dict[str, Any]:
        with self.transaction(auth_user_id) as cursor:
            self._require_workspace(cursor, workspace_id)
            cursor.execute("""
                SELECT id, provider_subscription_id, status
                FROM zyvello.billing_subscriptions WHERE id=%s AND workspace_id=%s
            """, (subscription_id, workspace_id))
            row = cursor.fetchone()
            if not row:
                raise StoreNotFound("Subscription not found")
            return row

    def mark_checkout_verified(self, auth_user_id: str, subscription_id: UUID,
                               payment_id: str) -> None:
        with self.transaction(auth_user_id) as cursor:
            cursor.execute("SELECT zyvello.mark_billing_checkout_verified(%s,%s) AS changed",
                           (subscription_id, payment_id))
            if not cursor.fetchone()["changed"]:
                raise StoreForbidden("Cannot verify this subscription")


EVENT_STATUSES = {
    "subscription.authenticated": "authenticated",
    "subscription.activated": "active",
    "subscription.charged": "active",
    "subscription.pending": "pending",
    "subscription.halted": "halted",
    "subscription.paused": "paused",
    "subscription.resumed": "active",
    "subscription.cancelled": "cancelled",
    "subscription.completed": "completed",
    "subscription.expired": "expired",
}

TERMINAL_SUBSCRIPTION_STATUSES = frozenset({"cancelled", "completed", "expired"})


def reconcile_subscription_event(sub: dict[str, Any], event: dict[str, Any],
                                 event_at: datetime) -> dict[str, Any] | None:
    """Calculate a safe state change without trusting webhook delivery order.

    A paid renewal can extend the recorded paid period, but it cannot resurrect
    a subscription that has already reached a terminal state. This function is
    deliberately pure so event ordering/retry scenarios can be tested without
    creating synthetic charges against Razorpay or customer rows in Neon.
    """
    state = EVENT_STATUSES.get(event["event_type"])
    captured = (event["event_type"] == "subscription.charged" and event["captured"]
                and event["amount_paise"] == sub["amount_paise"])
    recent = sub["last_event_at"] is None or event_at > sub["last_event_at"]
    if not state or not (recent or captured):
        return None

    status = sub["status"]
    if recent and not (event["event_type"] == "subscription.charged" and not captured):
        if status not in TERMINAL_SUBSCRIPTION_STATUSES:
            status = state
    paid_through = sub["paid_through"]
    paid_count = sub["paid_count"]
    if captured:
        next_end = datetime.fromtimestamp(event["period_end"], tz=timezone.utc)
        if paid_through is None or next_end > paid_through:
            paid_through = next_end
        paid_count = max(paid_count, event["paid_count"])
    return {"status": status, "paid_through": paid_through, "paid_count": paid_count,
            "last_event_at": event_at if recent else sub["last_event_at"]}


class BillingWebhookStore:
    def __init__(self, dsn: str) -> None:
        if not dsn.startswith(("postgresql://", "postgres://")):
            raise ValueError("A PostgreSQL connection URL is required")
        self._dsn = dsn

    def record(self, event: dict[str, Any]) -> str:
        """Deduplicate and reconcile in ONE transaction; never store raw PII."""
        import psycopg
        from psycopg.rows import dict_row

        event_at = datetime.fromtimestamp(event["event_time"], tz=timezone.utc)
        try:
            with psycopg.connect(self._dsn, connect_timeout=5, row_factory=dict_row) as conn:
                with conn.transaction():
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT current_user AS role")
                        if cursor.fetchone()["role"] != "zyvello_billing_staging":
                            raise StoreUnavailable("Unexpected billing database role")
                        # Serialize duplicate deliveries even on different HTTP workers.
                        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 7))",
                                       (event["event_id"],))
                        cursor.execute("SELECT resolution FROM zyvello.billing_webhook_events WHERE event_id=%s",
                                       (event["event_id"],))
                        prior = cursor.fetchone()
                        if prior:
                            return "duplicate"
                        cursor.execute("""
                            SELECT id, provider_plan_id, amount_paise, status,
                                   paid_count, paid_through, last_event_at
                            FROM zyvello.billing_subscriptions
                            WHERE provider_subscription_id=%s FOR UPDATE
                        """, (event["provider_subscription_id"],))
                        sub = cursor.fetchone()
                        resolution = "unmatched"
                        if sub and sub["provider_plan_id"] != event["provider_plan_id"]:
                            resolution = "plan_mismatch"
                        elif sub:
                            resolution = "ignored"
                            update = reconcile_subscription_event(sub, event, event_at)
                            if update is not None:
                                cursor.execute("""
                                    UPDATE zyvello.billing_subscriptions
                                       SET status=%s, paid_through=%s, paid_count=%s,
                                           last_event_at=%s, updated_at=now()
                                     WHERE id=%s
                                """, (update["status"], update["paid_through"], update["paid_count"],
                                      update["last_event_at"], sub["id"]))
                                resolution = "applied"
                        cursor.execute("""
                            INSERT INTO zyvello.billing_webhook_events
                                (event_id,event_type,provider_subscription_id,body_sha256,event_at,resolution)
                            VALUES (%s,%s,%s,%s,%s,%s)
                        """, (event["event_id"], event["event_type"],
                              event["provider_subscription_id"], event["body_sha256"],
                              event_at, resolution))
                        return resolution
        except psycopg.OperationalError as error:
            raise StoreUnavailable("Billing database temporarily unavailable") from error
