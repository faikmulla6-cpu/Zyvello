"""Razorpay TEST-mode contracts; tests NEVER contact the payment gateway."""

import hashlib
import hmac
import json
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient

from tests.test_hosted_workspace import FakeStore, WORKSPACE_A, WORKSPACE_B
from zyvello_hosted.auth import TokenRejected, VerifiedIdentity
from zyvello_hosted.health import create_app
from zyvello_hosted.razorpay_billing import (
    BillingRejected, BillingUnavailable, BillingSettings, Plan, RazorpayGateway,
    parse_verified_webhook, verify_checkout_signature, verify_webhook_signature,
)
from zyvello_hosted.billing_store import BillingApiStore
from zyvello_hosted.repository import StoreConflict, StoreNotFound


SUB_ID = "sub_12345678901234"
PAY_ID = "pay_12345678901234"
PLAN_ID = "plan_12345678901234"
INTENT_ID = UUID("00000000-0000-4000-8000-0000000000f1")


def settings():
    return BillingSettings.from_env({
        "ZYVELLO_RAZORPAY_MODE": "test",
        "ZYVELLO_RAZORPAY_KEY_ID": "rzp_test_ExampleOnly",
        "ZYVELLO_RAZORPAY_KEY_SECRET": "test_api_secret_fixture",
        "ZYVELLO_RAZORPAY_WEBHOOK_SECRET": "test_webhook_secret_fixture",
        "ZYVELLO_RAZORPAY_PLANS_JSON": json.dumps({"starter": {
            "name": "Starter monthly", "amount_paise": 49900,
            "razorpay_plan_id": PLAN_ID, "period": "monthly", "interval": 1,
            "total_count": 12,
        }}),
    })


def event_body(event_type="subscription.charged", *, payment_status="captured", amount=49900):
    payload = {
        "event": event_type,
        "created_at": 1794340000,
        "payload": {"subscription": {"entity": {
            "id": SUB_ID, "plan_id": PLAN_ID,
            "paid_count": 1, "current_end": 1825876000,
        }}, "payment": {"entity": {
            "id": PAY_ID, "status": payment_status, "amount": amount, "currency": "INR",
        }}},
    }
    return json.dumps(payload, separators=(",", ":")).encode()


class RazorpayCryptoTests(unittest.TestCase):
    def test_disabled_without_keys_or_live_mode(self):
        self.assertFalse(BillingSettings.from_env({}).enabled)
        self.assertFalse(BillingSettings.from_env({"ZYVELLO_RAZORPAY_MODE": "live"}).enabled)
        self.assertFalse(BillingSettings.from_env({"ZYVELLO_RAZORPAY_MODE": "test"}).enabled)
        bad = {"ZYVELLO_RAZORPAY_MODE": "test", "ZYVELLO_RAZORPAY_KEY_ID": "rzp_live_wrong",
               "ZYVELLO_RAZORPAY_KEY_SECRET": "x", "ZYVELLO_RAZORPAY_WEBHOOK_SECRET": "x",
               "ZYVELLO_RAZORPAY_PLANS_JSON": "{}"}
        self.assertFalse(BillingSettings.from_env(bad).enabled)
        self.assertTrue(settings().enabled)
        self.assertEqual(settings().plans["starter"].amount_paise, 49900)

    def test_checkout_uses_payment_id_then_server_subscription_id(self):
        digest = hmac.new(settings().key_secret.encode(), f"{PAY_ID}|{SUB_ID}".encode(), hashlib.sha256).hexdigest()
        verify_checkout_signature(payment_id=PAY_ID, stored_subscription_id=SUB_ID,
                                  signature=digest, key_secret=settings().key_secret)
        with self.assertRaises(BillingRejected):
            verify_checkout_signature(payment_id=PAY_ID, stored_subscription_id=SUB_ID,
                                      signature="0" * 64, key_secret=settings().key_secret)
        with self.assertRaises(BillingRejected):
            verify_checkout_signature(payment_id=PAY_ID, stored_subscription_id=SUB_ID,
                                      signature="not-hex", key_secret=settings().key_secret)

    def test_webhook_uses_raw_body_and_rejects_tampering(self):
        body = event_body()
        signature = hmac.new(settings().webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        verify_webhook_signature(raw_body=body, signature=signature,
                                 webhook_secret=settings().webhook_secret)
        with self.assertRaises(BillingRejected):
            verify_webhook_signature(raw_body=body + b" ", signature=signature,
                                     webhook_secret=settings().webhook_secret)
        event = parse_verified_webhook(body, "evt_12345678901")
        self.assertTrue(event["captured"])
        self.assertEqual(event["amount_paise"], 49900)
        self.assertNotIn("raw_body", event)
        self.assertFalse(parse_verified_webhook(event_body(payment_status="authorized"), "evt_12345678902")["captured"])
        with self.assertRaises(BillingRejected):
            parse_verified_webhook(b"{}", "evt_12345678903")

    def test_failed_or_uncaptured_payments_never_qualify_for_entitlement(self):
        for index, change in enumerate((
            {"status": "failed"},
            {"status": "authorized"},
            {"currency": "USD"},
            {"amount": 0},
            {"id": "invalid_payment"},
        ), start=1):
            with self.subTest(change=change):
                raw = json.loads(event_body())
                raw["payload"]["payment"]["entity"].update(change)
                parsed = parse_verified_webhook(json.dumps(raw).encode(), f"evt_failed_{index:03d}")
                self.assertFalse(parsed["captured"])
                self.assertIsNone(parsed["amount_paise"])
                self.assertIsNone(parsed["payment_id"])

        for field, value in (("paid_count", 0), ("current_end", None)):
            with self.subTest(field=field):
                raw = json.loads(event_body())
                raw["payload"]["subscription"]["entity"][field] = value
                parsed = parse_verified_webhook(json.dumps(raw).encode(), "evt_uncaptured_01")
                self.assertFalse(parsed["captured"])

    def test_gateway_checks_remote_plan_price_before_creating_subscription(self):
        gateway = RazorpayGateway(settings())
        responses = [{"id": PLAN_ID, "item": {"amount": 49900, "currency": "INR"},
                      "period": "monthly", "interval": 1},
                     {"id": SUB_ID, "plan_id": PLAN_ID, "status": "created"}]
        with patch.object(gateway, "_request", side_effect=responses) as request:
            sub = gateway.create_subscription(plan=settings().plans["starter"], local_intent_id=str(INTENT_ID))
        self.assertEqual(sub, SUB_ID)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[1].args[:2], ("POST", "subscriptions"))


