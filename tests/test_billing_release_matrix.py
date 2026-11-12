"""No-money billing release matrix: pure provider events and API fail closed.

Offline contract coverage only. Does NOT prove Razorpay merchant live approval,
a recurring mandate or customer-authorized renewal/refund events.
"""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.test_razorpay_billing import (
    FakeBillingApi, FakeGateway, FakeWebhookStore, settings, INTENT_ID, WORKSPACE_A,
)
from tests.test_hosted_workspace import FakeStore
from zyvello_hosted.auth import VerifiedIdentity, TokenRejected
from zyvello_hosted.billing_store import reconcile_subscription_event
from zyvello_hosted.health import create_app
from zyvello_hosted.razorpay_billing import BillingSettings, BillingUnavailable


T0 = datetime(2026, 11, 13, tzinfo=timezone.utc)
PERIOD_END = T0 + timedelta(days=30)
PAYMENT = "pay_12345678901234"


def sub():
    return {
        "status": "created", "paid_through": None, "paid_count": 0,
        "amount_paise": 149900, "last_event_at": None,
        "checkout_verified": False, "provider_subscription_id": "sub_12345678901234",
    }


def event(kind, when, *, paid_count=0, end=PERIOD_END, captured=False,
          amount=149900):
    return {
        "event_type": kind, "event_time": int(when.timestamp()),
        "paid_count": paid_count, "period_end": int(end.timestamp()),
        "captured": captured,
        "amount_paise": amount if captured else None,
        "payment_id": PAYMENT if captured else None,
    }


def apply(before, e, when):
    changed = reconcile_subscription_event(before, e, when)
    if changed is None:
        return before
    return {
        **before, **changed,
        "checkout_verified": before["checkout_verified"]
                             or changed["payment_confirmed"]
    }


def entitled(row, at):
    return (row["status"] == "active"
            and row["checkout_verified"]
            and row["paid_count"] > 0
            and row["paid_through"] is not None
            and row["paid_through"] > at
            and bool(row["provider_subscription_id"]))


