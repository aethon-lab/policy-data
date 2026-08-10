import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from policy_data.auth.repository import AuthRepository
from policy_data.auth.service import (
    SESSION_ABSOLUTE_TTL,
    SESSION_IDLE_TTL,
    AuthService,
)
from policy_data.storage.connections import initialize_control


class CapturingSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    async def send_otp(self, *, email: str, code: str, idempotency_key: str) -> str:
        self.messages.append((email, code, idempotency_key))
        return "email-1"


@pytest.mark.asyncio
async def test_otp_is_single_use_and_enumeration_safe(tmp_path: Path) -> None:
    repository = AuthRepository(initialize_control(tmp_path / "control.sqlite3"))
    sender = CapturingSender()
    service = AuthService(repository, sender, pepper=b"p" * 32)
    now = datetime(2026, 8, 10, tzinfo=UTC)
    request = await service.request_code(
        "ada@example.it", source_ip="192.0.2.1", now=now
    )
    assert (
        request.public_message
        == "If the address can receive mail, a code has been sent."
    )
    email, code, idempotency = sender.messages[0]
    assert email == "ada@example.it"
    assert idempotency == f"otp/{request.challenge_id}"

    verified = service.verify_code(
        request.challenge_id, code, source_ip="192.0.2.1", now=now
    )
    assert verified is not None
    assert (
        service.verify_code(request.challenge_id, code, source_ip="192.0.2.1", now=now)
        is None
    )
    assert service.validate_session(verified.generated.raw_token, now=now) is not None
    assert service.verify_csrf(
        verified.record.session_id, verified.generated.csrf_token
    )
    assert not service.verify_csrf(verified.record.session_id, "csrf_wrong")


@pytest.mark.asyncio
async def test_expired_and_five_wrong_attempt_challenges_fail(
    tmp_path: Path,
) -> None:
    repository = AuthRepository(initialize_control(tmp_path / "control.sqlite3"))
    sender = CapturingSender()
    service = AuthService(repository, sender, pepper=b"p" * 32)
    now = datetime(2026, 8, 10, tzinfo=UTC)
    expired = await service.request_code(
        "expired@example.it", source_ip="192.0.2.2", now=now
    )
    assert (
        service.verify_code(
            expired.challenge_id,
            sender.messages[-1][1],
            source_ip="192.0.2.2",
            now=now + timedelta(minutes=11),
        )
        is None
    )

    limited = await service.request_code(
        "limited@example.it", source_ip="192.0.2.3", now=now
    )
    correct = sender.messages[-1][1]
    for _ in range(5):
        assert (
            service.verify_code(
                limited.challenge_id,
                "000000",
                source_ip="192.0.2.3",
                now=now,
            )
            is None
        )
    assert (
        service.verify_code(
            limited.challenge_id,
            correct,
            source_ip="192.0.2.3",
            now=now,
        )
        is None
    )


@pytest.mark.asyncio
async def test_new_code_replaces_the_only_active_email_challenge(
    tmp_path: Path,
) -> None:
    repository = AuthRepository(initialize_control(tmp_path / "control.sqlite3"))
    sender = CapturingSender()
    service = AuthService(repository, sender, pepper=b"p" * 32)
    now = datetime(2026, 8, 10, tzinfo=UTC)

    first = await service.request_code(
        "ada@Example.it", source_ip="192.0.2.10", now=now
    )
    first_code = sender.messages[-1][1]
    second = await service.request_code(
        "ada@example.it",
        source_ip="192.0.2.10",
        now=now + timedelta(seconds=61),
    )
    second_code = sender.messages[-1][1]

    assert len(sender.messages) == 2
    assert (
        service.verify_code(
            first.challenge_id,
            first_code,
            source_ip="192.0.2.10",
            now=now + timedelta(seconds=62),
        )
        is None
    )
    assert (
        service.verify_code(
            second.challenge_id,
            second_code,
            source_ip="192.0.2.10",
            now=now + timedelta(seconds=62),
        )
        is not None
    )
    pending = repository.connection.execute(
        "SELECT COUNT(*) FROM otp_challenges WHERE state = 'pending'"
    ).fetchone()[0]
    assert pending == 0


@pytest.mark.asyncio
async def test_request_limits_are_enumeration_safe_and_store_only_digests(
    tmp_path: Path,
) -> None:
    repository = AuthRepository(initialize_control(tmp_path / "control.sqlite3"))
    sender = CapturingSender()
    service = AuthService(repository, sender, pepper=b"p" * 32)
    now = datetime(2026, 8, 10, tzinfo=UTC)

    first = await service.request_code(
        "ada@example.it", source_ip="192.0.2.20", now=now
    )
    limited = await service.request_code(
        "ada@example.it", source_ip="192.0.2.20", now=now
    )

    assert first.public_message == limited.public_message
    assert len(sender.messages) == 1
    stored_keys = " ".join(
        row[0]
        for row in repository.connection.execute(
            "SELECT bucket_key FROM rate_limit_buckets"
        ).fetchall()
    )
    assert "ada@example.it" not in stored_keys
    assert "192.0.2.20" not in stored_keys


