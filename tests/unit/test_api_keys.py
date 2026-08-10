from datetime import UTC, datetime

from policy_data.auth.repository import AuthRepository
from policy_data.auth.service import AuthService
from policy_data.storage.connections import initialize_control


class _Sender:
    async def send_otp(self, *, email: str, code: str, idempotency_key: str) -> str:
        return "email-id"


def test_raw_api_key_is_shown_once_never_stored_and_revocation_works(tmp_path) -> None:
    connection = initialize_control(tmp_path / "control.sqlite3")
    connection.execute(
        "INSERT INTO accounts VALUES ('account:1', 'digest', '2026-08-10T00:00:00+00:00', NULL)"
    )
    connection.commit()
    repository = AuthRepository(connection)
    service = AuthService(repository, _Sender(), pepper=b"p" * 32)
    issued = service.create_api_key(
        "account:1", "Cursor", now=datetime(2026, 8, 10, tzinfo=UTC)
    )
    raw = issued.generated.raw
    assert raw.startswith("pd_live_")
    assert raw not in (tmp_path / "control.sqlite3").read_bytes().decode(
        errors="ignore"
    )
    assert service.authenticate_api_key(raw).account_id == "account:1"
    assert repository.revoke_api_key(
        "account:1", issued.record.key_id, datetime(2026, 8, 10, tzinfo=UTC)
    )
    assert service.authenticate_api_key(raw) is None


def test_accounts_cannot_revoke_each_others_keys(tmp_path) -> None:
    connection = initialize_control(tmp_path / "control.sqlite3")
    connection.executemany(
        "INSERT INTO accounts VALUES (?, ?, '2026-08-10T00:00:00+00:00', NULL)",
        [("account:1", "digest1"), ("account:2", "digest2")],
    )
    connection.commit()
    repository = AuthRepository(connection)
    service = AuthService(repository, _Sender(), pepper=b"p" * 32)
    issued = service.create_api_key("account:1", "Agent")
    assert not repository.revoke_api_key(
        "account:2", issued.record.key_id, datetime.now(UTC)
    )
    assert service.authenticate_api_key(issued.generated.raw) is not None
