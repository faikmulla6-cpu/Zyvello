"""Verify the live pilot migration and billing roles on disposable PostgreSQL."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import unittest
from uuid import UUID

import psycopg

from rls_integration import execute_sql_file
from rls_paid_gates import INTENT_ID, WORKSPACE_A, WORKSPACE_B

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zyvello_hosted.billing_store import BillingApiStore, BillingWebhookStore
from zyvello_hosted.razorpay_billing import Plan
from zyvello_hosted.repository import StoreConflict, StoreNotFound


DB = os.getenv("TEST_POSTGRES_URL")
API = os.getenv("TEST_POSTGRES_API_URL")
BILLING = os.getenv("TEST_POSTGRES_BILLING_URL")


@unittest.skipUnless(DB and API and BILLING, "Disposable PostgreSQL not configured")
class LiveBillingSqlTests(unittest.TestCase):
    def test_only_scoped_live_capture_unlocks(self):
        with psycopg.connect(DB, autocommit=True) as admin:
            execute_sql_file(admin, "0015_razorpay_live_pilot.sql")
            self.assertEqual(admin.execute(
                "SELECT provider_mode FROM zyvello.billing_subscriptions WHERE id=%s",
                (INTENT_ID,),
            ).fetchone(), ("test",))

        api = BillingApiStore(API)
        billing = BillingWebhookStore(BILLING)
        workspace = UUID(WORKSPACE_B)
        plan = Plan("starter", "Starter", 49900, "plan_liveFixture01", "monthly", 1, 12)
        with self.assertRaises(StoreConflict):
            api.reserve_subscription("test-auth-a", UUID(WORKSPACE_A), plan=plan,
                                     idempotency_key="live-pilot-existing-test", provider_mode="live")
        row = api.reserve_subscription("test-auth-b", workspace, plan=plan,
                                       idempotency_key="live-pilot-ci-fixture", provider_mode="live")
        sub_id = "sub_liveFixture01"
        pay_id = "pay_liveFixture01"
        api.finalize_subscription("test-auth-b", row["id"], sub_id)
        retry = api.reserve_subscription("test-auth-b", workspace, plan=plan,
                                         idempotency_key="live-pilot-ci-retry", provider_mode="live")
        self.assertEqual((retry["id"], retry["created"]), (row["id"], False))
        with psycopg.connect(API) as conn:
            conn.execute("SELECT set_config('zyvello.auth_user_id','test-auth-b',true)")
            self.assertFalse(conn.execute(
                "SELECT zyvello.mark_billing_checkout_verified(%s,%s)", (row["id"], pay_id)
            ).fetchone()[0])
        self.assertFalse(api.has_paid_entitlement("test-auth-b", workspace))

        now = datetime.now(timezone.utc)
        charge = {
            "event_id": "evt_pilot_capture", "event_type": "subscription.charged",
            "event_time": int(now.timestamp()), "provider_subscription_id": sub_id,
            "provider_plan_id": plan.razorpay_plan_id, "body_sha256": "a" * 64,
            "captured": True, "payment_id": pay_id, "amount_paise": 49900, "paid_count": 1,
            "period_end": int((now + timedelta(days=30)).timestamp()),
        }
        self.assertEqual(billing.record(charge), "unmatched")
        self.assertEqual(billing.record({**charge, "event_id": "evt_wrong_workspace"},
                                        provider_mode="live", workspace_id=UUID(WORKSPACE_A)), "unmatched")
        self.assertEqual(billing.record({**charge, "event_id": "evt_wrong_amount", "amount_paise": 1},
                                        provider_mode="live", workspace_id=workspace), "ignored")
        self.assertEqual(billing.record({**charge, "event_id": "evt_auth_only",
                                        "event_type": "subscription.authenticated", "captured": False},
                                        provider_mode="live", workspace_id=workspace), "applied")
        self.assertFalse(api.has_paid_entitlement("test-auth-b", workspace))
        self.assertEqual(billing.record(charge, provider_mode="live", workspace_id=workspace), "applied")
        self.assertEqual(billing.record(charge, provider_mode="live", workspace_id=workspace), "duplicate")
        self.assertTrue(api.has_paid_entitlement("test-auth-b", workspace))
        with self.assertRaises(StoreNotFound):
            api.has_paid_entitlement("test-auth-a", workspace)
        self.assertEqual(billing.record({**charge, "event_id": "evt_pilot_cancel",
                                        "event_type": "subscription.cancelled", "captured": False,
                                        "event_time": charge["event_time"]},
                                        provider_mode="live", workspace_id=workspace), "applied")
        self.assertFalse(api.has_paid_entitlement("test-auth-b", workspace))


if __name__ == "__main__":
    unittest.main()
