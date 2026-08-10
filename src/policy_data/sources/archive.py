from __future__ import annotations

import io
import stat
import zipfile
from pathlib import PurePosixPath


class ArchiveRejected(ValueError):
    pass


def read_safe_zip(
    body: bytes,
    *,
    max_entries: int,
    max_expanded_bytes: int,
    max_compression_ratio: int = 200,
) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile as error:
        raise ArchiveRejected("invalid ZIP archive") from error
    with archive:
        infos = archive.infolist()
        if len(infos) > max_entries:
            raise ArchiveRejected("archive has too many entries")
        total = 0
        for info in infos:
            path = PurePosixPath(info.filename)
            mode = info.external_attr >> 16
            if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
                raise ArchiveRejected("archive entry has unsafe path")
            if info.is_dir():
                continue
            file_type = stat.S_IFMT(mode)
            if stat.S_ISLNK(mode) or (file_type and not stat.S_ISREG(mode)):
                raise ArchiveRejected("archive entry is not a regular file")
            if info.flag_bits & 0x1:
                raise ArchiveRejected("encrypted archive entries are unsupported")
            total += info.file_size
            if total > max_expanded_bytes:
                raise ArchiveRejected("archive exceeds expanded byte limit")
            if info.file_size > 0 and info.compress_size == 0:
                raise ArchiveRejected("archive has invalid compression metadata")
            if (
                info.compress_size
                and info.file_size / info.compress_size > max_compression_ratio
            ):
                raise ArchiveRejected("archive compression ratio is excessive")
            result[str(path)] = archive.read(info)
    return result


def reject_xml_dtd(body: bytes) -> None:
    prefix = body[:65536].upper()
    if b"<!DOCTYPE" in prefix or b"<!ENTITY" in prefix:
        raise ArchiveRejected("XML DTD or entity declarations are forbidden")
