from __future__ import annotations

import httpx
from fastapi import FastAPI
from datetime import UTC, datetime

from policy_data.app import create_app
from policy_data.auth.repository import AuthRepository
from policy_data.auth.service import AuthService
from policy_data.config import Settings
from policy_data.integrations.resend import ResendClient
from policy_data.query.service import QueryService
from policy_data.storage.connections import initialize_control


def build_app() -> FastAPI:
    settings = Settings.from_environment()
    release_root = settings.data_dir / "published"
    control_connection = initialize_control(settings.data_dir / "control.sqlite3")
    mail_client = httpx.AsyncClient(timeout=10.0)
    auth_repository = AuthRepository(control_connection)
    auth_repository.purge_expired_access_state(datetime.now(UTC))
    auth = AuthService(
        auth_repository,
        ResendClient(
            api_key=settings.resend_api_key,
            sender=settings.resend_sender,
            client=mail_client,
        ),
        pepper=settings.auth_pepper,
    )
    query = QueryService(release_root, cursor_secret=settings.cursor_secret)

    async def shutdown() -> None:
        await mail_client.aclose()
        control_connection.close()

    return create_app(
        query,
        auth,
        release_root=release_root,
        public_site_url=settings.public_site_url,
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=settings.allowed_origins,
        shutdown=shutdown,
    )


app = build_app()
