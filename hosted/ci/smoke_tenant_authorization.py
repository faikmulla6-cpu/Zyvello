"""Read-only, two-account authorization smoke for Zyvello Modal staging.

Supply two *different*, short-lived Neon Auth access JWTs through the process
environment (never commit them or put them in command-line arguments):

  ZYVELLO_TEST_TOKEN_A=... ZYVELLO_TEST_TOKEN_B=... \
      python hosted/ci/smoke_tenant_authorization.py

Optionally set ZYVELLO_TEST_WORKSPACE_A/B if an account has multiple workspaces.
This script creates nothing, never prints identity data or credentials, rejects
redirects, and only sends the JWTs to the exact known staging host.
"""

from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID


STAGING_URL = "https://zyvello--zyvello-staging-web.us-east.modal.run"
READ_ENDPOINTS = ("jobs", "contacts", "campaigns", "drafts")
OPTIONAL_ENDPOINTS = ("credentials/groq", "billing")


class SmokeFailure(Exception):
    """A release security precondition or read-only check failed."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def fetch(path: str, token: str) -> tuple[int, dict, dict]:
    """Only contact our pinned staging origin; never follow token-bearing redirects."""
    origin = urlsplit(STAGING_URL)
    if (origin.scheme != "https" or origin.netloc != "zyvello--zyvello-staging-web.us-east.modal.run"
            or not path.startswith("/api/v1/") or "?" in path or "#" in path):
        raise SmokeFailure("Unsafe staging request target")
    request = Request(STAGING_URL + path, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
    })
    try:
        response = build_opener(NoRedirects).open(request, timeout=20)
    except HTTPError as error:
        response = error
    with response:
        code = response.status
        headers = dict(response.headers)
        raw = response.read(131072)
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        body = {}
    return code, headers, body if isinstance(body, dict) else {}


def expect_code(fetcher, label: str, path: str, token: str, expected: set[int]):
    status, headers, body = fetcher(path, token)
    if status not in expected:
        # Don't include response bodies, token strings, or customer data.
        raise SmokeFailure(f"{label}: HTTP {status}; expected {sorted(expected)}")
    if headers.get("Cache-Control", "").lower() != "no-store":
        raise SmokeFailure(f"{label}: missing private API cache policy")
    return status, body


def pick_workspace(rows: list, explicit_id: str | None, account: str) -> str:
    ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise SmokeFailure(f"Account {account}: invalid workspace response")
        try:
            ids.add(str(UUID(row["id"])))
        except (KeyError, ValueError, TypeError) as cause:
            raise SmokeFailure(f"Account {account}: invalid workspace identifier") from cause
    if explicit_id:
        try:
            chosen = str(UUID(explicit_id))
        except (ValueError, TypeError) as cause:
            raise SmokeFailure(f"Account {account}: invalid configured workspace identifier") from cause
        if chosen not in ids:
            raise SmokeFailure(f"Account {account}: selected workspace not in memberships")
        return chosen
    if len(ids) != 1:
        raise SmokeFailure(f"Account {account}: expected one workspace; configure its workspace ID")
    return next(iter(ids))


def audit(fetcher, token_a: str, token_b: str,
          workspace_a: str | None = None, workspace_b: str | None = None) -> tuple[int, int]:
    if not token_a or not token_b or token_a == token_b:
        raise SmokeFailure("Two distinct Neon Auth access tokens are required")

    users = []
    workspaces = []
    membership_sets = []
    entitled_states = []
    for label, token, selected in (("A", token_a, workspace_a), ("B", token_b, workspace_b)):
        _, identity = expect_code(fetcher, f"{label} identity", "/api/v1/me", token, {200})
        if identity.get("status") != "identity_verified" or not identity.get("user_id"):
            raise SmokeFailure(f"Account {label}: identity claims not verified")
        users.append(identity["user_id"])
        _, body = expect_code(fetcher, f"{label} memberships", "/api/v1/workspaces", token, {200})
        rows = body.get("workspaces")
        if not isinstance(rows, list):
            raise SmokeFailure(f"Account {label}: malformed membership list")
        membership_sets.append({str(row.get("id")) for row in rows if isinstance(row, dict)})
        chosen = pick_workspace(rows, selected, label)
        workspaces.append(chosen)
        _, ent = expect_code(fetcher, f"{label} paid entitlement", f"/api/v1/workspaces/{chosen}/entitlement", token, {200})
        if type(ent.get("entitled")) is not bool:
            raise SmokeFailure(f"Account {label}: malformed entitlement state")
        entitled_states.append(ent["entitled"])

    if users[0] == users[1] or workspaces[0] == workspaces[1]:
        raise SmokeFailure("The test requires different users with different workspaces")
    if workspaces[0] in membership_sets[1] or workspaces[1] in membership_sets[0]:
        raise SmokeFailure("Accounts share workspace membership; cannot prove isolated tenants")

    checks = 0
    skipped = 0
    for label, token, own_id, other_id, entitled in (
        ("A", token_a, workspaces[0], workspaces[1], entitled_states[0]),
        ("B", token_b, workspaces[1], workspaces[0], entitled_states[1]),
    ):
        for suffix in ("",) + tuple("/" + item for item in READ_ENDPOINTS):
            own_code = {200} if not suffix or entitled else {402}
            expect_code(fetcher, f"{label} own {suffix or 'workspace'}",
                        f"/api/v1/workspaces/{own_id}{suffix}", token, own_code)
            checks += 1
            expect_code(fetcher, f"{label} cross-tenant {suffix or 'workspace'}",
                        f"/api/v1/workspaces/{other_id}{suffix}", token, {403, 404})
            checks += 1

        for suffix in OPTIONAL_ENDPOINTS:
            own_path = f"/api/v1/workspaces/{own_id}/{suffix}"
            allowed = {200, 503} if suffix == "billing" or entitled else {402}
            status, _ = expect_code(fetcher, f"{label} own {suffix}", own_path, token, allowed)
            if status == 503:
                skipped += 1  # A disabled service cannot prove tenant authorization.
                continue
            expect_code(fetcher, f"{label} cross-tenant {suffix}",
                        f"/api/v1/workspaces/{other_id}/{suffix}", token, {403, 404})
            checks += 2

    return checks, skipped


def main() -> int:
    try:
        checks, skipped = audit(
            fetch,
            os.environ.get("ZYVELLO_TEST_TOKEN_A", ""),
            os.environ.get("ZYVELLO_TEST_TOKEN_B", ""),
            os.environ.get("ZYVELLO_TEST_WORKSPACE_A"),
            os.environ.get("ZYVELLO_TEST_WORKSPACE_B"),
        )
    except SmokeFailure as error:
        print(f"FAIL tenant authorization: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"FAIL tenant authorization: {type(error).__name__}", file=sys.stderr)
        return 1
    print(f"PASS two-account authenticated staging isolation: {checks} read-only checks")
    if skipped:
        print(f"SKIPPED {skipped} optional, disabled-service checks (not counted as passes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
