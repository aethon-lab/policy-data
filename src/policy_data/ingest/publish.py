from __future__ import annotations

import json
import os
from pathlib import Path


def atomic_write(path: Path, body: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)


def activate_release(root: Path, release_id: str) -> None:
    if (
        not release_id.startswith("release-")
        or not release_id.replace("-", "").isalnum()
    ):
        raise ValueError("invalid release ID")
    atomic_write(
        root / "active.json",
        (json.dumps({"release_id": release_id}, sort_keys=True) + "\n").encode(),
    )


def read_active_release(root: Path) -> str | None:
    path = root / "active.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))["release_id"]
    if not isinstance(value, str) or not (root / "releases" / value).is_dir():
        raise ValueError("active release handle is invalid")
    return value
