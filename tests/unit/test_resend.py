import httpx
import pytest

from policy_data.integrations.resend import ResendClient


@pytest.mark.asyncio
async def test_resend_uses_bearer_and_stable_idempotency_header() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "email-1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resend = ResendClient(
            api_key="re_secret", sender="Access <access@example.it>", client=client
        )
        result = await resend.send_otp(
            email="ada@example.it", code="123456", idempotency_key="otp/challenge"
        )
    assert result == "email-1"
    assert requests[0].headers["authorization"] == "Bearer re_secret"
    assert requests[0].headers["idempotency-key"] == "otp/challenge"
    assert requests[0].url == httpx.URL("https://api.resend.com/emails")
