import json
import os
import sqlite3
from pathlib import Path

import pytest

from policy_data.domain.enums import ChamberCode, VotePosition
from policy_data.query.filters import VoteQuery
from policy_data.query.pagination import InvalidCursor
from policy_data.query.service import QueryService
from policy_data.storage.connections import initialize_canonical


def _release(root: Path, release_id: str, title: str) -> None:
    release_dir = root / "releases" / release_id
    release_dir.mkdir(parents=True)
    connection = initialize_canonical(release_dir / "canonical.sqlite3")
    connection.executemany("INSERT INTO legislatures VALUES (?, ?)", [(19, "XIX")])
    connection.executemany(
        "INSERT INTO chambers VALUES (?, ?)",
        [("camera", "Camera dei deputati"), ("senato", "Senato")],
    )
    connection.execute("INSERT INTO people VALUES ('person:1', 'Ada Rossi')")
    connection.execute(
        "INSERT INTO source_authorities VALUES ('camera', 'camera', 'Camera')"
    )
    connection.execute(
        "INSERT INTO source_identities VALUES ('identity:1', 'camera', '1', 'Ada Rossi', 'person:1', NULL)"
    )
    connection.execute(
        "INSERT INTO mandates VALUES ('mandate:1', 'person:1', 19, 'camera', '2022-10-13', NULL)"
    )
    connection.execute(
        "INSERT INTO political_groups VALUES ('group:1', 19, 'camera', 'Gruppo Alfa', 'GA')"
    )
    connection.execute(
        "INSERT INTO memberships VALUES ('membership:1', 'mandate:1', 'group:1', 19, 'camera', '2022-10-13', NULL)"
    )
    connection.execute(
        "INSERT INTO sittings VALUES ('sitting:1', 19, 'camera', '599', '2026-01-22')"
    )
    connection.execute(
        "INSERT INTO parliamentary_items VALUES ('item:1', 19, 'camera', 'law', 'L. 1/2026', ?, 'https://camera.it/legge/1')",
        (title,),
    )
    connection.execute(
        """INSERT INTO roll_calls(
               roll_call_id, legislature_number, chamber_code, sitting_id,
               primary_item_id, source_vote_id, occurred_at, official_type,
               official_title, official_result, official_url,
               positions_available, position_coverage
           ) VALUES (
               'roll:1', 19, 'camera', 'sitting:1', 'item:1', '599044',
               '2026-01-22T12:00:00Z', 'final', 'Votazione finale',
               'approved', 'https://camera.it/voto/599044', 1, 'complete'
           )"""
    )
    connection.execute(
        "INSERT INTO votes VALUES ('vote:1', 'roll:1', 'mandate:1', 19, 'camera', 'Favorevole', 'yes', 'normalized', 'group:1')"
    )
    connection.execute(
        "INSERT INTO source_datasets VALUES ('camera_votes', 'Camera dei deputati', 'CC-BY-SA-4.0', 'https://dati.camera.it')"
    )
    connection.execute(
        "INSERT INTO source_artifacts VALUES ('artifact:1', 'camera_votes', ?, '2026-08-10T00:00:00+00:00', 'application/rdf+xml', 1)",
        (release_id.removeprefix("release-").ljust(64, "0")[:64],),
    )
    connection.execute(
        "INSERT INTO source_records VALUES ('record:vote', 'artifact:1', '599044/person:1', 'votes[0]', 'member_vote', 'camera-xix-v1')"
    )
    connection.execute(
        "INSERT INTO fact_lineage VALUES ('vote', 'vote:1', 'record:vote', 'mapped official detail row')"
    )
    connection.execute(
        "INSERT INTO releases VALUES (?, 1, ?, '2026-01-22', '2026-08-10T00:00:00+00:00')",
        (release_id, release_id),
    )
    connection.commit()
    connection.close()


def _activate(root: Path, release_id: str) -> None:
    temporary = root / ".active.tmp"
    temporary.write_text(json.dumps({"release_id": release_id}))
    os.replace(temporary, root / "active.json")


def test_measure_query_returns_people_positions_links_and_provenance(
    tmp_path: Path,
) -> None:
    _release(tmp_path, "release-a", "Conversione Superbonus")
    _activate(tmp_path, "release-a")
    service = QueryService(tmp_path, cursor_secret=b"x" * 32)
    page = service.find_voters(
        VoteQuery(
            text="Superbonus",
            position=VotePosition.YES,
            chamber=ChamberCode.CAMERA,
            legislature=19,
        ),
        limit=20,
    )
    assert len(page.items) == 1
    item = page.items[0]
    assert item.person_id == "person:1" and item.person_name == "Ada Rossi"
    assert item.position is VotePosition.YES
    assert item.group_name_at_vote == "Gruppo Alfa"
    assert item.measure_title == "Conversione Superbonus"
    assert item.measure_url == "https://camera.it/legge/1"
    assert item.vote_url == "https://camera.it/voto/599044"
    assert item.publisher == "Camera dei deputati"
    assert item.license_id == "CC-BY-SA-4.0"
    assert page.release_id == "release-a"