class HaltedRenewalReservationTests(unittest.TestCase):
    def test_halted_subscription_cannot_start_a_second_recurring_plan(self):
        """Halted is recoverable; a new plan could otherwise double-charge."""
        class Cursor:
            def __init__(self):
                self.last = None
                self.inserted = False

            def execute(self, sql, params=None):
                normal = " ".join(sql.split())
                if "INSERT INTO zyvello.billing_subscriptions" in normal:
                    self.inserted = True
                    raise AssertionError("Duplicate provider subscription intent created")
                if "SELECT 1 FROM zyvello.workspaces" in normal:
                    self.last = {"exists": 1}
                elif "SELECT 1 FROM zyvello.memberships" in normal:
                    self.last = {"exists": 1}
                elif "idempotency_key=%s" in normal:
                    self.last = None
                elif "status IN" in normal:
                    # The fixture has one recoverable halted subscription.
                    self.last = {"exists": 1} if "'halted'" in normal else None
                else:
                    self.last = None
                return self

            def fetchone(self):
                return self.last

        class Store(BillingApiStore):
            @contextmanager
            def transaction(self, auth_user_id):
                self.cursor = Cursor()
                yield self.cursor

        store = Store("postgresql://example.invalid/ci-only")
        with self.assertRaises(StoreConflict):
            store.reserve_subscription("test-auth-a", UUID(WORKSPACE_A),
                                       plan=settings().plans["starter"],
                                       idempotency_key="stalled-renewal-1234")
        self.assertFalse(store.cursor.inserted)


