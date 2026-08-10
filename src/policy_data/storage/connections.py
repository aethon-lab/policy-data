from __future__ import annotations

import sqlite3
from pathlib import Path


def _initialize(path: Path, schema_name: str) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    schema_path = Path(__file__).with_name(schema_name)
    connection.executescript(schema_path.read_text(encoding="utf-8"))
    return connection


def initialize_canonical(path: Path) -> sqlite3.Connection:
    return _initialize(path, "schema.sql")


def initialize_control(path: Path) -> sqlite3.Connection:
    connection = _initialize(path, "control_schema.sql")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection
