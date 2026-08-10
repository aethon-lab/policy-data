import csv
import gzip

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


@pytest.mark.parametrize("chamber", ["../camera", "/tmp/camera", "camera/link"])
def test_export_filename_components_are_canonical(chamber: str, tmp_path) -> None:
    dataset = ExportDataset(chamber, "source", "publisher", "license", ())
    with pytest.raises(ValueError, match="canonical"):
        write_exports(tmp_path, (dataset,))
