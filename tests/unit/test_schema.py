from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from policy_data.storage.connections import initialize_canonical, initialize_control


def _canonical(tmp_path: Path) -> sqlite3.Connection:
    connection = initialize_canonical(tmp_path / "canonical.sqlite3")
    connection.executemany(
        "INSERT INTO legislatures(number, roman_numeral) VALUES (?, ?)",
        [(18, "XVIII"), (19, "XIX")],
    )
    connection.executemany(
        "INSERT INTO chambers(code, name) VALUES (?, ?)",
        [("camera", "Camera dei deputati"), ("senato", "Senato")],
    )
    return connection


def test_canonical_and_control_schemas_initialize_independently(tmp_path: Path) -> None:
    canonical = initialize_canonical(tmp_path / "canonical.sqlite3")
    control = initialize_control(tmp_path / "control.sqlite3")

    assert canonical.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert control.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert canonical.execute("PRAGMA foreign_key_check").fetchall() == []
    assert control.execute("PRAGMA foreign_key_check").fetchall() == []
    assert (
        canonical.execute(
            "SELECT version FROM schema_versions WHERE component = 'canonical'"
        ).fetchone()[0]
        == 1
    )
    assert (
        control.execute(
            "SELECT version FROM schema_versions WHERE component = 'control'"
        ).fetchone()[0]
        == 1
    )
    with pytest.raises(sqlite3.OperationalError):
        canonical.execute("SELECT * FROM ingestion_runs")
    with pytest.raises(sqlite3.OperationalError):
        control.execute("SELECT * FROM people")


def test_prior_legislature_uses_the_same_contract_as_xix(tmp_path: Path) -> None:
    connection = _canonical(tmp_path)
    connection.execute(
        "INSERT INTO people(person_id, display_name) VALUES ('person:42', 'Ada')"
    )
    connection.executemany(
        """INSERT INTO mandates(
               mandate_id, person_id, legislature_number, chamber_code
           ) VALUES (?, 'person:42', ?, 'camera')""",
        [("mandate:xviii", 18), ("mandate:xix", 19)],
    )

    assert connection.execute(
        "SELECT legislature_number FROM mandates ORDER BY legislature_number"
    ).fetchall() == [(18,), (19,)]


def test_source_identities_are_authority_scoped_and_do_not_auto_merge(
    tmp_path: Path,
) -> None:
    connection = _canonical(tmp_path)
    connection.executemany(
        "INSERT INTO people(person_id, display_name) VALUES (?, 'Same Name')",
        [("person:camera",), ("person:senato",)],
    )
    connection.executemany(
        "INSERT INTO source_authorities(authority_id, chamber_code, name) VALUES (?, ?, ?)",
        [
            ("authority:camera", "camera", "Camera"),
            ("authority:senato", "senato", "Senato"),
        ],
    )
    connection.executemany(
        """INSERT INTO source_identities(
               identity_id, authority_id, source_person_id, display_name,
               canonical_person_id, same_as_uri
           ) VALUES (?, ?, '42', 'Same Name', ?, 'https://example.test/42')""",
        [
            ("identity:camera", "authority:camera", "person:camera"),
            ("identity:senato", "authority:senato", "person:senato"),
        ],
    )

    assert (
        connection.execute(
            "SELECT COUNT(DISTINCT canonical_person_id) FROM source_identities"
        ).fetchone()[0]
        == 2
    )


def test_cross_scope_membership_roll_call_vote_and_item_relation_fail(
    tmp_path: Path,
) -> None:
    connection = _canonical(tmp_path)
    connection.execute(
        "INSERT INTO people(person_id, display_name) VALUES ('person:42', 'Ada')"
    )
    connection.execute(
        "INSERT INTO mandates VALUES ('mandate:camera', 'person:42', 19, 'camera', NULL, NULL)"
    )
    connection.execute(
        "INSERT INTO political_groups VALUES ('group:senato', 19, 'senato', 'G', NULL)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO memberships VALUES (
                   'membership:bad', 'mandate:camera', 'group:senato',
                   19, 'camera', '2022-10-13', NULL
               )"""
        )

    connection.execute(
        "INSERT INTO sittings VALUES ('sitting:senato', 19, 'senato', '1', '2022-10-13')"
    )
    connection.execute(
        """INSERT INTO parliamentary_items(
               item_id, legislature_number, chamber_code, item_type,
               source_item_id, title
           ) VALUES ('item:camera', 19, 'camera', 'bill', '1', 'Bill')"""
    )
    connection.execute(
        """INSERT INTO parliamentary_items(
               item_id, legislature_number, chamber_code, item_type,
               source_item_id, title
           ) VALUES ('item:senato', 19, 'senato', 'bill', '1', 'Bill')"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO roll_calls(
                   roll_call_id, legislature_number, chamber_code, sitting_id,
                   primary_item_id, source_vote_id, occurred_at
               ) VALUES (
                   'roll:bad', 19, 'camera', 'sitting:senato', 'item:camera',
                   '1', '2022-10-13T12:00:00Z'
               )"""
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO parliamentary_item_relations VALUES (
                   'relation:bad', 19, 'camera', 'item:camera', 'item:senato',
                   'refers_to'
               )"""
        )

    connection.execute(
        "INSERT INTO sittings VALUES ('sitting:camera', 19, 'camera', '1', '2022-10-13')"
    )
    connection.execute(
        """INSERT INTO roll_calls(
               roll_call_id, legislature_number, chamber_code, sitting_id,
               primary_item_id, source_vote_id, occurred_at
           ) VALUES (
               'roll:camera', 19, 'camera', 'sitting:camera', 'item:camera',
               '1', '2022-10-13T12:00:00Z'
           )"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO votes VALUES (
                   'vote:bad', 'roll:camera', 'mandate:camera', 19, 'senato',
                   'Favorevole', 'yes', 'normalized', NULL
               )"""
        )


def test_aliases_are_permanent_and_review_backed(tmp_path: Path) -> None:
    connection = _canonical(tmp_path)
    connection.executemany(
        "INSERT INTO people(person_id, display_name) VALUES (?, 'Ada')",
        [("person:survivor",), ("person:alias",)],
    )
    connection.execute(
        """INSERT INTO person_crosswalks VALUES (
               'crosswalk:1', 1, 'approved', 'person:survivor',
               'reviewer', '2026-08-10T00:00:00Z', NULL
           )"""
    )
    connection.execute(
        """INSERT INTO person_aliases VALUES (
               'person:alias', 'person:survivor', 'crosswalk:1', 1,
               '2026-08-10T00:00:00Z'
           )"""
    )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE person_aliases SET canonical_person_id = 'person:alias'"
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM person_aliases")
