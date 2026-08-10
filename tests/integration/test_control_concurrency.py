from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from policy_data.auth.repository import AuthRepository
from policy_data.storage.connections import initialize_control


def test_control_repository_serializes_cross_thread_access(tmp_path: Path) -> None:
    repository = AuthRepository(initialize_control(tmp_path / "control.sqlite3"))
    repository.connection.execute(
        "INSERT INTO accounts VALUES (?, ?, ?, NULL)",
        ("acct_test", "email-digest", datetime.now(UTC).isoformat()),
    )
    repository.connection.commit()
    repository.create_api_key(
        key_id="key_test",
        account_id="acct_test",
        lookup_prefix="pd_live_lookup",
        key_digest="digest",
        label="test",
        now=datetime.now(UTC),
    )

    with ThreadPoolExecutor(max_workers=8) as pool:
        records = tuple(
            pool.map(
                lambda _: repository.authenticate_api_key("pd_live_lookup", "digest"),
                range(40),
            )
        )

    assert all(record is not None and record.key_id == "key_test" for record in records)
