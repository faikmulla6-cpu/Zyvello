"""Deterministic contract tests; real provider resets require live test tokens."""

import base64
import json
import unittest

from hosted.ci.smoke_session_revocation import RevocationFailure, audit


NOW = 1_800_000_000
USER = "verified-test-subject"


def token(exp=NOW + 500, sub=USER, variant="old"):
    header = base64.urlsafe_b64encode(b'{"alg":"EdDSA","kid":"test"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({
        "sub": sub, "exp": exp, "jti": variant
    }).encode()).decode().rstrip("=")
    return header + "." + payload + ".signature"


class SessionRevocationGateTests(unittest.TestCase):
    def test_before_reset_accepts_only_verified_old_token(self):
        old = token()
        def verified(value):
            return (200, {"user_id": USER}) if value == old else (401, {})
        self.assertIn("PASS pre-reset", audit("before", old, "", verified, NOW))

    def test_after_reset_requires_old_unexpired_token_to_be_rejected(self):
        old, new = token(variant="old"), token(variant="new")
        def revoked(value):
            return (401, {}) if value == old else (200, {"user_id": USER})
        self.assertIn("PASS post-reset", audit("after", old, new, revoked, NOW))

    def test_after_reset_fails_if_stale_jwt_still_authorized(self):
        old, new = token(variant="old"), token(variant="new")
        with self.assertRaisesRegex(RevocationFailure, "SESSION REVOCATION NOT PROVEN"):
            audit("after", old, new, lambda _: (200, {"user_id": USER}), NOW)

    def test_expired_old_jwt_is_inconclusive_not_a_pass(self):
        old, new = token(exp=NOW - 2), token(variant="new")
        with self.assertRaisesRegex(RevocationFailure, "INCONCLUSIVE"):
            audit("after", old, new, lambda _: (401, {}), NOW)

    def test_same_or_different_account_tokens_are_rejected(self):
        old = token(variant="old")
        with self.assertRaisesRegex(RevocationFailure, "distinct"):
            audit("after", old, old, lambda _: (200, {"user_id": USER}), NOW)
        with self.assertRaisesRegex(RevocationFailure, "different accounts"):
            audit("after", old, token(sub="someone-else", variant="new"),
                  lambda _: (200, {"user_id": USER}), NOW)

    def test_new_token_must_be_accepted_for_same_identity(self):
        old, new = token(variant="old"), token(variant="new")
        with self.assertRaisesRegex(RevocationFailure, "Post-reset JWT is not verified"):
            audit("after", old, new, lambda _: (401, {}), NOW)


if __name__ == "__main__":
    unittest.main()
