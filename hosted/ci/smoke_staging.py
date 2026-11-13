"""Read-only smoke checks for the private Zyvello staging API.

Usage:
  python hosted/ci/smoke_staging.py
  ZYVELLO_STAGING_URL=https://... python hosted/ci/smoke_staging.py

No authentication, customer data, model calls, or payment requests are sent.
"""
from __future__ import annotations

import json
import os
import re
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = os.environ.get(
    "ZYVELLO_STAGING_URL",
    "https://zyvello--zyvello-staging-web.us-east.modal.run",
).rstrip("/")

EXPECTED_PLANS = {
    "starter": (49_900, 100, 5, 1),
    "growth": (149_900, 400, 20, 3),
    "business": (399_900, 1000, 50, 10),
}
LIMIT_FIELDS = ("stored_contacts", "stored_campaign_drafts", "workspace_members_including_owner")


def get(path: str) -> tuple[int, dict, dict]:
    req = Request(BASE + path, headers={"Accept": "application/json"})
    try:
        response = urlopen(req, timeout=25)
    except HTTPError as error:
        response = error
    with response:
        status = response.status
        headers = dict(response.headers)
        payload = response.read(32768).decode("utf-8")
    try:
        body = json.loads(payload)
    except json.JSONDecodeError:
        body = {}
    return status, headers, body


def get_text(path: str) -> tuple[int, str]:
    """Fetch only public HTML/JS bytes for deployment-mode verification."""
    req = Request(BASE + path, headers={"Accept": "text/html,*/*"})
    try:
        response = urlopen(req, timeout=25)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.read(131072).decode("utf-8", errors="replace")


def check(name: str, condition: bool) -> None:
    print(f'{"PASS" if condition else "FAIL"} {name}')
    if not condition:
        raise AssertionError(name)


def main() -> int:
    try:
        code, _, body = get("/healthz")
        check("health endpoint alive", code == 200 and body.get("status") == "alive")
        code, headers, _ = get("/api/v1/me")
        check("anonymous identity denied", code == 401)
        header_names = {name.lower() for name in headers}
        check("security headers", "x-content-type-options" in header_names)
        code, _, caps = get("/api/v1/capabilities")
        check("capabilities endpoint", code == 200 and caps.get("stage") == "private_beta")
        check(
            "public sending and paid checkout remain disabled",
            caps.get("outbound_sending_enabled") is False
            and caps.get("payments_enabled") is False
            and caps.get("ai_enabled") is False,
        )
        check("Razorpay Test Mode is configured", caps.get("razorpay_test_billing_ready") is True)
        code, _, pricing = get("/api/v1/pricing")
        plans = pricing.get("plans", [])
        plan_limits = {
            plan["code"]: (plan["amount_paise"], *(plan["limits"][field] for field in LIMIT_FIELDS))
            for plan in plans
        }
        check(
            "approved monthly prices and limits with live checkout disabled",
            code == 200 and pricing.get("currency") == "INR"
            and pricing.get("billing_cycle") == "monthly"
            and pricing.get("live_payments_enabled") is False
            and len(plans) == len(EXPECTED_PLANS) and plan_limits == EXPECTED_PLANS
            and all(plan.get("live_checkout_available") is False for plan in plans),
        )
        code, policy_headers, _ = get("/subscription-policy")
        robots = next((value for name, value in policy_headers.items() if name.lower() == "x-robots-tag"), "")
        check("subscription policy route available and excluded from indexing", code == 200 and "noindex" in robots)
        login_code, login_html = get_text("/login")
        # Hosted staging is built with Vite --base / and deployed from
        # frontend/dist. A /static/landing bundle indicates a stale preview.
        entry = re.search(r'src="(/assets/index-[^"]+\.js)"', login_html)
        bundle_code, bundle_js = get_text(entry.group(1)) if entry else (0, "")
        check(
            "current public hosted login bundle active (not stale preview)",
            login_code == 200 and entry is not None
            and bundle_code == 200 and "HostedAuthPage" in bundle_js,
        )
        code, _, body = get("/readyz")
        check("public readiness intentionally gated", code == 503 and body.get("status") == "not_ready")
    except Exception as error:
        print(f"SMOKE FAILED: {type(error).__name__}", file=sys.stderr)
        return 1
    print("All read-only staging smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