class FakeBillingApi:
    def __init__(self):
        self.rows = {}
        self.marked = []
        self.reservations = 0

    def list_subscriptions(self, user, workspace):
        if user != "test-auth-a" or str(workspace) != WORKSPACE_A:
            raise StoreNotFound("Workspace not found")
        return list(self.rows.values())

    def reserve_subscription(self, user, workspace, *, plan, idempotency_key):
        if user != "test-auth-a" or str(workspace) != WORKSPACE_A:
            raise StoreNotFound("Workspace not found")
        self.reservations += 1
        old = self.rows.get(idempotency_key)
        if old:
            return {**old, "created": False}
        row = {"id": INTENT_ID, "plan_code": plan.code, "status": "creating",
               "provider_subscription_id": None}
        self.rows[idempotency_key] = row
        return {**row, "created": True}

    def finalize_subscription(self, user, intent_id, subscription_id):
        row = next(row for row in self.rows.values() if row["id"] == intent_id)
        row.update(provider_subscription_id=subscription_id, status="created")
        return row

    def get_checkout_subscription(self, user, workspace, intent_id):
        if user != "test-auth-a" or str(workspace) != WORKSPACE_A:
            raise StoreNotFound("Workspace not found")
        return next(row for row in self.rows.values() if row["id"] == intent_id)

    def get_manageable_subscription(self, user, workspace, intent_id):
        if user != "test-auth-a" or str(workspace) != WORKSPACE_A:
            raise StoreNotFound("Workspace not found")
        found = next((row for row in self.rows.values() if row["id"] == intent_id), None)
        if found is None:
            raise StoreNotFound("Subscription not found")
        return {**found, "provider_plan_id": PLAN_ID}

    def mark_checkout_verified(self, user, intent_id, payment_id):
        self.marked.append(payment_id)


class FakeGateway:
    def __init__(self):
        self.calls = 0
        self.cancellations = []

    def create_subscription(self, *, plan, local_intent_id):
        self.calls += 1
        return SUB_ID

    def cancel_subscription(self, *, subscription_id, provider_plan_id, at_cycle_end):
        self.cancellations.append((subscription_id, provider_plan_id, at_cycle_end))
        return "active"


class FakeWebhookStore:
    def __init__(self):
        self.events = set()

    def record(self, event):
        duplicate = event["event_id"] in self.events
        self.events.add(event["event_id"])
        return "duplicate" if duplicate else "applied"


