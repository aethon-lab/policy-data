from datetime import date

import pytest

from policy_data.domain.enums import VotePosition
from policy_data.sources.senato import (
    SenatoGroupMembership,
    SenatoQuarantine,
    map_senato_position,
    resolve_group_at_vote,
)


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        ("favorevole", VotePosition.YES),
        ("contrario", VotePosition.NO),
        ("astenuto", VotePosition.ABSTAIN),
        ("inCongedoMissione", VotePosition.LEAVE_OR_MISSION),
        ("presenteNonVotante", VotePosition.PRESENT_NOT_VOTING),
        ("richiedenteNonVotante", VotePosition.REQUESTER_NOT_VOTING),
        ("presidente", VotePosition.PRESIDING),
    ],
)
def test_senato_position_predicates_map_once(
    predicate: str, expected: VotePosition
) -> None:
    assert map_senato_position(predicate) is expected


def test_unknown_senato_position_is_quarantined() -> None:
    with pytest.raises(SenatoQuarantine, match="unmapped"):
        map_senato_position("forse")


def test_group_resolution_handles_changes_and_rejects_ambiguity() -> None:
    memberships = (
        SenatoGroupMembership(
            "m1", "p1", "g1", "Alpha", date(2022, 10, 13), date(2024, 3, 1), "Membro"
        ),
        SenatoGroupMembership(
            "m2", "p1", "g2", "Beta", date(2024, 3, 2), None, "Membro"
        ),
    )
    assert resolve_group_at_vote(memberships, "p1", date(2024, 2, 20))[0] == "g1"
    assert resolve_group_at_vote(memberships, "p1", date(2024, 3, 20))[0] == "g2"
    assert resolve_group_at_vote(memberships, "p1", date(2024, 3, 1))[0] == "g1"
    overlapping = memberships + (
        SenatoGroupMembership(
            "m3", "p1", "g3", "Gamma", date(2024, 2, 1), None, "Membro"
        ),
    )
    group_id, diagnostic = resolve_group_at_vote(overlapping, "p1", date(2024, 2, 20))
    assert group_id is None and "ambiguous" in diagnostic
