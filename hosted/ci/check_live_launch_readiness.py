"""Fail-closed audit. NEVER turns live payments on."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

REQUIRED_EVIDENCE = (
    "merchant_activation_verified",
    "live_razorpay_subscriptions_approved",
    "business_legal_identity_verified",
    "customer_support_and_grievance_contact_verified",
    "gst_tax_invoicing_approved",
    "refund_and_cancellation_policy_legal_reviewed",
    "pricing_and_plan_quota_db_enforced",
    "public_site_matches_approved_catalog",
    "production_feature_claims_match_delivery",
    "provider_recurring_failure_qa_verified",
    "provider_cycle_end_cancellation_qa_verified",
    "provider_refund_and_dispute_qa_verified",
    "two_identity_tenant_acceptance_verified",
    "live_webhook_signing_and_replay_qa_verified",
    "release_observability_alerts_and_rollback_verified",
    "merchant_authorized_single_account_live_pilot",
)

def blockers(evidence: dict[str, Any]) -> list[str]:
    missing = [item for item in REQUIRED_EVIDENCE if evidence.get(item) is not True]
    if evidence.get("mode") != "live":
        missing.append("mode must be intentionally set to live after approvals")
    if evidence.get("manual_operator_signoff") is not True:
        missing.append("manual_operator_signoff")
    if not isinstance(evidence.get("signed_release_reference"),str) or not evidence["signed_release_reference"].strip():
        missing.append("signed_release_reference")
    return missing

if __name__ == "__main__":
    path = Path(__file__).resolve().parents[2] / "docs" / "live_launch_evidence.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    pending = blockers(payload)
    if pending:
        print("LIVE LAUNCH BLOCKED: " + str(len(pending)) + " unresolved conditions")
        for item in pending:
            print(" - " + item)
        raise SystemExit(1)
    print("Checklist complete. Independent operator deployment review still required.")