class FailureRecoveryAndCancellationTests(unittest.TestCase):
    def test_pending_halted_paused_revoke_and_charge_recovers(self):
        paid = apply(sub(), event("subscription.charged",T0,captured=True,
                                   paid_count=1), T0)
        self.assertTrue(entitled(paid,T0+timedelta(days=1)))
        pending = apply(paid,event("subscription.pending",T0+timedelta(days=5)),
                        T0+timedelta(days=5))
        self.assertFalse(entitled(pending,T0+timedelta(days=5)))
        halted = apply(pending,event("subscription.halted",T0+timedelta(days=6)),
                       T0+timedelta(days=6))
        self.assertFalse(entitled(halted,T0+timedelta(days=6)))
        paused = apply(halted,event("subscription.paused",T0+timedelta(days=7)),
                       T0+timedelta(days=7))
        self.assertFalse(entitled(paused,T0+timedelta(days=7)))
        resumed = apply(paused,event("subscription.resumed",T0+timedelta(days=8)),
                        T0+timedelta(days=8))
        self.assertTrue(entitled(resumed,T0+timedelta(days=8)))
        renewed_at = T0+timedelta(days=30)
        renewed = apply(resumed,event("subscription.charged",renewed_at,
                    captured=True,paid_count=2,end=T0+timedelta(days=60)),
                    renewed_at)
        self.assertTrue(entitled(renewed,renewed_at))
        self.assertEqual(renewed["paid_count"],2)
        self.assertEqual(renewed["paid_through"],T0+timedelta(days=60))

    def test_unauthorized_bad_amount_and_old_charge_never_reenable(self):
        paid = apply(sub(),event("subscription.charged",T0,captured=True,
                                 paid_count=1),T0)
        halted_time = T0+timedelta(days=5)
        halted = apply(paid,event("subscription.halted",halted_time),halted_time)
        for wrong in [
            event("subscription.charged",halted_time+timedelta(hours=1),
                  captured=True,paid_count=2,amount=149899),
            event("subscription.charged",halted_time+timedelta(hours=1),
                  captured=False,paid_count=2),
            event("subscription.charged",T0,captured=True,paid_count=1),
        ]:
            with self.subTest(payment=wrong):
                changed=apply(halted,wrong,
                              datetime.fromtimestamp(wrong["event_time"],timezone.utc))
                self.assertFalse(entitled(changed,halted_time))
                self.assertEqual(changed["paid_count"],1)

    def test_cancellation_and_expiry_are_terminal_even_after_duplicate_paid_event(self):
        paid=apply(sub(),event("subscription.charged",T0,captured=True,
                               paid_count=1),T0)
        cancelled_at=T0+timedelta(days=30)
        ended=apply(paid,event("subscription.cancelled",cancelled_at),
                    cancelled_at)
        self.assertFalse(entitled(ended,cancelled_at))
        late=apply(ended,event("subscription.charged",cancelled_at+timedelta(days=1),
                       captured=True,paid_count=2,end=T0+timedelta(days=60)),
                   cancelled_at+timedelta(days=1))
        self.assertEqual(late["status"],"cancelled")
        self.assertFalse(entitled(late,cancelled_at+timedelta(days=1)))
        stale=apply(ended,event("subscription.resumed",T0+timedelta(days=1)),
                    T0+timedelta(days=1))
        self.assertEqual(stale["status"],"cancelled")
        expired=apply(paid,event("subscription.expired",cancelled_at),cancelled_at)
        self.assertFalse(entitled(expired,cancelled_at))

    def test_successful_checkout_without_signed_capture_never_unlocks(self):
        before=sub()
        before["checkout_verified"]=True
        activated=apply(before,event("subscription.activated",T0),T0)
        self.assertFalse(entitled(activated,T0))
        self.assertEqual(activated["paid_count"],0)

    def test_live_merchant_credentials_are_deliberately_unsupported(self):
        for mode in ("live","LIVE"):
            config=BillingSettings.from_env({
                "ZYVELLO_RAZORPAY_MODE":mode,
                "ZYVELLO_RAZORPAY_KEY_ID":"rzp_live_example",
                "ZYVELLO_RAZORPAY_KEY_SECRET":"secret_placeholder",
                "ZYVELLO_RAZORPAY_WEBHOOK_SECRET":"other_placeholder"
            })
            self.assertFalse(config.enabled)


class CancellationApiFailureTests(unittest.TestCase):
    def test_provider_unavailable_does_not_mark_subscription_cancelled(self):
        billing=FakeBillingApi()
        gw=FakeGateway()
        gw.cancel_subscription=lambda **kwargs: (_ for _ in ()).throw(
            BillingUnavailable("Simulated TEST gateway timeout"))

        def verify(t):
            if t!="good-a":
                raise TokenRejected()
            return VerifiedIdentity("test-auth-a","a@example.test")

        with patch("zyvello_hosted.health.NeonTokenVerifier",
                   return_value=SimpleNamespace(verify=verify)):
            app=create_app(auth_base_url="https://auth.example.test/db/auth",
                           store_override=FakeStore(),
                           subscription_enforcement_override=False,
                           billing_settings_override=settings(),
                           billing_api_override=billing,
                           billing_webhook_override=FakeWebhookStore(),
                           billing_gateway_override=gw)
            c=TestClient(app)
            auth={"Authorization":"Bearer good-a"}
            base=f"/api/v1/workspaces/{WORKSPACE_A}/billing/subscriptions"
            created=c.post(base,json={"plan_code":"starter",
                           "idempotency_key":"cancel-timeout-test-123"},headers=auth)
            self.assertEqual(created.status_code,201)
            cancelled=c.post(base+f"/{INTENT_ID}/cancel",headers=auth)
            self.assertEqual(cancelled.status_code,503)
            self.assertEqual(next(iter(billing.rows.values()))["status"],"created")


if __name__=="__main__":
    unittest.main()
