from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime

from policy_data.ingest.exports import ExportFile


@dataclass(frozen=True, slots=True)
class ManifestFile:
    filename: str
    sha256: str
    byte_count: int
    row_count: int | None
    media_type: str
    content_encoding: str | None
    source_id: str | None
    publisher: str | None
    license_id: str | None


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    release_id: str
    schema_version: int
    source_fingerprint: str
    data_through: str
    created_at: str
    files: tuple[ManifestFile, ...]

    def to_bytes(self) -> bytes:
        return (
            json.dumps(
                asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode()


def parse_manifest(body: bytes) -> ReleaseManifest:
    value = json.loads(body)
    if not isinstance(value, dict) or not isinstance(value.get("files"), list):
        raise ValueError("release manifest has an invalid shape")
    try:
        files = tuple(ManifestFile(**entry) for entry in value.pop("files"))
        return ReleaseManifest(files=files, **value)
    except (TypeError, KeyError) as error:
        raise ValueError("release manifest has invalid fields") from error


def source_fingerprint(schema_version: int, sources: tuple[object, ...]) -> str:
    values = [
        {
            "dataset_id": getattr(source, "dataset_id"),
            "sha256": getattr(source, "sha256"),
            "adapter_version": getattr(source, "adapter_version"),
        }
        for source in sources
    ]
    body = json.dumps(
        {
            "schema_version": schema_version,
            "sources": sorted(values, key=lambda item: item["dataset_id"]),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(body).hexdigest()


def release_id_for(fingerprint: str) -> str:
    return f"release-{fingerprint[:24]}"


def manifest_for(
    *,
    release_id: str,
    schema_version: int,
    fingerprint: str,
    data_through: str,
    created_at: datetime,
    database_sha256: str,
    database_bytes: int,
    exports: tuple[ExportFile, ...],
) -> ReleaseManifest:
    database = ManifestFile(
        "canonical.sqlite3",
        database_sha256,
        database_bytes,
        None,
        "application/vnd.sqlite3",
        None,
        None,
        None,
        None,
    )
    export_files = tuple(
        ManifestFile(
            entry.filename,
            entry.sha256,
            entry.byte_count,
            entry.row_count,
            entry.media_type,
            entry.content_encoding,
            entry.source_id,
            entry.publisher,
            entry.license_id,
        )
        for entry in exports
    )
    return ReleaseManifest(
        release_id,
        schema_version,
        fingerprint,
        data_through,
        created_at.isoformat(),
        (database, *export_files),
    )
