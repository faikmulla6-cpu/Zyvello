"""Validate Neon Managed Auth access JWTs for the hosted staging API.

Authentication is *not* authorization: a valid identity must still be checked
against workspace memberships before any tenant-scoped data or job is exposed.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

import jwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientConnectionError, PyJWKClientError


class TokenRejected(Exception):
    """The supplied credential does not prove a verified user identity."""


class IdentityProviderUnavailable(Exception):
    """Cannot currently retrieve the identity provider's signing keys."""


@dataclass(frozen=True)
class VerifiedIdentity:
    user_id: str
    email: str


class NeonTokenVerifier:
    def __init__(self, auth_base_url: str) -> None:
        parsed = urlsplit(auth_base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path.endswith("/auth")
        ):
            raise ValueError("An HTTPS Neon Auth Base URL must be configured")
        self.origin = f"https://{parsed.netloc}"
        self.jwks_client = PyJWKClient(
            auth_base_url.rstrip("/") + "/.well-known/jwks.json",
            cache_jwk_set=True,
            lifespan=300,
            timeout=4,
        )

    def verify(self, token: str) -> VerifiedIdentity:
        # Limit header parsing and remote JWKS lookup work for malicious input.
        if not token or len(token) > 8192 or token.count(".") != 2:
            raise TokenRejected
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "EdDSA" or not isinstance(header.get("kid"), str):
                raise TokenRejected
            key = self.jwks_client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["EdDSA"],
                issuer=self.origin,
                audience=self.origin,
                leeway=10,
                options={"require": ["sub", "iat", "exp", "iss", "aud"]},
            )
            user_id = claims.get("sub")
            email = claims.get("email")
            if (
                not isinstance(user_id, str)
                or not user_id.strip()
                or not isinstance(email, str)
                or "@" not in email
                or claims.get("emailVerified") is not True
            ):
                raise TokenRejected
            return VerifiedIdentity(user_id=user_id, email=email)
        except PyJWKClientConnectionError as cause:
            raise IdentityProviderUnavailable from cause
        except (InvalidTokenError, PyJWKClientError, ValueError, TypeError) as cause:
            raise TokenRejected from cause
