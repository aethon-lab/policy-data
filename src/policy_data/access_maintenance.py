from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from policy_data.auth.repository import AuthRepository
from policy_data.storage.connections import initialize_control


def main() -> None:
    parser = argparse.ArgumentParser(description="Maintain private access state")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("purge")
    delete = subcommands.add_parser("delete-account")
    delete.add_argument("account_id")
    args = parser.parse_args()

    data_root = Path(os.getenv("POLICY_DATA_DATA_DIR", "data"))
    connection = initialize_control(data_root / "control.sqlite3")
    try:
        repository = AuthRepository(connection)
        if args.command == "purge":
            result = repository.purge_expired_access_state(datetime.now(UTC))
            print(
                "purged " + ", ".join(f"{key}={value}" for key, value in result.items())
            )
        else:
            deleted = repository.delete_account(args.account_id)
            if not deleted:
                raise SystemExit("account not found")
            print(f"deleted account {args.account_id}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
