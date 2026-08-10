import csv
import gzip
import hashlib

import pytest

from policy_data.ingest.exports import ExportDataset, write_exports


def test_exports_are_deterministic_attributed_and_formula_safe(tmp_path) -> None:
    dataset = ExportDataset(
        chamber="camera",
        source_id="camera_votes_xix",
        publisher="Camera dei deputati",
        license_id="CC-BY-SA-4.0",
        rows=(
            {"vote_id": "vote:1", "title": '=HYPERLINK("bad")', "position": "yes"},
            {"vote_id": "vote:2", "title": "+SUM(1,1)", "position": "no"},
        ),
    )
    files = write_exports(tmp_path, (dataset,))
    csv_entry = next(entry for entry in files if entry.media_type == "text/csv")
    jsonl_entry = next(
        entry for entry in files if entry.media_type == "application/x-ndjson"
    )

    with gzip.open(tmp_path / csv_entry.filename, "rt", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["title"].startswith("'=")
    assert rows[1]["title"].startswith("'+")
    with gzip.open(tmp_path / jsonl_entry.filename, "rt") as handle:
        assert "=HYPERLINK" in handle.readline()
    assert csv_entry.row_count == jsonl_entry.row_count == 2
    assert csv_entry.source_id == "camera_votes_xix"
    assert csv_entry.license_id == "CC-BY-SA-4.0"
    assert (
        csv_entry.sha256
        == hashlib.sha256((tmp_path / csv_entry.filename).read_bytes()).hexdigest()
    )
    assert csv_entry.byte_count == (tmp_path / csv_entry.filename).stat().st_size


@pytest.mark.parametrize("chamber", ["../camera", "/tmp/camera", "camera/link"])
def test_export_filename_components_are_canonical(chamber: str, tmp_path) -> None:
    dataset = ExportDataset(chamber, "source", "publisher", "license", ())
    with pytest.raises(ValueError, match="canonical"):
        write_exports(tmp_path, (dataset,))


def test_streamed_gzip_is_deterministic_and_never_overwrites(tmp_path) -> None:
    dataset = ExportDataset(
        "senato",
        "senato_votes_xix",
        "Senato della Repubblica",
        "CC-BY-3.0",
        tuple({"vote_id": f"vote:{index}", "position": "yes"} for index in range(50)),
    )
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first = write_exports(first_root, (dataset,))
    second = write_exports(second_root, (dataset,))
    assert [(item.filename, item.sha256, item.byte_count) for item in first] == [
        (item.filename, item.sha256, item.byte_count) for item in second
    ]

    with pytest.raises(FileExistsError):
        write_exports(first_root, (dataset,))
    assert not list(first_root.glob(".*.tmp"))
