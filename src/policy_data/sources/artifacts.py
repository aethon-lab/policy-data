from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Iterable
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
        return self.persist_stream(
            source,
            (body,),
            media_type=media_type,
            etag=etag,
            last_modified=last_modified,
            max_bytes=len(body),
        )

    def persist_stream(
        self,
        source: SourceDefinition,
        chunks: Iterable[bytes],
        *,
        media_type: str,
        etag: str | None,
        last_modified: str | None,
        max_bytes: int,
    ) -> StoredArtifact:
        """Persist a bounded stream without materializing the artifact in memory."""
        self.root.mkdir(parents=True, exist_ok=True)
        self._require_directory(self.root)
        temporary_root = self.root / ".tmp"
        temporary_root.mkdir(exist_ok=True, mode=0o700)
        self._require_directory(temporary_root)
        os.chmod(temporary_root, 0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="artifact-", dir=temporary_root
        )
        temporary = Path(temporary_name)
        digest_state = hashlib.sha256()
        byte_count = 0
        try:
            with os.fdopen(descriptor, "wb") as stream:
                for chunk in chunks:
                    if not chunk:
                        continue
                    byte_count += len(chunk)
                    if byte_count > max_bytes:
                        raise ValueError("source exceeded maximum byte count")
                    digest_state.update(chunk)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            digest = digest_state.hexdigest()
            hash_root = self.root / "sha256"
            hash_root.mkdir(exist_ok=True)
            self._require_directory(hash_root)
            prefix_root = hash_root / digest[:2]
            prefix_root.mkdir(exist_ok=True)
            self._require_directory(prefix_root)
            artifact_dir = prefix_root / digest
            artifact_dir.mkdir(exist_ok=True)
            self._require_directory(artifact_dir)
            body_path = artifact_dir / "body"
            metadata_path = artifact_dir / "metadata.json"
            try:
                os.link(temporary, body_path)
            except FileExistsError:
                self._verify_body(body_path, digest)
            self._fsync_directory(artifact_dir)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        temporary.unlink(missing_ok=True)
        metadata = {
            "artifact_id": f"sha256:{digest}",
            "sha256": digest,
            "source_id": source.source_id,
            "source_url": source.request_url,
            "publisher": source.publisher,
            "dataset": source.dataset,
            "legislature": source.legislature,
            "chamber": source.chamber,
            "license": source.license_id,
            "adapter_version": source.adapter_version,
            "media_type": media_type,
            "byte_count": byte_count,
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
        self._require_directory(latest_path.parent)
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
        if not metadata_path.is_file():
            raise ValueError("latest artifact is incomplete")
        self._verify_body(body_path, digest)
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
    def _require_directory(path: Path) -> None:
        try:
            details = path.stat(follow_symlinks=False)
        except FileNotFoundError as error:
            raise ValueError(f"artifact directory is missing: {path.name}") from error
        if not stat.S_ISDIR(details.st_mode):
            raise ValueError(f"artifact path is not a private directory: {path.name}")

    @staticmethod
    def _atomic_write(path: Path, body: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            ArtifactStore._fsync_directory(path.parent)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _verify_body(path: Path, expected_digest: str) -> None:
        try:
            details = path.stat(follow_symlinks=False)
        except FileNotFoundError as error:
            raise ValueError("latest artifact is incomplete") from error
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise ValueError("artifact body must be an unlinked regular file")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_digest:
            raise ValueError("latest artifact hash does not verify")

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
