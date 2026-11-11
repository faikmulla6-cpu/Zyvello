"""Regression checks for the read-only, real-token staging isolation probe."""

import unittest
from unittest.mock import patch

from hosted.ci import smoke_tenant_authorization as smoke


A = "00000000-0000-4000-8000-0000000000a1"
B = "00000000-0000-4000-8000-0000000000b2"


def fake_fetch(path, token, *, cross_leak=False, disabled_billing=False,
               missing_cache=False, shared_membership=False):
    own, other = (A, B) if token == "test-token-a" else (B, A)
    headers = {} if missing_cache else {"Cache-Control": "no-store"}
    if path == "/api/v1/me":
        return 200, headers, {"status": "identity_verified", "user_id": token}
    if path == "/api/v1/workspaces":
        rows = [{"id": own}]
        if shared_membership:
            rows.append({"id": other})
        return 200, headers, {"workspaces": rows}
    if path.startswith("/api/v1/workspaces/"):
        requested = path.split("/")[4]
        if requested != own and not cross_leak:
            return 404, headers, {"detail": "Workspace not found"}
        if disabled_billing and path.endswith("/billing"):
            return 503, headers, {"detail": "Test billing not configured"}
        return 200, headers, {}
    raise AssertionError("Unexpected test request")


class TenantAuthorizationSmokeTests(unittest.TestCase):
    def test_valid_distinct_accounts_are_read_only_and_isolated(self):
        paths = []

        def record(path, token):
            paths.append((path, token))
            return fake_fetch(path, token)

        checks, skipped = smoke.audit(record, "test-token-a", "test-token-b")
        self.assertEqual((checks, skipped), (28, 0))
        self.assertEqual(len(paths), 32)  # identity + membership + own/cross requests
        self.assertTrue(all(path.startswith("/api/v1/") for path, _ in paths))

    def test_disabled_billing_is_explicitly_skipped_not_passed(self):
        checks, skipped = smoke.audit(
            lambda path, token: fake_fetch(path, token, disabled_billing=True),
            "test-token-a", "test-token-b",
        )
        self.assertEqual((checks, skipped), (24, 2))

    def test_cross_tenant_data_exposure_fails_closed(self):
        with self.assertRaisesRegex(smoke.SmokeFailure, "cross-tenant"):
            smoke.audit(lambda path, token: fake_fetch(path, token, cross_leak=True),
                        "test-token-a", "test-token-b")

    def test_shared_membership_cannot_be_misrepresented_as_isolation(self):
        with self.assertRaisesRegex(smoke.SmokeFailure, "share workspace membership"):
            smoke.audit(lambda path, token: fake_fetch(path, token, shared_membership=True),
                        "test-token-a", "test-token-b", A, B)

    def test_tokens_must_be_distinct_and_present(self):
        for tokens in (("", "test-token-b"), ("test-token-a", "test-token-a")):
            with self.subTest(tokens=bool(tokens[0])):
                with self.assertRaisesRegex(smoke.SmokeFailure, "distinct"):
                    smoke.audit(lambda path, token: self.fail("No request should occur"), *tokens)

    def test_multiple_workspaces_require_explicit_selection(self):
        def fetch_multiple(path, token):
            if path == "/api/v1/workspaces" and token == "test-token-a":
                return 200, {"Cache-Control": "no-store"}, {"workspaces": [{"id": A}, {"id": B}]}
            return fake_fetch(path, token)

        with self.assertRaisesRegex(smoke.SmokeFailure, "configure its workspace ID"):
            smoke.audit(fetch_multiple, "test-token-a", "test-token-b")

    def test_cache_policy_is_mandatory(self):
        with self.assertRaisesRegex(smoke.SmokeFailure, "cache policy"):
            smoke.audit(lambda path, token: fake_fetch(path, token, missing_cache=True),
                        "test-token-a", "test-token-b")

    def test_staging_origin_is_pinned_before_sending_jwts(self):
        with patch.object(smoke, "STAGING_URL", "https://attacker.example"):
            with patch.object(smoke, "build_opener") as opener:
                with self.assertRaisesRegex(smoke.SmokeFailure, "Unsafe staging"):
                    smoke.fetch("/api/v1/me", "test-token-a")
                opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
