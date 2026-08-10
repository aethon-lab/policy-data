from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from policy_data.domain.enums import VotePosition
from policy_data.ingest.manifest import ReleaseManifest


class ReleaseValidationError(ValueError):
    pass


LINEAGE_TARGETS = {
    "people": ("person", "person_id"),
    "mandates": ("mandate", "mandate_id"),
    "political_groups": ("political_group", "group_id"),
    "memberships": ("membership", "membership_id"),
    "sittings": ("sitting", "sitting_id"),
    "parliamentary_items": ("parliamentary_item", "item_id"),
    "roll_calls": ("roll_call", "roll_call_id"),
    "votes": ("vote", "vote_id"),
    "disclosure_documents": ("disclosure_document", "disclosure_id"),
}


def validate_database(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ReleaseValidationError("SQLite integrity check failed")
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        raise ReleaseValidationError(
            f"SQLite foreign key check failed: {foreign_keys!r}"
        )
    allowed_positions = tuple(position.value for position in VotePosition)
    placeholders = ",".join("?" for _ in allowed_positions)
    invalid = connection.execute(
        f"SELECT vote_id FROM votes WHERE normalized_position IS NOT NULL AND normalized_position NOT IN ({placeholders})",
        allowed_positions,
    ).fetchone()
    if invalid:
        raise ReleaseValidationError(
            f"vote {invalid[0]} has an invalid normalized position"
        )
    for table, (fact_type, id_column) in LINEAGE_TARGETS.items():
        missing = connection.execute(
            f"""SELECT entity.{id_column}
                FROM {table} AS entity
                LEFT JOIN fact_lineage AS lineage
                  ON lineage.fact_type = ? AND lineage.fact_id = entity.{id_column}
                WHERE lineage.fact_id IS NULL LIMIT 1""",
            (fact_type,),
        ).fetchone()
        if missing:
            raise ReleaseValidationError(
                f"{fact_type} {missing[0]} has no resolvable source lineage"
            )


def validate_release_directory(root: Path, manifest: ReleaseManifest) -> None:
    expected = {
        "canonical.sqlite3",
        "manifest.json",
        *(entry.filename for entry in manifest.files),
    }
    actual = {path.name for path in root.iterdir()}
    if actual != expected:
        raise ReleaseValidationError("release directory contains unmanifested files")
    for path in root.iterdir():
        stat = path.lstat()
        if path.is_symlink() or not path.is_file() or stat.st_nlink != 1:
            raise ReleaseValidationError(
                f"release file is not a private regular file: {path.name}"
            )
    parsed = json.loads((root / "manifest.json").read_bytes())
    if parsed["release_id"] != manifest.release_id:
        raise ReleaseValidationError("manifest release identity disagrees")
    for entry in manifest.files:
        body = (root / entry.filename).read_bytes()
        if (
            len(body) != entry.byte_count
            or hashlib.sha256(body).hexdigest() != entry.sha256
        ):
            raise ReleaseValidationError(
                f"manifest checksum disagrees for {entry.filename}"
            )