def test_concurrent_code_requests_send_once_and_leave_one_pending(
    tmp_path: Path,
) -> None:
    repository = AuthRepository(initialize_control(tmp_path / "control.sqlite3"))
    sender = CapturingSender()
    service = AuthService(repository, sender, pepper=b"p" * 32)
    now = datetime(2026, 8, 10, tzinfo=UTC)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = tuple(
            pool.map(
                lambda _: asyncio.run(
                    service.request_code(
                        "race-request@example.it",
                        source_ip="192.0.2.21",
                        now=now,
                    )
                ),
                range(8),
            )
        )

    assert len(results) == 8
    assert len(sender.messages) == 1
    pending = repository.connection.execute(
        "SELECT COUNT(*) FROM otp_challenges WHERE state = 'pending'"
    ).fetchone()[0]
    assert pending == 1


@pytest.mark.asyncio
async def test_concurrent_verification_creates_exactly_one_session(
    tmp_path: Path,
) -> None:
    repository = AuthRepository(initialize_control(tmp_path / "control.sqlite3"))
    sender = CapturingSender()
    service = AuthService(repository, sender, pepper=b"p" * 32)
    now = datetime(2026, 8, 10, tzinfo=UTC)
    request = await service.request_code(
        "race@example.it", source_ip="192.0.2.30", now=now
    )
    code = sender.messages[-1][1]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = tuple(
            pool.map(
                lambda _: service.verify_code(
                    request.challenge_id,
                    code,
                    source_ip="192.0.2.30",
                    now=now,
                ),
                range(8),
            )
        )

    assert sum(result is not None for result in results) == 1
    sessions = repository.connection.execute(
        "SELECT COUNT(*) FROM sessions"
    ).fetchone()[0]
    assert sessions == 1


@pytest.mark.asyncio
async def test_session_idle_and_absolute_expiry_boundaries(tmp_path: Path) -> None:
    repository = AuthRepository(initialize_control(tmp_path / "control.sqlite3"))
    sender = CapturingSender()
    service = AuthService(repository, sender, pepper=b"p" * 32)
    now = datetime(2026, 8, 10, tzinfo=UTC)

    idle_request = await service.request_code(
        "idle@example.it", source_ip="192.0.2.40", now=now
    )
    idle_session = service.verify_code(
        idle_request.challenge_id,
        sender.messages[-1][1],
        source_ip="192.0.2.40",
        now=now,
    )
    assert idle_session is not None
    assert (
        service.validate_session(
            idle_session.generated.raw_token,
            now=now + SESSION_IDLE_TTL - timedelta(microseconds=1),
        )
        is not None
    )

    absolute_request = await service.request_code(
        "absolute@example.it", source_ip="192.0.2.41", now=now
    )
    absolute_session = service.verify_code(
        absolute_request.challenge_id,
        sender.messages[-1][1],
        source_ip="192.0.2.41",
        now=now,
    )
    assert absolute_session is not None
    assert (
        service.validate_session(
            absolute_session.generated.raw_token,
            now=now + SESSION_ABSOLUTE_TTL,
        )
        is None
    )

    idle_expired_request = await service.request_code(
        "idle-expired@example.it", source_ip="192.0.2.42", now=now
    )
    idle_expired_session = service.verify_code(
        idle_expired_request.challenge_id,
        sender.messages[-1][1],
        source_ip="192.0.2.42",
        now=now,
    )
    assert idle_expired_session is not None
    assert (
        service.validate_session(
            idle_expired_session.generated.raw_token,
            now=now + SESSION_IDLE_TTL,
        )
        is None
    )


@pytest.mark.asyncio
async def test_retention_purge_and_manual_account_deletion(tmp_path: Path) -> None:
    repository = AuthRepository(initialize_control(tmp_path / "control.sqlite3"))
    sender = CapturingSender()
    service = AuthService(repository, sender, pepper=b"p" * 32)
    now = datetime(2026, 8, 10, tzinfo=UTC)
    request = await service.request_code(
        "delete@example.it", source_ip="192.0.2.50", now=now
    )
    verified = service.verify_code(
        request.challenge_id,
        sender.messages[-1][1],
        source_ip="192.0.2.50",
        now=now,
    )
    assert verified is not None
    service.create_api_key(verified.record.account_id, "delete me", now=now)
    assert repository.delete_account(verified.record.account_id) is True
    assert (
        repository.connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        == 0
    )
    assert (
        repository.connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        == 0
    )
    assert (
        repository.connection.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0]
        == 0
    )

    stale = await service.request_code(
        "stale@example.it", source_ip="192.0.2.51", now=now
    )
    repository.connection.execute(
        "UPDATE otp_challenges SET created_at = ? WHERE challenge_id = ?",
        ((now - timedelta(hours=25)).isoformat(), stale.challenge_id),
    )
    repository.connection.execute(
        "UPDATE rate_limit_buckets SET expires_at = ?",
        ((now - timedelta(hours=49)).isoformat(),),
    )
    repository.connection.commit()
    purged = repository.purge_expired_access_state(now)
    assert purged["otp_challenges"] == 1
    assert purged["rate_limit_buckets"] > 0
