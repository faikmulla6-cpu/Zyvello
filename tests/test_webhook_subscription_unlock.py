"""Signed captured webhooks may restore paid access when the browser callback was lost.

All database/gateway calls are mocked: no test or real money moves.
"""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from zyvello_hosted.billing_store import BillingWebhookStore


class FakeCursor:
    def __init__(self):
        self.calls = []
        self.last = None

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "SELECT current_user" in sql:
            self.last = {"role": "zyvello_billing_staging"}
        elif "SELECT resolution" in sql:
            self.last = None
        elif "SELECT id, provider_plan_id" in sql:
            self.last = {"id": "00000000-0000-4000-8000-0000000000f1",
                         "provider_plan_id": "plan_12345678901234",
                         "amount_paise": 9900, "status": "created",
                         "paid_count": 0, "paid_through": None,
                         "last_event_at": None}
        else:
            self.last = None
        return self

    def fetchone(self): return self.last


class FakeConn:
    def __init__(self):
        self.cursor_instance = FakeCursor()

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def transaction(self): return self
    def cursor(self): return self.cursor_instance


def webhook(*, valid=True):
    return {"event_id": "evt_12345678901", "event_type": "subscription.charged",
            "event_time": 1794340000,
            "provider_subscription_id": "sub_12345678901234",
            "provider_plan_id": "plan_12345678901234",
            "paid_count": 1, "period_end": 1825876000,
            "captured": True,
            "payment_id": "pay_12345678901234",
            "amount_paise": 9900 if valid else 9899,
            "body_sha256": "1" * 64}


class SignedWebhookPaymentTests(unittest.TestCase):
    def process(self, event):
        conn = FakeConn()
        # The unit-test environment deliberately has no PostgreSQL driver.
        fake_psycopg = SimpleNamespace(connect=lambda *a, **k: conn,
                                       OperationalError=OSError)
        with patch.dict(sys.modules, {"psycopg": fake_psycopg,
                                      "psycopg.rows": SimpleNamespace(dict_row=dict)}):
            outcome = BillingWebhookStore("postgresql://example.test/demo").record(event)
        updates = [(sql, params) for sql, params in conn.cursor_instance.calls
                   if "UPDATE zyvello.billing_subscriptions" in sql]
        self.assertEqual(outcome, "applied")
        self.assertEqual(len(updates), 1)
        return updates[0]

    def test_provider_captured_charge_can_verify_without_browser_callback(self):
        sql, parameters = self.process(webhook())
        self.assertIn("checkout_verified=checkout_verified OR %s", sql)
        self.assertTrue(parameters[4])
        self.assertTrue(parameters[5])
        self.assertEqual(parameters[6], "pay_12345678901234")

    def test_wrong_price_does_not_mark_checkout_verified(self):
        _, parameters = self.process(webhook(valid=False))
        self.assertFalse(parameters[4])
        self.assertFalse(parameters[5])
        self.assertIsNone(parameters[6])


if __name__ == "__main__":
    unittest.main()
