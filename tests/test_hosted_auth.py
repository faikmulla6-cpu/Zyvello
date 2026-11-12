"""No internet or real user accounts: exercise JWT validation with test keys."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from zyvello_hosted.auth import IdentityProviderUnavailable, NeonTokenVerifier, TokenRejected
from zyvello_hosted.health import create_app


AUTH_BASE_URL = "https://identity.example.test/neondb/auth"
ORIGIN = "https://identity.example.test"


class HostedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.private_key = Ed25519PrivateKey.generate()
        self.verifier = NeonTokenVerifier(AUTH_BASE_URL)
        self.verifier.jwks_client.get_signing_key_from_jwt = lambda _: SimpleNamespace(key=self.private_key.public_key())
        self.now = int(time.time())

    def token(self, **overrides):
        claims = {
            "sub": "test-user-a",
            "email": "user@example.test",
            "emailVerified": True,
            "iat": self.now - 5,
            "exp": self.now + 300,
            "iss": ORIGIN,
            "aud": ORIGIN,
        }
        claims.update(overrides)
        return jwt.encode(claims, self.private_key, algorithm="EdDSA", headers={"kid": "staging-test"})

    def test_verified_identity(self):
        identity = self.verifier.verify(self.token())
        self.assertEqual(identity.user_id, "test-user-a")
        self.assertEqual(identity.email, "user@example.test")
        self.assertEqual(identity.issued_at, self.now - 5)

    def test_fail_closed_for_unverified_wrong_origin_and_expired(self):
        cases = [
            self.token(emailVerified=False),
            self.token(iat=True),
            self.token(iat=0),
            self.token(aud="https://attacker.example.test"),
            self.token(iss="https://attacker.example.test"),
            self.token(exp=self.now - 120),
            "bad-token",
        ]
        for case in cases:
            with self.subTest(case=case[:10]), self.assertRaises(TokenRejected):
                self.verifier.verify(case)

    def test_missing_token_is_401_not_anonymous_access(self):
        client = TestClient(create_app(auth_base_url=AUTH_BASE_URL))
        self.assertEqual(client.get("/api/v1/me").status_code, 401)
        self.assertEqual(client.get("/api/v1/me", headers={"Authorization": "Basic fake"}).status_code, 401)

    def test_valid_identity_endpoint_never_grants_membership(self):
        client = TestClient(create_app(auth_base_url=AUTH_BASE_URL))
        with patch("zyvello_hosted.auth.PyJWKClient.get_signing_key_from_jwt", return_value=SimpleNamespace(key=self.private_key.public_key())):
            response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {self.token()}"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"user_id": "test-user-a", "email": "user@example.test", "status": "identity_verified"})
        self.assertNotIn("workspace_id", response.json())

    def test_bad_origin_rejected_during_configuration(self):
        for url in ("http://identity.example.test/neondb/auth", "https://x.test/neondb/auth?key=a", "https://x.test/other"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                NeonTokenVerifier(url)


if __name__ == "__main__":
    unittest.main()
