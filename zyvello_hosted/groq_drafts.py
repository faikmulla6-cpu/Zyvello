"""Server-side Groq draft generation only (never email delivery).

The provider key is decrypted only by the trusted worker. Provider text is
treated as untrusted draft content and must be reviewed before any future send.
"""

from __future__ import annotations

import httpx


GROQ_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"


class DraftProviderError(RuntimeError):
    """A provider error safe for API/job metadata without response contents."""


class GroqDraftClient:
    def __init__(self, *, model: str = DEFAULT_MODEL, transport: httpx.BaseTransport | None = None):
        # Avoid arbitrary model selection via customer supplied values.
        if model not in {DEFAULT_MODEL}:
            raise ValueError("Unapproved Groq model")
        self.model = model
        self._transport = transport

    def generate(self, *, api_key: str, contact_name: str, company: str,
                 campaign_goal: str) -> str:
        if not api_key.startswith("gsk_"):
            raise DraftProviderError("Provider credential is invalid")
        # Purposefully restrict the data that can leave a tenant. No email
        # address, customer lists, database IDs, or secrets go in the prompt.
        payload = {
            "model": self.model,
            "temperature": 0.4,
            "max_completion_tokens": 700,
            "reasoning_effort": "low",
            "include_reasoning": False,
            "messages": [
                {"role": "system", "content": (
                    "Draft a short, truthful, respectful business outreach email for human review. "
                    "Do not invent facts, credentials, personalization, case studies, or guarantees. "
                    "Do not include tracking links or assume consent. Return plain text only. "
                    "This is a DRAFT; do not send anything.")},
                {"role": "user", "content": (
                    f"Recipient name: {contact_name[:160]}\n"
                    f"Recipient company: {company[:160]}\n"
                    f"Outreach goal: {campaign_goal[:1000]}\n"
                    "Write a subject line and a concise body.")},
            ],
        }
        try:
            with httpx.Client(transport=self._transport, timeout=20.0, follow_redirects=False) as client:
                response = client.post(
                    GROQ_COMPLETIONS_URL,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
            if response.status_code == 429:
                raise DraftProviderError("Provider rate limit reached")
            if response.status_code in (401, 403):
                raise DraftProviderError("Provider credential was rejected")
            if response.status_code != 200:
                raise DraftProviderError("Provider temporarily unavailable")
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not 1 <= len(content.strip()) <= 5000:
                raise DraftProviderError("Provider response was invalid")
            return content.strip()
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise DraftProviderError("Provider connection failed") from error
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise DraftProviderError("Provider response was invalid") from error