def test_cursor_is_signed_and_pins_retained_release(tmp_path: Path) -> None:
    _release(tmp_path, "release-a", "Superbonus old")
    database = tmp_path / "releases" / "release-a" / "canonical.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO people VALUES ('person:2', 'Bruno Bianchi')")
    connection.execute(
        "INSERT INTO mandates VALUES ('mandate:2', 'person:2', 19, 'camera', '2022-10-13', NULL)"
    )
    connection.execute(
        "INSERT INTO votes VALUES ('vote:2', 'roll:1', 'mandate:2', 19, 'camera', 'Favorevole', 'yes', 'normalized', 'group:1')"
    )
    connection.commit()
    connection.close()
    _activate(tmp_path, "release-a")
    service = QueryService(tmp_path, cursor_secret=b"x" * 32)
    first = service.find_voters(VoteQuery(text="Superbonus"), limit=1)
    assert first.next_cursor is not None

    _release(tmp_path, "release-b", "Different measure")
    _activate(tmp_path, "release-b")
    pinned = service.find_voters(
        VoteQuery(text="Superbonus"), limit=1, cursor=first.next_cursor
    )
    assert pinned.release_id == "release-a"

    with pytest.raises(InvalidCursor):
        service.find_voters(
            VoteQuery(text="Superbonus"),
            limit=1,
            cursor=first.next_cursor[:-1] + "A",
        )


def test_exact_terminal_page_has_no_cursor(tmp_path: Path) -> None:
    _release(tmp_path, "release-a", "Superbonus")
    _activate(tmp_path, "release-a")
    page = QueryService(tmp_path, cursor_secret=b"x" * 32).find_voters(
        VoteQuery(text="Superbonus"), limit=1
    )
    assert page.next_cursor is None
    assert page.data_through == "2026-01-22"


def test_hostile_text_is_data_and_text_is_bounded(tmp_path: Path) -> None:
    _release(tmp_path, "release-a", "A normal law")
    _activate(tmp_path, "release-a")
    service = QueryService(tmp_path, cursor_secret=b"x" * 32)
    page = service.find_voters(VoteQuery(text="%' OR 1=1 --"), limit=10)
    assert page.items == ()
    with pytest.raises(ValueError, match="200"):
        VoteQuery(text="x" * 201)


def test_canonical_resources_share_release_metadata(tmp_path: Path) -> None:
    _release(tmp_path, "release-a", "Conversione Superbonus")
    _activate(tmp_path, "release-a")
    service = QueryService(tmp_path, cursor_secret=b"x" * 32)

    pages = (
        service.list_legislatures(),
        service.list_people(text="Ada"),
        service.list_groups(legislature=19, chamber=ChamberCode.CAMERA),
        service.list_roll_calls(text="Votazione", legislature=19),
        service.list_roll_call_positions("roll:1"),
        service.list_disclosures(person_id="person:1"),
    )
    assert all(page.release_id == "release-a" for page in pages)
    assert all(page.data_through == "2026-01-22" for page in pages)
    assert pages[0].items[0]["number"] == 19
    assert pages[1].items[0]["person_id"] == "person:1"
    assert pages[4].items[0]["position"] == "yes"

    person = service.get_person("person:1")
    assert person is not None
    assert person.item["disclosures"] == []
    assert person.item["identities"] == [
        {
            "authority_id": "camera",
            "authority_name": "Camera",
            "chamber": "camera",
            "source_person_id": "1",
            "source_display_name": "Ada Rossi",
            "same_as_uri": None,
        }
    ]
    assert person.item["mandates"] == [
        {
            "mandate_id": "mandate:1",
            "legislature": 19,
            "chamber": "camera",
            "starts_on": "2022-10-13",
            "ends_on": None,
        }
    ]
    assert person.item["memberships"] == [
        {
            "membership_id": "membership:1",
            "mandate_id": "mandate:1",
            "group_id": "group:1",
            "group_name": "Gruppo Alfa",
            "group_abbreviation": "GA",
            "legislature": 19,
            "chamber": "camera",
            "starts_on": "2022-10-13",
            "ends_on": None,
        }
    ]
    assert person.item["vote_summary"] == {
        "recorded": 1,
        "normalized": 1,
        "yes": 1,
        "no": 0,
        "abstain": 0,
        "present_not_voting": 0,
        "did_not_vote": 0,
        "not_participating": 0,
        "mission": 0,
        "leave": 0,
        "leave_or_mission": 0,
        "requester_not_voting": 0,
        "presiding": 0,
        "not_in_office": 0,
        "secret_participation": 0,
        "absent_explicit": 0,
        "not_recorded": 0,
        "unknown": 0,
        "unmapped": 0,
    }
    assert person.item["coverage"] == [
        {
            "mandate_id": "mandate:1",
            "legislature": 19,
            "chamber": "camera",
            "published_roll_calls": 1,
            "complete_roll_calls": 1,
            "partial_roll_calls": 0,
            "unavailable_roll_calls": 0,
            "secret_roll_calls": 0,
            "roll_calls_with_positions": 1,
            "recorded_person_votes": 1,
            "expected_person_votes": 1,
            "coverage_status": "complete",
        }
    ]
    assert service.get_roll_call("roll:1").item["official_result"] == "approved"  # type: ignore[union-attr]
    assert service.dataset_status().item["counts"]["votes"] == 1


