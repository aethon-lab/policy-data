from datetime import UTC, datetime, timedelta

import pytest

from policy_data.auth.repository import AuthRepository
from policy_data.auth.service import AuthService
from policy_data.storage.connections import initialize_control


class CapturingSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    async def send_otp(self, *, email: str, code: str, idempotency_key: str) -> str:
        self.messages.append((email, code, idempotency_key))
        return "email-1"


@pytest.mark.asyncio
async def test_otp_is_single_use_and_enumeration_safe(tmp_path) -> None:
    repository = AuthRepository(initialize_control(tmp_path / "control.sqlite3"))
    sender = CapturingSender()
    service = AuthService(repository, sender, pepper=b"p" * 32)
    now = datetime(2026, 8, 10, tzinfo=UTC)
    request = await service.request_code("ada@example.it", now=now)
    assert (
        request.public_message
        == "If the address can receive mail, a code has been sent."
    )
    email, code, idempotency = sender.messages[0]
    assert email == "ada@example.it"
    assert idempotency == f"otp/{request.challenge_id}"

    verified = service.verify_code(request.challenge_id, code, now=now)
    assert verified is not None
    assert service.verify_code(request.challenge_id, code, now=now) is None
    assert service.validate_session(verified.generated.raw_token, now=now) is not None
    assert service.verify_csrf(
        verified.record.session_id, verified.generated.csrf_token
    )
    assert not service.verify_csrf(verified.record.session_id, "csrf_wrong")


@pytest.mark.asyncio
async def test_expired_and_five_wrong_attempt_challenges_fail(tmp_path) -> None:
    repository = AuthRepository(initialize_control(tmp_path / "control.sqlite3"))
    sender = CapturingSender()
    service = AuthService(repository, sender, pepper=b"p" * 32)
    now = datetime(2026, 8, 10, tzinfo=UTC)
    expired = await service.request_code("expired@example.it", now=now)
    assert (
        service.verify_code(
            expired.challenge_id,
            sender.messages[-1][1],
            now=now + timedelta(minutes=11),
        )
        is None
    )

    limited = await service.request_code("limited@example.it", now=now)
    correct = sender.messages[-1][1]
    for _ in range(5):
        assert service.verify_code(limited.challenge_id, "000000", now=now) is None
    assert service.verify_code(limited.challenge_id, correct, now=now) is None
