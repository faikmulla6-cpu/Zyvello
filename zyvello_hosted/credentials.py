"""Encryption boundary for workspace-owned provider credentials.

Never store or log a provider key in plaintext, a job payload, or a response.
All keys used here come from Modal's secret manager, not the repository.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class CredentialConfigurationError(RuntimeError):
    """No usable host-side encryption key is available."""


class CredentialDecryptionError(RuntimeError):
    """The credential cannot be opened with the configured key ring."""


class CredentialVault:
    """Versionable Fernet keyring; the first key encrypts, all keys decrypt."""

    def __init__(self, keys: str | None) -> None:
        entries = [item.strip() for item in (keys or "").split(",") if item.strip()]
        if not entries:
            raise CredentialConfigurationError("Credential encryption is not configured")
        try:
            self._cipher = MultiFernet([Fernet(entry.encode("ascii")) for entry in entries])
        except (TypeError, ValueError) as error:
            raise CredentialConfigurationError("Invalid credential encryption configuration") from error

    def seal(self, api_key: str) -> str:
        # Validate without reporting the key or even a provider-derived suffix.
        if not api_key.startswith("gsk_") or len(api_key) < 36 or len(api_key) > 256:
            raise ValueError("A valid Groq API key is required")
        return self._cipher.encrypt(api_key.encode("utf-8")).decode("ascii")

    def open(self, ciphertext: str) -> str:
        try:
            value = self._cipher.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError) as error:
            raise CredentialDecryptionError("Credential cannot be decrypted") from error
        if not value.startswith("gsk_"):
            raise CredentialDecryptionError("Credential format is invalid")
        return value
