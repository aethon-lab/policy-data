from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def generate_challenge_id() -> str:
    return f"otp_{secrets.token_urlsafe(24)}"


def secret_digest(pepper: bytes, namespace: str, value: str) -> str:
    if len(pepper) < 32:
        raise ValueError("secret pepper must contain at least 32 bytes")
    return hmac.new(
        pepper, f"{namespace}\0{value}".encode(), hashlib.sha256
    ).hexdigest()


def code_digest(pepper: bytes, challenge_id: str, code: str) -> str:
    return secret_digest(pepper, f"otp:{challenge_id}", code)


def normalize_email(value: str) -> str:
    email = value.strip()
    if (
        len(email) > 254
        or email.count("@") != 1
        or any(character.isspace() or ord(character) < 32 for character in email)
    ):
        raise ValueError("invalid email address")
    local, domain = email.rsplit("@", 1)
    if (
        not local
        or not domain
        or "." not in domain
        or domain.startswith(".")
        or domain.endswith(".")
    ):
        raise ValueError("invalid email address")
    return f"{local}@{domain.casefold()}"
