from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from policy_data.ingest.exports import ExportDataset, write_exports
from policy_data.ingest.lock import RefreshLock
from policy_data.ingest.manifest import manifest_for, release_id_for, source_fingerprint
from policy_data.ingest.publish import activate_release, read_active_release
from policy_data.ingest.validate import validate_database, validate_release_directory
from policy_data.storage.connections import initialize_canonical

SqlValue = str | int | float | bytes | None


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    dataset_id: str
    publisher: str
    license_id: str
    canonical_url: str
    artifact_id: str
    sha256: str
    observed_at: datetime
    media_type: str
    byte_count: int
    adapter_version: str


@dataclass(frozen=True, slots=True)
class ReleaseInput:
    data_through: str
    created_at: datetime
    sources: tuple[SourceSnapshot, ...]
    tables: Mapping[str, tuple[Mapping[str, SqlValue], ...]]
    exports: tuple[ExportDataset, ...]
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class ReleaseBuildResult:
    release_id: str
    path: Path
    created: bool


TABLE_ORDER = (
    "people",
    "source_authorities",
    "source_identities",
    "person_crosswalks",
    "person_aliases",
    "mandates",
    "political_groups",
    "memberships",
    "sittings",
    "parliamentary_items",
    "parliamentary_item_relations",
    "roll_calls",
    "roll_call_items",
    "votes",
    "source_records",
    "fact_lineage",
    "disclosure_documents",
)


class ReleaseBuilder:
    def __init__(
        self, root: Path, *, checkpoint: Callable[[str], None] | None = None
    ) -> None:
        self.root = root
        self.checkpoint = checkpoint or (lambda _: None)

    def active_release_id(self) -> str | None:
        return read_active_release(self.root)

    def build(self, release: ReleaseInput) -> ReleaseBuildResult:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("release root must be a regular directory")
        releases_root = self.root / "releases"
        staging_root = self.root / ".staging"
        releases_root.mkdir(exist_ok=True)
        staging_root.mkdir(exist_ok=True)
        with RefreshLock(self.root / ".refresh.lock"):
            fingerprint = source_fingerprint(release.schema_version, release.sources)
            release_id = release_id_for(fingerprint)
            final_path = releases_root / release_id
            if final_path.is_dir():
                activate_release(self.root, release_id)
                return ReleaseBuildResult(release_id, final_path, False)
            stage = staging_root / f"{release_id}-{uuid.uuid4().hex}"
            stage.mkdir(mode=0o755)
            try:
                self.checkpoint("before_database")
                database_path = stage / "canonical.sqlite3"
                connection = initialize_canonical(database_path)
                try:
                    self._populate(connection, release, release_id, fingerprint)
                    validate_database(connection)
                    self._validate_export_counts(connection, release.exports)
                    connection.commit()
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                finally:
                    connection.close()
                read_only = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
                try:
                    validate_database(read_only)
                finally:
                    read_only.close()
                self.checkpoint("after_database")
                export_files = write_exports(stage, release.exports)
                self.checkpoint("after_exports")
                database_body = database_path.read_bytes()
                manifest = manifest_for(
                    release_id=release_id,
                    schema_version=release.schema_version,
                    fingerprint=fingerprint,
                    data_through=release.data_through,
                    created_at=release.created_at,
                    database_sha256=hashlib.sha256(database_body).hexdigest(),
                    database_bytes=len(database_body),
                    exports=export_files,
                )
                self._write_new(stage / "manifest.json", manifest.to_bytes())
                validate_release_directory(stage, manifest)
                self._fsync_directory(stage)
                self.checkpoint("before_finalize")
                os.replace(stage, final_path)
                self._fsync_directory(releases_root)
                self.checkpoint("before_activate")
                activate_release(self.root, release_id)
                return ReleaseBuildResult(release_id, final_path, True)
            except Exception:
                if (
                    stage.parent == staging_root
                    and stage.is_dir()
                    and not stage.is_symlink()
                ):
                    shutil.rmtree(stage)
                raise

    @staticmethod
    def _populate(
        connection: sqlite3.Connection,
        release: ReleaseInput,
        release_id: str,
        fingerprint: str,
    ) -> None:
        connection.executemany(
            "INSERT INTO legislatures(number, roman_numeral) VALUES (?, ?)",
            [(19, "XIX")],
        )
        connection.executemany(
            "INSERT INTO chambers(code, name) VALUES (?, ?)",
            [("camera", "Camera dei deputati"), ("senato", "Senato della Repubblica")],
        )
        for source in release.sources:
            connection.execute(
                "INSERT INTO source_datasets VALUES (?, ?, ?, ?)",
                (
                    source.dataset_id,
                    source.publisher,
                    source.license_id,
                    source.canonical_url,
                ),
            )
            connection.execute(
                "INSERT INTO source_artifacts VALUES (?, ?, ?, ?, ?, ?)",
                (
                    source.artifact_id,
                    source.dataset_id,
                    source.sha256,
                    source.observed_at.isoformat(),
                    source.media_type,
                    source.byte_count,
                ),
            )
        unknown = set(release.tables) - set(TABLE_ORDER)
        if unknown:
            raise ValueError(f"unsupported canonical tables: {sorted(unknown)!r}")
        for table in TABLE_ORDER:
            for row in release.tables.get(table, ()):
                if not row:
                    raise ValueError(f"empty row for {table}")
                columns = tuple(row)
                placeholders = ",".join("?" for _ in columns)
                column_sql = ",".join(f'"{column}"' for column in columns)
                connection.execute(
                    f'INSERT INTO "{table}" ({column_sql}) VALUES ({placeholders})',
                    tuple(row[column] for column in columns),
                )
        connection.execute(
            "INSERT INTO releases VALUES (?, ?, ?, ?, ?)",
            (
                release_id,
                release.schema_version,
                fingerprint,
                release.data_through,
                release.created_at.isoformat(),
            ),
        )

    @staticmethod
    def _validate_export_counts(
        connection: sqlite3.Connection, exports: tuple[ExportDataset, ...]
    ) -> None:
        for export in exports:
            count = connection.execute(
                "SELECT COUNT(*) FROM votes WHERE chamber_code = ?",
                (export.chamber,),
            ).fetchone()[0]
            if count != len(export.rows):
                raise ValueError(
                    f"{export.chamber} export has {len(export.rows)} rows but database has {count} votes"
                )

    @staticmethod
    def _write_new(path: Path, body: bytes) -> None:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            os.write(descriptor, body)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
