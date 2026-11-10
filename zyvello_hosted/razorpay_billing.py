"""Fail-closed Razorpay Subscriptions adapter for Zyvello's *test* environment.

Only the Razorpay key ID may reach Checkout. The key secret, webhook secret,
prices, plan IDs and account mode are server-side configuration. This module
deliberately refuses live keys until the launch review is complete.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
import base64


PLAN_CODE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
PLAN_ID_RE = re.compile(r"^plan_[A-Za-z0-9]{8,40}$")
SUBSCRIPTION_ID_RE = re.compile(r"^sub_[A-Za-z0-9]{8,40}$")
PAYMENT_ID_RE = re.compile(r"^pay_[A-Za-z0-9]{8,40}$")
WEBHOOK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,128}$")
MAX_WEBHOOK_BODY_BYTES = 1024 * 1024


class _NeverRedirect(HTTPRedirectHandler):
    """Never forward Basic credentials to a redirected URL."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class BillingUnavailable(Exception):
    """Payment setup or the external gateway is unavailable."""


class BillingRejected(Exception):
    """The gateway payload or signature did not pass validation."""


@dataclass(frozen=True)
class Plan:
    code: str
    name: str
    amount_paise: int
    razorpay_plan_id: str
    period: str
    interval: int
    total_count: int

    def public(self) -> dict[str, Any]:
        return {
            "code": self.code, "name": self.name, "amount_paise": self.amount_paise,
            "currency": "INR", "period": self.period, "interval": self.interval,
        }


@dataclass(frozen=True)
class BillingSettings:
    mode: str
    key_id: str
    key_secret: str
    webhook_secret: str
    plans: dict[str, Plan]

    @property
    def enabled(self) -> bool:
        return bool(self.mode == "test" and self.key_id and self.key_secret
                    and self.webhook_secret and self.plans)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> BillingSettings:
        values = os.environ if env is None else env
        mode = values.get("ZYVELLO_RAZORPAY_MODE", "disabled").lower().strip()
        if mode == "disabled":
            return cls("disabled", "", "", "", {})
        if mode != "test":
            # Live-mode charging requires a separate, reviewed release.
            return cls("disabled", "", "", "", {})
        key_id = values.get("ZYVELLO_RAZORPAY_KEY_ID", "").strip()
        key_secret = values.get("ZYVELLO_RAZORPAY_KEY_SECRET", "")
        webhook_secret = values.get("ZYVELLO_RAZORPAY_WEBHOOK_SECRET", "")
        if not key_id.startswith("rzp_test_") or not key_secret or not webhook_secret:
            return cls("disabled", "", "", "", {})
        raw = values.get("ZYVELLO_RAZORPAY_PLANS_JSON", "")
        try:
            mapping = json.loads(raw)
            if not isinstance(mapping, dict) or len(mapping) > 12:
                raise ValueError("Invalid plans")
            plans: dict[str, Plan] = {}
            for code, data in mapping.items():
                if not isinstance(code, str) or not PLAN_CODE_RE.fullmatch(code) or not isinstance(data, dict):
                    raise ValueError("Invalid plan code")
                amount = data["amount_paise"]
                interval = data.get("interval", 1)
                total_count = data.get("total_count", 12)
                period = data.get("period", "monthly")
                razorpay_id = data["razorpay_plan_id"]
                name = data["name"]
                if (type(amount) is not int or not 100 <= amount <= 10_000_000
                    or type(interval) is not int or not 1 <= interval <= 12
                    or type(total_count) is not int or not 1 <= total_count <= 120
                    or period not in ("monthly", "yearly")
                    or not isinstance(razorpay_id, str) or not PLAN_ID_RE.fullmatch(razorpay_id)
                    or not isinstance(name, str) or not 1 <= len(name.strip()) <= 80):
                    raise ValueError("Invalid plan configuration")
                plans[code] = Plan(code, name.strip(), amount, razorpay_id, period, interval, total_count)
            return cls("test", key_id, key_secret, webhook_secret, plans) if plans else cls("disabled", "", "", "", {})
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            # No public endpoint should accidentally expose malformed config.
            return cls("disabled", "", "", "", {})


def _require_hex_signature(signature: str) -> bool:
    return len(signature) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in signature)


def verify_checkout_signature(*, payment_id: str, stored_subscription_id: str,
                              signature: str, key_secret: str) -> None:
    """Use the *server-stored* subscription ID, never the browser's claimed ID."""
    if (not PAYMENT_ID_RE.fullmatch(payment_id) or not SUBSCRIPTION_ID_RE.fullmatch(stored_subscription_id)
        or not _require_hex_signature(signature) or not key_secret):
        raise BillingRejected("Invalid checkout signature")
    digest = hmac.new(key_secret.encode(), f"{payment_id}|{stored_subscription_id}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, signature.lower()):
        raise BillingRejected("Invalid checkout signature")


def verify_webhook_signature(*, raw_body: bytes, signature: str,
                             webhook_secret: str) -> None:
    if (not raw_body or len(raw_body) > MAX_WEBHOOK_BODY_BYTES
        or not _require_hex_signature(signature) or not webhook_secret):
        raise BillingRejected("Invalid webhook signature")
    digest = hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, signature.lower()):
        raise BillingRejected("Invalid webhook signature")


