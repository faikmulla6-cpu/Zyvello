"""Fail-closed real-token release gate for Neon password-reset revocation.

Use an existing, authorized *test* account. Do not paste JWTs, passwords or OTPs
into chat, source files, or shell command history.

1. Keep a PRE-RESET access JWT in ZYVELLO_TEST_OLD_TOKEN; run --phase before.
2. Reset that test account's password using Neon Auth's normal email OTP UI.
3. Obtain a NEW access JWT from that same account, in ZYVELLO_TEST_NEW_TOKEN.
4. Run --phase after *before the old JWT's natural expiry*.

This checks API revocation, not just browser cookie invalidation. Because Neon
access JWTs are stateless, resetting the provider session may NOT invalidate an
already-issued JWT. In that case the gate MUST fail and the release stays held.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

API_ORIGIN = "https://zyvello--zyvello-staging-web.us-east.modal.run"
PATH = "/api/v1/me"


class RevocationFailure(Exception):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def jwt_payload(token: str) -> dict:
    if not token or len(token) > 8192 or token.count(".") != 2:
        raise RevocationFailure("A valid-looking access JWT is required")
    try:
        section = token.split(".")[1]
        blob = base64.urlsafe_b64decode(section + "=" * (-len(section) % 4))
        claims = json.loads(blob)
    except (ValueError, UnicodeError) as exc:
        raise RevocationFailure("Access JWT claims could not be inspected") from exc
    if not isinstance(claims, dict) or not isinstance(claims.get("exp"), int):
        raise RevocationFailure("Access JWT lacks a numeric expiry")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise RevocationFailure("Access JWT lacks a subject")
    return claims  # Untrusted until the staging API verifies the signature.


def fetch_identity(token: str) -> tuple[int, dict]:
    req = Request(API_ORIGIN + PATH, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
    })
    try:
        response = build_opener(NoRedirects).open(req, timeout=20)
    except HTTPError as error:
        response = error
    with response:
        status = response.status
        cache = response.headers.get("Cache-Control", "")
        raw = response.read(32768)
    if cache.lower() != "no-store":
        raise RevocationFailure("The API response lacks Cache-Control: no-store")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        body = {}
    return status, body if isinstance(body, dict) else {}


def audit(phase: str, old: str, new: str, fetcher=fetch_identity, now: int | None = None) -> str:
    current = int(time.time()) if now is None else now
    old_claims = jwt_payload(old)
    if old_claims["exp"] <= current:
        raise RevocationFailure("INCONCLUSIVE: old access JWT expired naturally; repeat with fresh tokens")
    if phase == "before":
        code, identity = fetcher(old)
        if code != 200 or identity.get("user_id") != old_claims["sub"]:
            raise RevocationFailure("PRE-RESET failed: original JWT not verified by staging")
        return "PASS pre-reset: original test JWT is valid and still unexpired"

    if not new or new == old:
        raise RevocationFailure("A distinct post-reset access JWT is required")
    new_claims = jwt_payload(new)
    if new_claims["sub"] != old_claims["sub"]:
        raise RevocationFailure("Old/new JWTs are for different accounts")
    if new_claims["exp"] <= current:
        raise RevocationFailure("Post-reset JWT has expired")
    new_code, new_identity = fetcher(new)
    if new_code != 200 or new_identity.get("user_id") != old_claims["sub"]:
        raise RevocationFailure("Post-reset JWT is not verified for the same account")
    old_code, _ = fetcher(old)
    if old_code != 401:
        raise RevocationFailure(
            "SESSION REVOCATION NOT PROVEN: unexpired pre-reset JWT still "
            f"received HTTP {old_code}; do not release"
        )
    return "PASS post-reset: new JWT valid; unexpired old JWT rejected by staging"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check live post-reset JWT invalidation without printing secrets")
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    args = parser.parse_args()
    try:
        message = audit(args.phase,
                        os.getenv("ZYVELLO_TEST_OLD_TOKEN", ""),
                        os.getenv("ZYVELLO_TEST_NEW_TOKEN", ""))
    except RevocationFailure as err:
        print("FAIL " + str(err), file=sys.stderr)
        return 1
    except Exception as err:
        print(f"FAIL auth verification request: {type(err).__name__}", file=sys.stderr)
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
