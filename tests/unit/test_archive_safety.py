from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from policy_data.ingest.lock import RefreshAlreadyRunning, RefreshLock
from policy_data.sources.archive import ArchiveRejected, read_safe_zip, reject_xml_dtd


def _zip(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return output.getvalue()


def test_safe_zip_returns_bounded_regular_entries() -> None:
    entries = read_safe_zip(
        _zip({"votes.rdf": b"<rdf />"}), max_entries=2, max_expanded_bytes=64
    )
    assert entries == {"votes.rdf": b"<rdf />"}


def test_zip_slip_and_expansion_bomb_are_rejected() -> None:
    with pytest.raises(ArchiveRejected, match="path"):
        read_safe_zip(_zip({"../escape": b"x"}), max_entries=2, max_expanded_bytes=64)
    with pytest.raises(ArchiveRejected, match="expanded"):
        read_safe_zip(_zip({"large": b"x" * 128}), max_entries=2, max_expanded_bytes=64)


def test_xml_doctype_is_rejected_before_parsing() -> None:
    with pytest.raises(ArchiveRejected, match="DTD"):
        reject_xml_dtd(
            b'<!DOCTYPE rdf [<!ENTITY x SYSTEM "file:///etc/passwd">]><rdf>&x;</rdf>'
        )


def test_refresh_lock_is_exclusive(tmp_path: Path) -> None:
    path = tmp_path / "refresh.lock"
    with RefreshLock(path):
        with pytest.raises(RefreshAlreadyRunning):
            with RefreshLock(path):
                pass
    with RefreshLock(path):
        assert path.exists()
