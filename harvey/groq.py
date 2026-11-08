"""Text-only Groq adapter with optional fallback and bounded cooldowns."""
import os
import math
import re
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

from harvey.paths import PROJECT_ROOT

DEFAULT_MODEL = "openai/gpt-oss-120b"
KEY_NAMES = ("GROQ_API_KEY",) + tuple(f"GROQ_API_KEY_{i}" for i in range(2, 11))


def credentials(path: Path | None = None) -> tuple[str, dict[str, str]]:
    values = dict(os.environ)
    values.update(dotenv_values(path or PROJECT_ROOT / ".env", interpolate=False))
    active = str(values.get("GROQ_ACTIVE_KEY") or "GROQ_API_KEY")
    keys = {name: str(values.get(name) or "").strip() for name in KEY_NAMES}
    return active, keys


def validate_key(value: str) -> str:
    value = value.strip()
    if value and not re.fullmatch(r"gsk_[A-Za-z0-9_-]{16,240}", value):
        raise ValueError("Enter a valid Groq API key beginning with gsk_.")
    return value


def fallback_settings(path: Path | None = None) -> tuple[bool, list[str]]:
    values = dict(os.environ)
    values.update(dotenv_values(path or PROJECT_ROOT / ".env", interpolate=False))
    enabled = str(values.get("GROQ_AUTO_FALLBACK") or "true").lower() == "true"
    slots = values.get("GROQ_FALLBACK_KEYS")
    eligible = list(KEY_NAMES) if slots is None else [s for s in slots.split(",") if s in KEY_NAMES]
    return enabled, eligible


class GroqError(Exception):
    def __init__(self, message: str, *, kind="setup", retry_after=60):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


class GroqClient:
    def __init__(self, model="", key=None):
        self.model = model or DEFAULT_MODEL
        self.key = key
        self.until = 0.0
        self.reason = ""
        self._identity = None
        self._cooldowns = {}
        self.last_slot = ""

    def current_key(self):
        if self.key is not None:
            return self.key
        active, keys = credentials()
        return keys.get(active, "")

    def preflight(self):
        key = self.current_key()
        if key != self._identity:
            self._identity, self.until, self.reason = key, 0.0, ""
        if not key:
            return "Add and select a Groq API key in Settings."
        if time.monotonic() < self.until:
            return self.reason
        return ""

    async def run(self, prompt: str, timeout=120):
        reason = self.preflight()
        if reason:
            raise GroqError(reason, kind="paused")
        started = time.monotonic()
        if self.key is not None:
            candidates = [("test", self.key)]
        else:
            active, keys = credentials()
            enabled, eligible = fallback_settings()
            names = [active] + ([name for name in KEY_NAMES if name != active and name in eligible] if enabled else [])
            # Duplicate saved keys never create additional attempts.
            candidates, seen = [], set()
            for name in names:
                key = keys.get(name, "")
                if key and key not in seen:
                    candidates.append((name, key))
                    seen.add(key)
        last_error = None
        for slot, key in candidates:
            if self._cooldowns.get(key, 0) > time.monotonic():
                continue
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                break
            try:
                text, usage, _ = await self._request(prompt, key, timeout=min(30, remaining))
            except GroqError as error:
                last_error = error
                if error.kind not in ("auth", "transient", "usage_limit"):
                    raise
                self._cooldowns[key] = time.monotonic() + error.retry_after
                continue
            self.last_slot = slot
            return text, usage, int((time.monotonic() - started) * 1000)
        self.reason = str(last_error) if last_error else "Groq fallback keys are cooling down. Retry shortly."
        self.until = min((until for until in self._cooldowns.values() if until > time.monotonic()),
                         default=time.monotonic() + 60)
        raise GroqError(self.reason, kind=last_error.kind if last_error else "paused")

    async def _request(self, prompt: str, key: str, timeout=120):
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": "Bearer " + key},
                    json={"model": self.model, "messages": [{"role": "user", "content": prompt}],
                          "max_completion_tokens": 4096, "stream": False},
                )
        except httpx.HTTPError:
            raise GroqError("Groq could not be reached. Check the connection and retry.", kind="transient") from None
        if response.status_code != 200:
            status = response.status_code
            if status == 429:
                try:
                    delay = max(1, float(response.headers.get("retry-after", "60")))
                    if not math.isfinite(delay):
                        delay = 60
                except ValueError:
                    delay = 60
                message, kind = "Groq usage limit reached. Waiting before retrying the selected key.", "usage_limit"
                self.until = time.monotonic() + delay
            elif status in (401, 403):
                message, kind = "Groq rejected this key. Check its permissions or replace it in Settings.", "auth"
                self.until = time.monotonic() + 60
            elif status >= 500:
                message, kind = "Groq is temporarily unavailable. Retry later.", "transient"
            else:
                message, kind = "Groq rejected the request. Check the selected model and its availability.", "setup"
                self.until = time.monotonic() + 60
            self.reason = message
            raise GroqError(message, kind=kind, retry_after=delay if status == 429 else 60)
        try:
            result = response.json()
            choice = result["choices"][0]
            text = choice["message"]["content"]
            if not isinstance(text, str) or not text.strip() or choice.get("finish_reason") != "stop":
                raise ValueError()
            usage = result.get("usage") or {}
            counts = {"input_tokens": int(usage.get("prompt_tokens", 0)),
                      "output_tokens": int(usage.get("completion_tokens", 0))}
        except (KeyError, IndexError, TypeError, ValueError):
            raise GroqError("Groq returned an incomplete or invalid response. Retry later.", kind="transient") from None
        self.until, self.reason = 0.0, ""
        return text, counts, int((time.monotonic() - started) * 1000)


class GroqQuota:
    async def get_utilization(self):
        # API quotas are per organization/model; never invent a plan percentage.
        return None
