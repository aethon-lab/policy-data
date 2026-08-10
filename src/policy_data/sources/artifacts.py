from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from policy_data.sources.registry import SourceDefinition


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    source_id: str
    sha256: str
    path: Path
    metadata_path: Path
    etag: str | None
    last_modified: str | None


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def persist(
        self,
        source: SourceDefinition,
        body: bytes,
        *,
        media_type: str,
        etag: str | None,
        last_modified: str | None,
    ) -> StoredArtifact:
        digest = hashlib.sha256(body).hexdigest()
        artifact_dir = self.root / "sha256" / digest[:2] / digest
        artifact_dir.mkdir(parents=True, exist_ok=True)
        body_path = artifact_dir / "body"
        metadata_path = artifact_dir / "metadata.json"
        if not body_path.exists():
            self._atomic_write(body_path, body)
        metadata = {
            "artifact_id": f"sha256:{digest}",
            "sha256": digest,
            "source_id": source.source_id,
            "source_url": source.url,
            "publisher": source.publisher,
            "dataset": source.dataset,
            "legislature": source.legislature,
            "chamber": source.chamber,
            "license": source.license_id,
            "adapter_version": source.adapter_version,
            "media_type": media_type,
            "byte_count": len(body),
            "etag": etag,
            "last_modified": last_modified,
            "observed_at": datetime.now(UTC).isoformat(),
        }
        self._atomic_write(
            metadata_path,
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode(),
        )
        latest_path = self._latest_path(source.source_id)
        latest_path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(
            latest_path,
            json.dumps(
                {"sha256": digest, "etag": etag, "last_modified": last_modified},
                sort_keys=True,
            ).encode(),
        )
        return StoredArtifact(
            source.source_id, digest, body_path, metadata_path, etag, last_modified
        )

    def latest(self, source_id: str) -> StoredArtifact | None:
        latest_path = self._latest_path(source_id)
        if not latest_path.exists():
            return None
        record = json.loads(latest_path.read_text(encoding="utf-8"))
        digest = record["sha256"]
        artifact_dir = self.root / "sha256" / digest[:2] / digest
        body_path = artifact_dir / "body"
        metadata_path = artifact_dir / "metadata.json"
        if not body_path.is_file() or not metadata_path.is_file():
            raise ValueError("latest artifact is incomplete")
        if hashlib.sha256(body_path.read_bytes()).hexdigest() != digest:
            raise ValueError("latest artifact hash does not verify")
        return StoredArtifact(
            source_id,
            digest,
            body_path,
            metadata_path,
            record.get("etag"),
            record.get("last_modified"),
        )

    def _latest_path(self, source_id: str) -> Path:
        if not source_id.replace("_", "").replace("-", "").isalnum():
            raise ValueError("unsafe source ID")
        return self.root / "latest" / f"{source_id}.json"

    @staticmethod
    def _atomic_write(path: Path, body: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
