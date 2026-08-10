from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

Scalar = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class ExportDataset:
    chamber: str
    source_id: str
    publisher: str
    license_id: str
    rows: tuple[Mapping[str, Scalar], ...]


@dataclass(frozen=True, slots=True)
class ExportFile:
    filename: str
    sha256: str
    byte_count: int
    row_count: int
    media_type: str
    content_encoding: str
    source_id: str
    publisher: str
    license_id: str


def _canonical_chamber(value: str) -> str:
    if value not in {"camera", "senato"}:
        raise ValueError("export chamber must be a canonical chamber code")
    return value


def _csv_value(value: Scalar) -> Scalar:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


class _HashingWriter:
    def __init__(self, raw: io.BufferedWriter) -> None:
        self.raw = raw
        self.digest = hashlib.sha256()
        self.byte_count = 0

    def write(self, body: bytes) -> int:
        written = self.raw.write(body)
        if written != len(body):
            raise OSError("short export write")
        self.digest.update(body)
        self.byte_count += written
        return written

    def flush(self) -> None:
        self.raw.flush()


def _write_gzip_stream(
    path: Path, write_content: Callable[[gzip.GzipFile], None]
) -> tuple[str, int]:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as raw:
            hashing = _HashingWriter(raw)
            with gzip.GzipFile(
                filename="", fileobj=hashing, mode="wb", mtime=0
            ) as compressed:
                write_content(compressed)
            raw.flush()
            os.fsync(raw.fileno())
            digest = hashing.digest.hexdigest()
            byte_count = hashing.byte_count
        os.chmod(temporary, 0o644)
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        temporary.unlink()
        return digest, byte_count
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _entry(
    path: Path,
    dataset: ExportDataset,
    row_count: int,
    media_type: str,
    digest: str,
    byte_count: int,
) -> ExportFile:
    return ExportFile(
        path.name,
        digest,
        byte_count,
        row_count,
        media_type,
        "gzip",
        dataset.source_id,
        dataset.publisher,
        dataset.license_id,
    )


def write_exports(
    root: Path, datasets: tuple[ExportDataset, ...]
) -> tuple[ExportFile, ...]:
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("export root must be a regular directory")
    entries: list[ExportFile] = []
    seen_chambers: set[str] = set()
    for dataset in datasets:
        chamber = _canonical_chamber(dataset.chamber)
        if chamber in seen_chambers:
            raise ValueError(f"duplicate export dataset for {chamber}")
        seen_chambers.add(chamber)
        fields = sorted({key for row in dataset.rows for key in row})

        csv_path = root / f"{chamber}-votes.csv.gz"

        def write_csv(compressed: gzip.GzipFile) -> None:
            text = io.TextIOWrapper(compressed, encoding="utf-8", newline="")
            writer = csv.DictWriter(text, fieldnames=fields, extrasaction="raise")
            writer.writeheader()
            for row in dataset.rows:
                writer.writerow({key: _csv_value(row.get(key)) for key in fields})
            text.flush()
            text.detach()

        csv_digest, csv_bytes = _write_gzip_stream(csv_path, write_csv)
        entries.append(
            _entry(
                csv_path,
                dataset,
                len(dataset.rows),
                "text/csv",
                csv_digest,
                csv_bytes,
            )
        )

        jsonl_path = root / f"{chamber}-votes.jsonl.gz"

        def write_jsonl(compressed: gzip.GzipFile) -> None:
            for row in dataset.rows:
                line = json.dumps(
                    dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                compressed.write(f"{line}\n".encode())

        jsonl_digest, jsonl_bytes = _write_gzip_stream(jsonl_path, write_jsonl)
        entries.append(
            _entry(
                jsonl_path,
                dataset,
                len(dataset.rows),
                "application/x-ndjson",
                jsonl_digest,
                jsonl_bytes,
            )
        )
    return tuple(entries)