def parse_verified_webhook(raw_body: bytes, event_id: str) -> dict[str, Any]:
    """Keep only non-sensitive fields needed for safe reconciliation."""
    if not WEBHOOK_ID_RE.fullmatch(event_id):
        raise BillingRejected("Invalid event ID")
    try:
        data = json.loads(raw_body)
        event_type = data["event"]
        event_time = data["created_at"]
        payload = data["payload"]["subscription"]["entity"]
        subscription_id = payload["id"]
        plan_id = payload["plan_id"]
    except (ValueError, TypeError, KeyError):
        raise BillingRejected("Invalid subscription event") from None
    if (not isinstance(event_type, str) or not event_type.startswith("subscription.")
        or type(event_time) is not int or event_time < 1
        or not isinstance(subscription_id, str) or not SUBSCRIPTION_ID_RE.fullmatch(subscription_id)
        or not isinstance(plan_id, str) or not PLAN_ID_RE.fullmatch(plan_id)):
        raise BillingRejected("Invalid subscription event")
    current_end = payload.get("current_end")
    if current_end is not None and (type(current_end) is not int or current_end < 1):
        raise BillingRejected("Invalid subscription period")
    paid_count = payload.get("paid_count", 0)
    if type(paid_count) is not int or not 0 <= paid_count <= 1000:
        raise BillingRejected("Invalid subscription count")
    payment = data.get("payload", {}).get("payment", {})
    payment = payment.get("entity", {}) if isinstance(payment, dict) else {}
    if not isinstance(payment, dict):
        payment = {}
    payment_id = payment.get("id")
    captured = (event_type == "subscription.charged" and payment.get("status") == "captured"
                and isinstance(payment_id, str) and PAYMENT_ID_RE.fullmatch(payment_id) is not None
                and payment.get("currency") == "INR" and type(payment.get("amount")) is int
                and payment["amount"] > 0 and paid_count > 0 and current_end is not None)
    return {
        "event_id": event_id,
        "event_type": event_type,
        "event_time": event_time,
        "provider_subscription_id": subscription_id,
        "provider_plan_id": plan_id,
        "paid_count": paid_count,
        "period_end": current_end,
        "captured": bool(captured),
        "payment_id": payment_id if captured else None,
        "amount_paise": payment["amount"] if captured else None,
        "body_sha256": hashlib.sha256(raw_body).hexdigest(),
    }


class RazorpayGateway:
    """Minimal fixed-origin gateway with no client-controlled URL or amount."""

    def __init__(self, settings: BillingSettings):
        if not settings.enabled:
            raise BillingUnavailable("Test billing is not configured")
        self.settings = settings

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        credentials = base64.b64encode(f"{self.settings.key_id}:{self.settings.key_secret}".encode()).decode()
        request = Request(
            "https://api.razorpay.com/v1/" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Basic " + credentials, "Content-Type": "application/json"},
            method=method,
        )
        try:
            with build_opener(_NeverRedirect()).open(request, timeout=8) as response:
                result = response.read(128 * 1024 + 1)
            if len(result) > 128 * 1024:
                raise BillingUnavailable("Gateway response too large")
            value = json.loads(result)
            if not isinstance(value, dict):
                raise ValueError("Invalid gateway response")
            return value
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
            raise BillingUnavailable("Payment gateway temporarily unavailable") from error

    def validate_plan(self, plan: Plan) -> None:
        remote = self._request("GET", "plans/" + plan.razorpay_plan_id)
        item = remote.get("item")
        if (remote.get("id") != plan.razorpay_plan_id
            or remote.get("period") != plan.period or remote.get("interval") != plan.interval
            or not isinstance(item, dict) or item.get("currency") != "INR"
            or item.get("amount") != plan.amount_paise):
            raise BillingUnavailable("Configured Razorpay plan does not match its published price")

    def create_subscription(self, *, plan: Plan, local_intent_id: str) -> str:
        self.validate_plan(plan)
        response = self._request("POST", "subscriptions", {
            "plan_id": plan.razorpay_plan_id,
            "total_count": plan.total_count,
            "quantity": 1,
            "customer_notify": False,
            "notes": {"zyvello_intent_id": local_intent_id},
        })
        subscription_id = response.get("id")
        if (not isinstance(subscription_id, str) or not SUBSCRIPTION_ID_RE.fullmatch(subscription_id)
            or response.get("plan_id") != plan.razorpay_plan_id
            or response.get("status") != "created"):
            raise BillingUnavailable("Unexpected Razorpay subscription response")
        return subscription_id