class RazorpayApiTests(unittest.TestCase):
    def setUp(self):
        self.billing = FakeBillingApi()
        self.gateway = FakeGateway()
        self.webhooks = FakeWebhookStore()

        def verify(token):
            if token == "good-a":
                return VerifiedIdentity("test-auth-a", "a@example.test")
            if token == "good-b":
                return VerifiedIdentity("test-auth-b", "b@example.test")
            raise TokenRejected

        self.patch = patch("zyvello_hosted.health.NeonTokenVerifier", return_value=SimpleNamespace(verify=verify))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.client = TestClient(create_app(
            auth_base_url="https://identity.example.test/db/auth", store_override=FakeStore(), subscription_enforcement_override=False,
            billing_settings_override=settings(), billing_api_override=self.billing,
            billing_webhook_override=self.webhooks, billing_gateway_override=self.gateway,
        ))

    @staticmethod
    def auth(token="good-a"):
        return {"Authorization": "Bearer " + token}

    def test_no_anonymous_or_cross_tenant_billing(self):
        path = f"/api/v1/workspaces/{WORKSPACE_A}/billing"
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get(path, headers=self.auth("good-b")).status_code, 404)
        self.assertEqual(self.client.get(path, headers=self.auth()).json()["subscriptions"], [])
        plans = self.client.get(path + "/plans", headers=self.auth())
        self.assertEqual(plans.status_code, 200)
        self.assertEqual(plans.json()["plans"][0]["amount_paise"], 49900)
        self.assertNotIn("razorpay_plan_id", plans.text)
        self.assertNotIn("test_api_secret_fixture", plans.text)
        self.assertEqual(self.client.get(path + "/plans", headers=self.auth("good-b")).status_code, 404)

    def test_create_once_verify_authorization_not_entitlement(self):
        path = f"/api/v1/workspaces/{WORKSPACE_A}/billing/subscriptions"
        body = {"plan_code": "starter", "idempotency_key": "intent-12345678"}
        first = self.client.post(path, json=body, headers=self.auth())
        again = self.client.post(path, json=body, headers=self.auth())
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["subscription_id"], SUB_ID)
        self.assertEqual(again.json()["subscription_id"], SUB_ID)
        self.assertEqual(self.gateway.calls, 1)
        self.assertEqual(self.client.post(path, json=body, headers=self.auth("good-b")).status_code, 404)
        self.assertEqual(self.client.post(path, json={**body, "plan_code": "unknown"},
                                          headers=self.auth()).status_code, 422)
        verify_path = path + f"/{INTENT_ID}/verify"
        signed = hmac.new(settings().key_secret.encode(), f"{PAY_ID}|{SUB_ID}".encode(), hashlib.sha256).hexdigest()
        result = self.client.post(verify_path, json={"razorpay_payment_id": PAY_ID,
                                                     "razorpay_signature": signed}, headers=self.auth())
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["entitlement_pending_webhook"])
        self.assertEqual(self.billing.marked, [PAY_ID])
        wrong = self.client.post(verify_path, json={"razorpay_payment_id": PAY_ID,
                                                    "razorpay_signature": "0" * 64}, headers=self.auth())
        self.assertEqual(wrong.status_code, 400)

    def test_cancel_only_workspace_owner_and_wait_for_verified_provider_webhook(self):
        base = f"/api/v1/workspaces/{WORKSPACE_A}/billing/subscriptions"
        first = self.client.post(base,
                                 json={"plan_code": "starter", "idempotency_key": "cancel-at-cycle-end-1"},
                                 headers=self.auth())
        self.assertEqual(first.status_code, 201)
        endpoint = base + f"/{INTENT_ID}/cancel"
        self.assertEqual(self.client.post(endpoint, headers=self.auth("good-b")).status_code, 404)
        self.assertEqual(self.gateway.cancellations, [])
        result = self.client.post(endpoint, headers=self.auth())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "cancellation_requested")
        self.assertTrue(result.json()["at_cycle_end"])
        self.assertTrue(result.json()["entitlement_pending_webhook"])
        self.assertEqual(self.gateway.cancellations, [(SUB_ID, PLAN_ID, True)])
        row = next(iter(self.billing.rows.values()))
        row["status"] = "cancelled"
        finished = self.client.post(endpoint, headers=self.auth())
        self.assertEqual(finished.status_code, 200)
        self.assertEqual(finished.json()["status"], "already_ended")
        self.assertEqual(len(self.gateway.cancellations), 1)

    def test_provider_cancel_uses_only_validated_subscription_id(self):
        gateway = RazorpayGateway(settings())
        with patch.object(gateway, "_request", return_value={
            "id": SUB_ID, "plan_id": PLAN_ID, "status": "active"}) as request:
            status = gateway.cancel_subscription(subscription_id=SUB_ID,
                                                 provider_plan_id=PLAN_ID,
                                                 at_cycle_end=True)
        self.assertEqual(status, "active")
        self.assertEqual(request.call_args.args[0:2], ("POST", "subscriptions/" + SUB_ID + "/cancel"))
        self.assertEqual(request.call_args.args[2], {"cancel_at_cycle_end": True})
        with self.assertRaises(BillingRejected):
            gateway.cancel_subscription(subscription_id="../untrusted", provider_plan_id=PLAN_ID)
        with patch.object(gateway, "_request", return_value={
            "id": SUB_ID, "plan_id": "plan_wrongplan12", "status": "active"}):
            with self.assertRaises(BillingUnavailable):
                gateway.cancel_subscription(subscription_id=SUB_ID, provider_plan_id=PLAN_ID)

    def test_webhook_requires_valid_hmac_and_is_replay_safe(self):
        path = "/api/v1/billing/razorpay/webhook"
        body = event_body()
        headers = {"x-razorpay-event-id": "evt_12345678901",
                   "x-razorpay-signature": hmac.new(settings().webhook_secret.encode(),
                                                     body, hashlib.sha256).hexdigest(),
                   "Content-Type": "application/json"}
        self.assertEqual(self.client.post(path, data=body).status_code, 400)
        self.assertEqual(self.client.post(path, data=body + b" ", headers=headers).status_code, 400)
        self.assertEqual(self.client.post(path, data=body, headers={**headers,
            "x-razorpay-event-id": "invalid event id"}).status_code, 400)
        self.assertEqual(self.client.post(path, data=b" " * (1024 * 1024 + 1),
                                          headers=headers).status_code, 413)
        self.assertEqual(self.webhooks.events, set())
        self.assertEqual(self.client.post(path, data=body, headers=headers).status_code, 200)
        self.assertEqual(self.client.post(path, data=body, headers=headers).status_code, 200)
        self.assertEqual(len(self.webhooks.events), 1)

    def test_billing_is_disabled_by_default(self):
        disabled = TestClient(create_app(auth_base_url="https://identity.example.test/db/auth",
                                         store_override=FakeStore(), subscription_enforcement_override=False,
                                         billing_settings_override=BillingSettings.from_env({})))
        self.assertFalse(disabled.get("/api/v1/capabilities").json()["payments_enabled"])
        self.assertEqual(disabled.get(f"/api/v1/workspaces/{WORKSPACE_A}/billing", headers=self.auth()).status_code, 503)
        self.assertEqual(disabled.post("/api/v1/billing/razorpay/webhook", data=event_body()).status_code, 503)


if __name__ == "__main__":
    unittest.main()
