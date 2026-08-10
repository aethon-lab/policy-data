from __future__ import annotations

import secrets
from dataclasses import dataclass

from policy_data.auth.codes import secret_digest

KEY_PREFIX = "pd_live_"


@dataclass(frozen=True, slots=True)
class GeneratedApiKey:
    raw: str
    lookup_prefix: str
    digest: str


def generate_api_key(pepper: bytes) -> GeneratedApiKey:
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    return GeneratedApiKey(raw, raw[:18], secret_digest(pepper, "api_key", raw))


def api_key_lookup_prefix(raw: str) -> str:
    if not raw.startswith(KEY_PREFIX) or len(raw) < 24:
        raise ValueError("invalid API key format")
    return raw[:18]


def api_key_digest(pepper: bytes, raw: str) -> str:
    return secret_digest(pepper, "api_key", raw)
