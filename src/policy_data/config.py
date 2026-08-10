from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    data_dir: Path
    public_site_url: str
    public_api_url: str
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            environment=os.getenv("POLICY_DATA_ENV", "development"),
            data_dir=Path(os.getenv("POLICY_DATA_DATA_DIR", "data")),
            public_site_url=os.getenv("PUBLIC_SITE_URL", "http://localhost:8000"),
            public_api_url=os.getenv("PUBLIC_API_URL", "http://localhost:8000"),
            allowed_hosts=_csv("ALLOWED_HOSTS", "localhost,127.0.0.1"),
            allowed_origins=_csv("ALLOWED_ORIGINS", "http://localhost:8000"),
        )


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(
        part.strip() for part in os.getenv(name, default).split(",") if part.strip()
    )
