import json
import sqlite3
from datetime import UTC, datetime

from policy_data.ingest.exports import ExportDataset
from policy_data.ingest.pipeline import ReleaseBuilder, ReleaseInput, SourceSnapshot


def _release(title: str, sha: str) -> ReleaseInput:
    source = SourceSnapshot(
        dataset_id="camera_votes_xix",
        publisher="Camera dei deputati",
        license_id="CC-BY-SA-4.0",
        canonical_url="https://dati.camera.it/votes",
        artifact_id=f"artifact:{sha}",
        sha256=sha * 64,
        observed_at=datetime(2026, 8, 10, tzinfo=UTC),
        media_type="application/rdf+xml",
        byte_count=100,
        adapter_version="camera-xix-v1",
    )
    tables = {
        "people": ({"person_id": "person:1", "display_name": "Ada Rossi"},),
        "source_authorities": (
            {"authority_id": "camera", "chamber_code": "camera", "name": "Camera"},
        ),
        "source_identities": (
            {
                "identity_id": "identity:1",
                "authority_id": "camera",
                "source_person_id": "1",
                "display_name": "Ada Rossi",
                "canonical_person_id": "person:1",
                "same_as_uri": None,
            },
        ),
        "mandates": (
            {
                "mandate_id": "mandate:1",
                "person_id": "person:1",
                "legislature_number": 19,
                "chamber_code": "camera",
                "starts_on": "2022-10-13",
                "ends_on": None,
            },
        ),
        "sittings": (
            {
                "sitting_id": "sitting:1",
                "legislature_number": 19,
                "chamber_code": "camera",
                "source_sitting_id": "1",
                "sitting_date": "2026-08-10",
            },
        ),
        "parliamentary_items": (
            {
                "item_id": "item:1",
                "legislature_number": 19,
                "chamber_code": "camera",
                "item_type": "bill",
                "source_item_id": "1",
                "title": title,
                "official_url": "https://camera.it/law/1",
            },
        ),
        "roll_calls": (
            {
                "roll_call_id": "roll:1",
                "legislature_number": 19,
                "chamber_code": "camera",
                "sitting_id": "sitting:1",
                "primary_item_id": "item:1",
                "source_vote_id": "1",
                "occurred_at": "2026-08-10T12:00:00Z",
                "positions_available": 1,
                "position_coverage": "complete",
            },
        ),
        "votes": (
            {
                "vote_id": "vote:1",
                "roll_call_id": "roll:1",
                "mandate_id": "mandate:1",
                "legislature_number": 19,
                "chamber_code": "camera",
                "raw_position": "Favorevole",
                "normalized_position": "yes",
                "normalization_status": "normalized",
                "group_id_at_vote": None,
            },
        ),
        "source_records": (
            {
                "source_record_id": "record:person",
                "artifact_id": f"artifact:{sha}",
                "upstream_key": "person:1",
                "record_locator": "people[0]",
                "raw_scope": "person",
                "mapping_version": "camera-xix-v1",
            },
            {
                "source_record_id": "record:item",
                "artifact_id": f"artifact:{sha}",
                "upstream_key": "item:1",
                "record_locator": "items[0]",
                "raw_scope": "item",
                "mapping_version": "camera-xix-v1",
            },
            {
                "source_record_id": "record:mandate",
                "artifact_id": f"artifact:{sha}",
                "upstream_key": "mandate:1",
                "record_locator": "mandates[0]",
                "raw_scope": "mandate",
                "mapping_version": "camera-xix-v1",
            },
            {
                "source_record_id": "record:sitting",
                "artifact_id": f"artifact:{sha}",
                "upstream_key": "sitting:1",
                "record_locator": "sittings[0]",
                "raw_scope": "sitting",
                "mapping_version": "camera-xix-v1",
            },
            {
                "source_record_id": "record:roll",
                "artifact_id": f"artifact:{sha}",
                "upstream_key": "roll:1",
                "record_locator": "rolls[0]",
                "raw_scope": "roll_call",
                "mapping_version": "camera-xix-v1",
            },
            {
                "source_record_id": "record:vote",
                "artifact_id": f"artifact:{sha}",
                "upstream_key": "vote:1",
                "record_locator": "votes[0]",
                "raw_scope": "vote",
                "mapping_version": "camera-xix-v1",
            },
        ),
        "fact_lineage": (
            {
                "fact_type": "person",
                "fact_id": "person:1",
                "source_record_id": "record:person",
                "resolution_rule": "source identity",
            },
            {
                "fact_type": "mandate",
                "fact_id": "mandate:1",
                "source_record_id": "record:mandate",
                "resolution_rule": "source mandate",
            },
            {
                "fact_type": "sitting",
                "fact_id": "sitting:1",
                "source_record_id": "record:sitting",
                "resolution_rule": "source sitting",
            },
            {
                "fact_type": "parliamentary_item",
                "fact_id": "item:1",
                "source_record_id": "record:item",
                "resolution_rule": "source object",
            },
            {
                "fact_type": "roll_call",
                "fact_id": "roll:1",
                "source_record_id": "record:roll",
                "resolution_rule": "source vote",
            },
            {
                "fact_type": "vote",
                "fact_id": "vote:1",
                "source_record_id": "record:vote",
                "resolution_rule": "mapped predicate",
            },
        ),
    }
    export = ExportDataset(
        "camera",
        source.dataset_id,
        source.publisher,
        source.license_id,
        ({"vote_id": "vote:1", "title": title, "position": "yes"},),
    )
    return ReleaseInput(
        data_through="2026-08-10",
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        sources=(source,),
        tables=tables,
        exports=(export,),
    )


def test_release_build_is_valid_immutable_and_idempotent(tmp_path) -> None:
    builder = ReleaseBuilder(tmp_path)
    first = builder.build(_release("Law one", "a"))
    duplicate = builder.build(_release("Law one", "a"))
    assert first.created is True and duplicate.created is False
    assert first.release_id == duplicate.release_id
    assert builder.active_release_id() == first.release_id

    release_dir = tmp_path / "releases" / first.release_id
    manifest = json.loads((release_dir / "manifest.json").read_text())
    assert manifest["release_id"] == first.release_id
    assert {
        entry["license_id"] for entry in manifest["files"] if entry.get("source_id")
    } == {"CC-BY-SA-4.0"}
    connection = sqlite3.connect(
        f"file:{release_dir / 'canonical.sqlite3'}?mode=ro&immutable=1", uri=True
    )
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 1


def test_failed_build_never_replaces_active_release(tmp_path) -> None:
    builder = ReleaseBuilder(tmp_path)
    first = builder.build(_release("Old law", "a"))

    def fail(checkpoint: str) -> None:
        if checkpoint == "before_activate":
            raise RuntimeError("injected failure")

    failing_builder = ReleaseBuilder(tmp_path, checkpoint=fail)
    try:
        failing_builder.build(_release("Corrected law", "b"))
    except RuntimeError:
        pass
    assert builder.active_release_id() == first.release_id
