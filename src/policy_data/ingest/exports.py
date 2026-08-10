from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
from collections.abc import Mapping
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


def _gzip_bytes(body: bytes) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as stream:
        stream.write(body)
    return output.getvalue()


def _write_new_regular_file(path: Path, body: bytes) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _entry(
    path: Path, dataset: ExportDataset, row_count: int, media_type: str
) -> ExportFile:
    body = path.read_bytes()
    return ExportFile(
        path.name,
        hashlib.sha256(body).hexdigest(),
        len(body),
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

        csv_buffer = io.StringIO(newline="")
        writer = csv.DictWriter(csv_buffer, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        for row in dataset.rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in fields})
        csv_path = root / f"{chamber}-votes.csv.gz"
        _write_new_regular_file(csv_path, _gzip_bytes(csv_buffer.getvalue().encode()))
        entries.append(_entry(csv_path, dataset, len(dataset.rows), "text/csv"))

        jsonl = b"".join(
            (
                json.dumps(
                    dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                + "\n"
            ).encode()
            for row in dataset.rows
        )
        jsonl_path = root / f"{chamber}-votes.jsonl.gz"
        _write_new_regular_file(jsonl_path, _gzip_bytes(jsonl))
        entries.append(
            _entry(jsonl_path, dataset, len(dataset.rows), "application/x-ndjson")
        )
    return tuple(entries)
