import json
import os
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


def test_hostile_text_is_data_and_text_is_bounded(tmp_path: Path) -> None:
    _release(tmp_path, "release-a", "A normal law")
    _activate(tmp_path, "release-a")
    service = QueryService(tmp_path, cursor_secret=b"x" * 32)
    page = service.find_voters(VoteQuery(text="%' OR 1=1 --"), limit=10)
    assert page.items == ()
    with pytest.raises(ValueError, match="200"):
        VoteQuery(text="x" * 201)
