from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    data_dir: Path
    public_site_url: str
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    cursor_secret: bytes
    auth_pepper: bytes
    resend_api_key: str
    resend_sender: str

    @classmethod
    def from_environment(cls) -> "Settings":
        environment = os.getenv("POLICY_DATA_ENV", "development")
        settings = cls(
            environment=environment,
            data_dir=Path(os.getenv("POLICY_DATA_DATA_DIR", "data")),
            public_site_url=os.getenv("PUBLIC_SITE_URL", "http://localhost:8000"),
            allowed_hosts=_csv("ALLOWED_HOSTS", "localhost,127.0.0.1"),
            allowed_origins=_csv("ALLOWED_ORIGINS", "http://localhost:8000"),
            cursor_secret=_secret("CURSOR_SECRET", environment),
            auth_pepper=_secret("AUTH_PEPPER", environment),
            resend_api_key=_value("RESEND_API_KEY", "development-only") or "",
            resend_sender=os.getenv(
                "RESEND_SENDER", "Policy Data Italia <access@example.invalid>"
            ),
        )
        if environment == "production" and settings.resend_api_key in {
            "",
            "development-only",
        }:
            raise ValueError("RESEND_API_KEY is required in production")
        return settings


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(
        part.strip() for part in os.getenv(name, default).split(",") if part.strip()
    )


def _secret(name: str, environment: str) -> bytes:
    value = _value(name)
    if value is None and environment != "production":
        value = f"development-only-{name.casefold()}-change-me"
    encoded = value.encode() if value is not None else b""
    if len(encoded) < 32:
        raise ValueError(f"{name} must contain at least 32 bytes")
    return encoded


def _value(name: str, default: str | None = None) -> str | None:
    file_name = os.getenv(f"{name}_FILE")
    if file_name:
        return Path(file_name).read_text(encoding="utf-8").strip()
    return os.getenv(name, default)
