"""Owner-approved Zyvello monthly catalog (13 November 2026).

These are base INR prices, *before any applicable tax*. Annual pricing,
entitlements/quotas, and live checkout require separate sign-off.
Never invent a Razorpay plan ID or infer payment readiness from this catalog.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ApprovedMonthlyTier:
    code: str
    name: str
    amount_paise: int
    max_contacts: int
    max_campaign_drafts: int
    max_members: int

    def public(self, *, test_checkout_available: bool = False,
               live_checkout_available: bool = False) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "amount_paise": self.amount_paise,
            "currency": "INR",
            "period": "monthly",
            "interval": 1,
            "taxes": "additional_if_applicable",
            "test_checkout_available": test_checkout_available,
            "live_checkout_available": live_checkout_available,
            "limits": {
                "stored_contacts": self.max_contacts,
                "stored_campaign_drafts": self.max_campaign_drafts,
                "workspace_members_including_owner": self.max_members,
            },
            "available_features": [
                "Manual contact management", "Campaign draft planning",
                "Workspace health checks", "Workspace member access",
            ],
            "unavailable_features": [
                "Automatic lead discovery", "AI-generated content",
                "Outbound email sending", "Reply handling and autonomous sales agents",
            ],
        }


APPROVED_MONTHLY_TIERS: dict[str, ApprovedMonthlyTier] = {
    "starter": ApprovedMonthlyTier("starter", "Starter", 49_900, 100, 5, 1),
    "growth": ApprovedMonthlyTier("growth", "Growth", 149_900, 400, 20, 3),
    "business": ApprovedMonthlyTier("business", "Business", 399_900, 1000, 50, 10),
}

# A future, unshipped capability. Not a sellable Razorpay plan or promise of
# features. Amount is a provisional starting price, not a fixed checkout rate.
FUTURE_AI_EMPLOYEE = {
    "code": "ai_employee",
    "name": "AI Employee",
    "starting_at_amount_paise": 999_900,
    "currency": "INR",
    "period": "monthly",
    "status": "future",
    "available_for_purchase": False,
}


def approved_monthly_plan(configured_plan: Any, code: str) -> bool:
    """Require an exact approved price and monthly cadence before checkout.

    RazorpayGateway separately validates the *actual* plan ID/currency
    and amount against the provider before creating a subscription.
    """
    tier = APPROVED_MONTHLY_TIERS.get(code)
    return bool(
        tier
        and configured_plan is not None
        and configured_plan.code == code
        and configured_plan.amount_paise == tier.amount_paise
        and configured_plan.period == "monthly"
        and configured_plan.interval == 1
    )


approved_test_plan = approved_monthly_plan


def public_monthly_catalog(configured_plans: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    configured = configured_plans or {}
    return [
        tier.public(test_checkout_available=approved_monthly_plan(configured.get(code), code))
        for code, tier in APPROVED_MONTHLY_TIERS.items()
    ]
