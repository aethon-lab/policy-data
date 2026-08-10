from pathlib import Path

from policy_data.domain.enums import VotePosition
from policy_data.sources.camera import CameraArtifactSet, CameraAdapter

FIXTURES = Path("tests/fixtures/camera")


def test_camera_fixture_normalizes_all_official_types_and_reconciles_detail() -> None:
    artifacts = CameraArtifactSet(
        votes_rdf=(FIXTURES / "votes.rdf").read_bytes(),
        deputies_rdf=(FIXTURES / "deputies.rdf").read_bytes(),
        mandates_rdf=(FIXTURES / "mandates.rdf").read_bytes(),
        groups_rdf=(FIXTURES / "groups.rdf").read_bytes(),
        detail_html={
            "https://documenti.camera.it/apps/votazioni/votazionitutte/schedaVotazione.asp?Legislatura=19&RifVotazione=599_44&tipo=dettaglio": (
                FIXTURES / "vote_detail.html"
            ).read_text()
        },
    )
    result = CameraAdapter().normalize(artifacts)

    assert result.quarantined == ()
    assert {vote.official_type for vote in result.roll_calls} == {
        "amendment",
        "article",
        "confidence",
        "final",
    }
    assert (
        next(
            vote for vote in result.roll_calls if vote.source_vote_id == "599044"
        ).position_coverage
        == "complete"
    )
    assert {vote.position for vote in result.member_votes} == {
        VotePosition.YES,
        VotePosition.ABSTAIN,
    }
    assert len(result.people) == 2 and len(result.mandates) == 2


def test_secret_vote_has_no_manufactured_member_rows() -> None:
    artifacts = CameraArtifactSet(
        votes_rdf=(FIXTURES / "votes.rdf").read_bytes(),
        deputies_rdf=(FIXTURES / "deputies.rdf").read_bytes(),
        mandates_rdf=(FIXTURES / "mandates.rdf").read_bytes(),
        groups_rdf=(FIXTURES / "groups.rdf").read_bytes(),
        detail_html={},
    )
    result = CameraAdapter().normalize(artifacts)
    secret = next(vote for vote in result.roll_calls if vote.is_secret)
    assert secret.position_coverage == "secret"
    assert not [
        vote for vote in result.member_votes if vote.roll_call_id == secret.roll_call_id
    ]
