"""Offline renewal and failed-payment state-machine tests (no gateway charges)."""

import unittest
from datetime import datetime, timedelta, timezone

from zyvello_hosted.billing_store import reconcile_subscription_event


NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)


def subscription():
    return {
        "status": "created", "paid_through": None, "paid_count": 0,
        "amount_paise": 499900, "last_event_at": None,
    }


def event(kind, at, *, count=0, end=None, captured=False, amount=499900):
    return {
        "event_type": kind, "event_time": int(at.timestamp()),
        "paid_count": count, "period_end": int(end.timestamp()) if end else None,
        "captured": captured, "amount_paise": amount if captured else None,
        "payment_id": "pay_12345678901234" if captured else None,
    }


def apply(sub, kind, at, **kwargs):
    update = reconcile_subscription_event(sub, event(kind, at, **kwargs), at)
    return {**sub, **update} if update else sub


class SubscriptionLifecycleTests(unittest.TestCase):
    def test_activation_without_captured_payment_is_not_entitlement(self):
        sub = apply(subscription(), "subscription.activated", NOW)
        self.assertEqual(sub["status"], "active")
        self.assertIsNone(sub["paid_through"])
        self.assertEqual(sub["paid_count"], 0)

    def test_successful_first_charge_then_a_second_monthly_renewal(self):
        first_end = NOW + timedelta(days=30)
        second_end = first_end + timedelta(days=30)
        sub = apply(subscription(), "subscription.charged", NOW, captured=True,
                    count=1, end=first_end)
        self.assertEqual((sub["status"], sub["paid_count"], sub["paid_through"]),
                         ("active", 1, first_end))
        self.assertTrue(sub["payment_confirmed"])
        self.assertEqual(sub["confirmed_payment_id"], "pay_12345678901234")
        sub = apply(sub, "subscription.charged", NOW + timedelta(days=30),
                    captured=True, count=2, end=second_end)
        self.assertEqual((sub["status"], sub["paid_count"], sub["paid_through"]),
                         ("active", 2, second_end))

    def test_failed_charge_and_halted_restrict_access_until_recovery(self):
        first_end = NOW + timedelta(days=30)
        sub = apply(subscription(), "subscription.charged", NOW, captured=True,
                    count=1, end=first_end)
        failed = apply(sub, "subscription.charged", NOW + timedelta(days=30),
                       count=2, end=first_end + timedelta(days=30), captured=False)
        self.assertEqual(failed["paid_count"], 1)
        self.assertEqual(failed["paid_through"], first_end)
        pending = apply(failed, "subscription.pending", NOW + timedelta(days=31))
        halted = apply(pending, "subscription.halted", NOW + timedelta(days=34))
        self.assertEqual(halted["status"], "halted")
        resumed = apply(halted, "subscription.resumed", NOW + timedelta(days=35))
        self.assertEqual(resumed["status"], "active")
        self.assertLess(resumed["paid_through"], NOW + timedelta(days=35))
        renewed = apply(resumed, "subscription.charged", NOW + timedelta(days=36),
                        captured=True, count=2, end=NOW + timedelta(days=60))
        self.assertEqual(renewed["paid_count"], 2)
        self.assertEqual(renewed["paid_through"], NOW + timedelta(days=60))

    def test_old_out_of_order_charge_cannot_roll_back_newer_period(self):
        sub = apply(subscription(), "subscription.charged", NOW + timedelta(days=30),
                    captured=True, count=2, end=NOW + timedelta(days=60))
        previous_time = sub["last_event_at"]
        out_of_order = apply(sub, "subscription.charged", NOW, captured=True,
                             count=1, end=NOW + timedelta(days=30))
        self.assertEqual(out_of_order["status"], "active")
        self.assertEqual(out_of_order["paid_count"], 2)
        self.assertEqual(out_of_order["paid_through"], NOW + timedelta(days=60))
        self.assertEqual(out_of_order["last_event_at"], previous_time)
        older_pending = apply(out_of_order, "subscription.pending", NOW)
        self.assertEqual(older_pending, out_of_order)

    def test_stale_paid_event_does_not_override_newer_payment_failure(self):
        first_end = NOW + timedelta(days=30)
        active = apply(subscription(), "subscription.charged", NOW,
                       captured=True, count=1, end=first_end)
        pending = apply(active, "subscription.pending", NOW + timedelta(days=29))
        self.assertEqual(pending["status"], "pending")
        stale = apply(pending, "subscription.charged", NOW + timedelta(days=15),
                      captured=True, count=1, end=first_end)
        self.assertEqual(stale["status"], "pending")
        self.assertEqual(stale["last_event_at"], pending["last_event_at"])
        self.assertEqual(stale["paid_through"], first_end)

    def test_cancelled_or_expired_subscriptions_cannot_be_resurrected(self):
        for status in ("cancelled", "completed", "expired"):
            with self.subTest(status=status):
                sub = apply(subscription(), "subscription.charged", NOW,
                            captured=True, count=1, end=NOW + timedelta(days=30))
                sub = apply(sub, f"subscription.{status}", NOW + timedelta(days=1))
                self.assertEqual(sub["status"], status)
                sub = apply(sub, "subscription.charged", NOW + timedelta(days=40),
                            captured=True, count=2, end=NOW + timedelta(days=70))
                self.assertEqual(sub["status"], status)
                self.assertEqual(sub["paid_count"], 2)

    def test_wrong_amount_never_extends_paid_period(self):
        sub = apply(subscription(), "subscription.charged", NOW, captured=True,
                    amount=499899, count=1, end=NOW + timedelta(days=30))
        self.assertIsNone(sub["paid_through"])
        self.assertEqual(sub["paid_count"], 0)
        self.assertEqual(sub["status"], "created")
        self.assertFalse(sub["payment_confirmed"])
        self.assertIsNone(sub["confirmed_payment_id"])


if __name__ == "__main__":
    unittest.main()
