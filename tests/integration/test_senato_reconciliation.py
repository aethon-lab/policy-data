from datetime import UTC, datetime
from pathlib import Path

from policy_data.domain.enums import VotePosition
from policy_data.sources.senato import SenatoAdapter, SenatoArtifactSet

FIXTURES = Path("tests/fixtures/senato")


def _artifacts(*vote_windows: bytes) -> SenatoArtifactSet:
    return SenatoArtifactSet(
        vote_windows=vote_windows,
        people_json=(FIXTURES / "people.json").read_bytes(),
        groups_json=(FIXTURES / "groups.json").read_bytes(),
        observed_at=datetime(2026, 8, 10, tzinfo=UTC),
    )


def test_senato_fixture_normalizes_people_votes_groups_and_disclosures() -> None:
    result = SenatoAdapter().normalize(
        _artifacts((FIXTURES / "votes.json").read_bytes())
    )
    assert result.quarantined == ()
    assert len(result.people) == 2 and len(result.mandates) == 2
    assert len(result.roll_calls) == 1 and len(result.member_votes) == 2
    assert {vote.position for vote in result.member_votes} == {
        VotePosition.YES,
        VotePosition.NO,
    }
    assert {vote.group_name_at_vote for vote in result.member_votes} == {"Gruppo Alfa"}
    disclosure = result.disclosures[0]
    assert disclosure.year == 2024
    assert disclosure.official_url.endswith("36401-2024.pdf")
    assert disclosure.observed_at == datetime(2026, 8, 10, tzinfo=UTC)
    assert result.crosswalk_candidates[0].endswith("p36401")


def test_overlapping_windows_deduplicate_the_same_vote() -> None:
    window = (FIXTURES / "votes.json").read_bytes()
    result = SenatoAdapter().normalize(_artifacts(window, window))
    assert len(result.roll_calls) == 1
    assert len(result.member_votes) == 2
