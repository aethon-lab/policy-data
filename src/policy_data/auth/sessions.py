from __future__ import annotations

import secrets
from dataclasses import dataclass

from policy_data.auth.codes import secret_digest


@dataclass(frozen=True, slots=True)
class GeneratedSession:
    session_id: str
    raw_token: str
    token_digest: str
    csrf_token: str
    csrf_digest: str


def generate_session(pepper: bytes) -> GeneratedSession:
    session_id = f"ses_{secrets.token_urlsafe(24)}"
    raw = f"ps_{secrets.token_urlsafe(32)}"
    csrf = f"csrf_{secrets.token_urlsafe(24)}"
    return GeneratedSession(
        session_id,
        raw,
        secret_digest(pepper, "session", raw),
        csrf,
        csrf_digest(pepper, session_id, csrf),
    )


def session_digest(pepper: bytes, raw: str) -> str:
    return secret_digest(pepper, "session", raw)


def csrf_digest(pepper: bytes, session_id: str, csrf_token: str) -> str:
    return secret_digest(pepper, f"csrf:{session_id}", csrf_token)
