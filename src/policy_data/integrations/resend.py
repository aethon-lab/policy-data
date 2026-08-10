from __future__ import annotations

import html

import httpx


class ResendClient:
    def __init__(
        self,
        *,
        api_key: str,
        sender: str,
        client: httpx.AsyncClient,
        endpoint: str = "https://api.resend.com/emails",
    ) -> None:
        self.api_key = api_key
        self.sender = sender
        self.client = client
        self.endpoint = endpoint

    async def send_otp(self, *, email: str, code: str, idempotency_key: str) -> str:
        if not idempotency_key or len(idempotency_key) > 256:
            raise ValueError("Resend idempotency key must contain 1-256 characters")
        safe_code = html.escape(code)
        response = await self.client.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Idempotency-Key": idempotency_key,
            },
            json={
                "from": self.sender,
                "to": [email],
                "subject": "Il tuo codice di accesso",
                "text": f"Il tuo codice di accesso è {code}. Scade tra 10 minuti.",
                "html": f"<p>Il tuo codice di accesso è <strong>{safe_code}</strong>.</p><p>Scade tra 10 minuti.</p>",
            },
        )
        response.raise_for_status()
        value = response.json().get("id")
        if not isinstance(value, str) or not value:
            raise ValueError("Resend response is missing its email ID")
        return value