def test_person_profile_distinguishes_missing_vote_data_from_no_votes(
    tmp_path: Path,
) -> None:
    _release(tmp_path, "release-a", "Conversione Superbonus")
    database = tmp_path / "releases" / "release-a" / "canonical.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM votes")
    connection.commit()
    connection.close()
    _activate(tmp_path, "release-a")

    person = QueryService(tmp_path, cursor_secret=b"x" * 32).get_person("person:1")

    assert person is not None
    assert person.item["vote_summary"]["recorded"] == 0
    assert person.item["vote_summary"]["absent_explicit"] == 0
    assert person.item["coverage"][0]["published_roll_calls"] == 1
    assert person.item["coverage"][0]["recorded_person_votes"] == 0
    assert person.item["coverage"][0]["coverage_status"] == "partial"


@pytest.mark.parametrize("position", tuple(VotePosition))
def test_person_profile_preserves_each_normalized_position(
    tmp_path: Path, position: VotePosition
) -> None:
    _release(tmp_path, "release-a", "Votazione")
    database = tmp_path / "releases" / "release-a" / "canonical.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE votes SET raw_position = ?, normalized_position = ?",
        (position.value, position.value),
    )
    connection.commit()
    connection.close()
    _activate(tmp_path, "release-a")

    person = QueryService(tmp_path, cursor_secret=b"x" * 32).get_person("person:1")

    assert person is not None
    assert person.item["vote_summary"][position.value] == 1
    assert person.item["vote_summary"]["recorded"] == 1


@pytest.mark.parametrize(
    ("source_coverage", "expected_status", "expected_person_votes"),
    (
        ("complete", "complete", 1),
        ("partial", "partial", 0),
        ("unavailable", "unavailable", 0),
        ("secret", "secret", 0),
    ),
)
def test_person_profile_keeps_source_coverage_states_distinct(
    tmp_path: Path,
    source_coverage: str,
    expected_status: str,
    expected_person_votes: int,
) -> None:
    _release(tmp_path, "release-a", "Votazione")
    database = tmp_path / "releases" / "release-a" / "canonical.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE roll_calls SET position_coverage = ?, positions_available = ?",
        (source_coverage, int(source_coverage in {"complete", "partial"})),
    )
    connection.commit()
    connection.close()
    _activate(tmp_path, "release-a")

    person = QueryService(tmp_path, cursor_secret=b"x" * 32).get_person("person:1")

    assert person is not None
    coverage = person.item["coverage"][0]
    assert coverage["coverage_status"] == expected_status
    assert coverage["expected_person_votes"] == expected_person_votes


def test_person_votes_can_be_pinned_to_profile_release(tmp_path: Path) -> None:
    _release(tmp_path, "release-a", "Original")
    _release(tmp_path, "release-b", "Replacement")
    _activate(tmp_path, "release-b")
    service = QueryService(tmp_path, cursor_secret=b"x" * 32)

    page = service.list_person_votes("person:1", release_id="release-a")

    assert page.release_id == "release-a"
    assert page.items[0].measure_title == "Original"


def test_structural_queries_use_canonical_indexes(tmp_path: Path) -> None:
    _release(tmp_path, "release-a", "Superbonus")
    database = tmp_path / "releases" / "release-a" / "canonical.sqlite3"
    connection = sqlite3.connect(database)
    plan = " ".join(
        str(row)
        for row in connection.execute(
            "EXPLAIN QUERY PLAN SELECT vote_id FROM votes WHERE roll_call_id = ? AND normalization_status = 'normalized' ORDER BY vote_id",
            ("roll:1",),
        )
    )
    connection.close()
    assert "idx_votes_roll_call_order" in plan
