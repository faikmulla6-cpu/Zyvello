"""Read-only two-account staging smoke must respect subscription paywalls."""

import unittest

from hosted.ci.smoke_tenant_authorization import SmokeFailure, audit


A = "00000000-0000-4000-8000-0000000000a1"
B = "00000000-0000-4000-8000-0000000000b2"


def make_fetch(*, grant_unpaid=False):
    users = {"token-a": ("test-user-a", A, False), "token-b": ("test-user-b", B, True)}

    def fetch(path, token):
        user, own_id, entitled = users[token]
        headers = {"Cache-Control": "no-store"}
        if path == "/api/v1/me":
            return 200, headers, {"status": "identity_verified", "user_id": user}
        if path == "/api/v1/workspaces":
            return 200, headers, {"workspaces": [{"id": own_id}]}
        pieces = path.split("/")
        if len(pieces) < 5:
            return 404, headers, {}
        workspace = pieces[4]
        suffix = "/".join(pieces[5:])
        if workspace != own_id:
            return 404, headers, {}
        if not suffix:
            return 200, headers, {}
        if suffix == "entitlement":
            return 200, headers, {"entitled": entitled}
        if suffix.startswith("billing"):
            return 503, headers, {}
        if suffix in ("jobs", "contacts", "campaigns", "drafts", "credentials/groq"):
            return (200 if entitled or grant_unpaid else 402), headers, {}
        return 404, headers, {}
    return fetch


class PublicSignupIsolationSmokeTests(unittest.TestCase):
    def test_unpaid_is_denied_and_paid_can_read_own_workspace(self):
        checks, skipped = audit(make_fetch(), "token-a", "token-b")
        self.assertGreaterEqual(checks, 20)
        self.assertEqual(skipped, 2)

    def test_unpaid_feature_access_fails_release_gate(self):
        with self.assertRaises(SmokeFailure):
            audit(make_fetch(grant_unpaid=True), "token-a", "token-b")


if __name__ == "__main__":
    unittest.main()
