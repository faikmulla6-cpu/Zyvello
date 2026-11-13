"""Approved Zyvello INR prices, independent of Razorpay plan IDs or live billing."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.test_hosted_workspace import FakeStore, WORKSPACE_A
from tests.test_razorpay_billing import (
    FakeBillingApi, FakeGateway, FakeWebhookStore, PLAN_ID, settings,
)
from zyvello_hosted.auth import TokenRejected, VerifiedIdentity
from zyvello_hosted.billing_catalog import (
    APPROVED_MONTHLY_TIERS, FUTURE_AI_EMPLOYEE, approved_test_plan,
    public_monthly_catalog,
)
from zyvello_hosted.health import create_app
from zyvello_hosted.razorpay_billing import BillingSettings


class ApprovedPricingTests(unittest.TestCase):
    def test_three_approved_monthly_inr_prices_are_exact(self):
        self.assertEqual(
            [(tier.code, tier.amount_paise) for tier in APPROVED_MONTHLY_TIERS.values()],
            [("starter", 49_900), ("growth", 149_900), ("business", 399_900)],
        )
        self.assertFalse(FUTURE_AI_EMPLOYEE["available_for_purchase"])
        self.assertEqual(FUTURE_AI_EMPLOYEE["starting_at_amount_paise"], 999_900)
        self.assertTrue(all(item["live_checkout_available"] is False
                            for item in public_monthly_catalog()))
        self.assertEqual(
            [item["limits"] for item in public_monthly_catalog()],
            [
                {"stored_contacts":100, "stored_campaign_drafts":5,
                 "workspace_members_including_owner":1},
                {"stored_contacts":400, "stored_campaign_drafts":20,
                 "workspace_members_including_owner":3},
                {"stored_contacts":1000, "stored_campaign_drafts":50,
                 "workspace_members_including_owner":10},
            ],
        )
        for tier in public_monthly_catalog():
            self.assertIn("Outbound email sending", tier["unavailable_features"])
            self.assertIn("AI-generated content", tier["unavailable_features"])

    def test_only_matching_provider_test_plan_is_checkout_ready(self):
        matching = settings().plans["starter"]
        self.assertTrue(approved_test_plan(matching, "starter"))
        catalog = public_monthly_catalog({"starter": matching})
        self.assertEqual([item["test_checkout_available"] for item in catalog],
                         [True, False, False])
        legacy = BillingSettings.from_env({
            "ZYVELLO_RAZORPAY_MODE": "test",
            "ZYVELLO_RAZORPAY_KEY_ID": "rzp_test_ExampleOnly",
            "ZYVELLO_RAZORPAY_KEY_SECRET": "test_api_secret_fixture",
            "ZYVELLO_RAZORPAY_WEBHOOK_SECRET": "test_webhook_secret_fixture",
            "ZYVELLO_RAZORPAY_PLANS_JSON": json.dumps({
                "agency": {"name": "Old agency", "amount_paise": 499_900,
                           "razorpay_plan_id": PLAN_ID}
            })
        })
        self.assertTrue(legacy.enabled)
        self.assertEqual(len(public_monthly_catalog(legacy.plans)), 3)
        self.assertFalse(any(item["test_checkout_available"]
                             for item in public_monthly_catalog(legacy.plans)))
        wrong_price = BillingSettings.from_env({
            "ZYVELLO_RAZORPAY_MODE": "test",
            "ZYVELLO_RAZORPAY_KEY_ID": "rzp_test_ExampleOnly",
            "ZYVELLO_RAZORPAY_KEY_SECRET": "test_api_secret_fixture",
            "ZYVELLO_RAZORPAY_WEBHOOK_SECRET": "test_webhook_secret_fixture",
            "ZYVELLO_RAZORPAY_PLANS_JSON": json.dumps({
                "starter": {"name": "Old starter", "amount_paise": 9_900,
                            "razorpay_plan_id": PLAN_ID}
            })
        })
        self.assertFalse(approved_test_plan(wrong_price.plans["starter"], "starter"))

    def test_api_displays_all_approved_prices_but_refuses_wrong_checkout(self):
        legacy = BillingSettings.from_env({
            "ZYVELLO_RAZORPAY_MODE": "test",
            "ZYVELLO_RAZORPAY_KEY_ID": "rzp_test_ExampleOnly",
            "ZYVELLO_RAZORPAY_KEY_SECRET": "test_api_secret_fixture",
            "ZYVELLO_RAZORPAY_WEBHOOK_SECRET": "test_webhook_secret_fixture",
            "ZYVELLO_RAZORPAY_PLANS_JSON": json.dumps({
                "agency": {"name": "Old QA agency", "amount_paise": 499_900,
                           "razorpay_plan_id": PLAN_ID}
            })
        })

        def verify(token):
            if token != "qa-owner":
                raise TokenRejected
            return VerifiedIdentity("test-auth-a", "a@example.test")

        with patch("zyvello_hosted.health.NeonTokenVerifier",
                   return_value=SimpleNamespace(verify=verify)):
            gateway = FakeGateway()
            app = create_app(
                auth_base_url="https://identity.example.test/db/auth",
                store_override=FakeStore(), subscription_enforcement_override=True,
                billing_settings_override=legacy, billing_api_override=FakeBillingApi(),
                billing_webhook_override=FakeWebhookStore(), billing_gateway_override=gateway,
            )
            client = TestClient(app)
            public = client.get("/api/v1/pricing")
            self.assertEqual(public.status_code, 200)
            self.assertEqual([x["amount_paise"] for x in public.json()["plans"]],
                             [49_900, 149_900, 399_900])
            self.assertFalse(public.json()["live_payments_enabled"])
            self.assertFalse(public.json()["future_tiers"][0]["available_for_purchase"])

            endpoint = f"/api/v1/workspaces/{WORKSPACE_A}/billing"
            header = {"Authorization": "Bearer qa-owner"}
            self.assertEqual(client.get(endpoint + "/plans").status_code, 401)
            configured = client.get(endpoint + "/plans", headers=header)
            self.assertEqual(configured.status_code, 200)
            self.assertEqual([p["test_checkout_available"] for p in configured.json()["plans"]],
                             [False, False, False])

            url = endpoint + "/subscriptions"
            for code, expected in [("agency", 422), ("starter", 409), ("growth", 409),
                                   ("business", 409), ("ai_employee", 422)]:
                with self.subTest(plan=code):
                    result = client.post(url, headers=header, json={
                        "plan_code": code, "idempotency_key": "approved-price-check-12345"})
                    self.assertEqual(result.status_code, expected)
            self.assertEqual(gateway.calls, 0)


if __name__ == "__main__":
    unittest.main()
