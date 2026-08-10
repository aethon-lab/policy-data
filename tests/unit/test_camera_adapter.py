from pathlib import Path

import pytest
from rdflib import Literal
from rdflib.namespace import XSD

from policy_data.domain.enums import VotePosition
from policy_data.sources.camera import (
    CameraQuarantine,
    map_camera_position,
    parse_camera_boolean,
    parse_vote_detail,
)

FIXTURES = Path("tests/fixtures/camera")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Favorevole", VotePosition.YES),
        ("Contrario", VotePosition.NO),
        ("Astensione", VotePosition.ABSTAIN),
        ("Non ha votato", VotePosition.DID_NOT_VOTE),
        ("Non ha partecipato", VotePosition.NOT_PARTICIPATING),
        ("In missione", VotePosition.MISSION),
        ("Presidente di turno", VotePosition.PRESIDING),
    ],
)
def test_camera_positions_preserve_distinct_semantics(
    raw: str, expected: VotePosition
) -> None:
    assert map_camera_position(raw) is expected


def test_detail_parser_reads_totals_and_member_rows() -> None:
    detail = parse_vote_detail((FIXTURES / "vote_detail.html").read_text())
    assert detail.sitting_number == "599"
    assert detail.vote_number == "44"
    assert detail.totals == {
        "present": 216,
        "voting": 190,
        "abstain": 26,
        "majority": 96,
        "yes": 190,
        "no": 0,
    }
    assert [(row.name, row.group, row.raw_position) for row in detail.rows] == [
        ("ALBANO LUCIA", "FDI", "Favorevole"),
        ("ALIFANO ENRICA", "M5S", "Astensione"),
    ]


def test_browser_challenge_is_not_a_vote_detail() -> None:
    with pytest.raises(CameraQuarantine, match="challenge"):
        parse_vote_detail(
            "<html><h1>Checking your browser before accessing</h1></html>"
        )


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        (Literal(True, datatype=XSD.boolean), True),
        (Literal(False, datatype=XSD.boolean), False),
        (Literal(1, datatype=XSD.integer), True),
        (Literal(0, datatype=XSD.integer), False),
    ],
)
def test_camera_boolean_accepts_declared_boolean_and_observed_integer(
    literal: Literal, expected: bool
) -> None:
    parsed = parse_camera_boolean(literal)
    assert parsed.value is expected
    assert parsed.raw_datatype == str(literal.datatype)


def test_camera_boolean_rejects_other_values() -> None:
    with pytest.raises(CameraQuarantine, match="boolean"):
        parse_camera_boolean(Literal(2, datatype=XSD.integer))
