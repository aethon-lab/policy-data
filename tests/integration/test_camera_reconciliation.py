from pathlib import Path

from rdflib import Graph, Namespace
from rdflib.namespace import RDF

from policy_data.domain.enums import VotePosition
from policy_data.sources.camera import CameraArtifactSet, CameraAdapter

FIXTURES = Path("tests/fixtures/camera")
OCD = Namespace("http://dati.camera.it/ocd/")


def _as_ntriples(path: Path) -> bytes:
    graph = Graph().parse(path, format="xml")
    serialized = graph.serialize(format="nt")
    return serialized.encode() if isinstance(serialized, str) else serialized


def test_camera_accepts_the_ntriples_serialization_used_by_official_dumps() -> None:
    result = CameraAdapter().normalize(
        CameraArtifactSet(
            votes_rdf=_as_ntriples(FIXTURES / "votes.rdf"),
            deputies_rdf=_as_ntriples(FIXTURES / "deputies.rdf"),
            mandates_rdf=_as_ntriples(FIXTURES / "mandates.rdf"),
            groups_rdf=_as_ntriples(FIXTURES / "groups.rdf"),
            detail_html={},
        )
    )

    assert len(result.roll_calls) == 5
    assert len(result.people) == 2
    assert len(result.member_votes) == 2


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


def test_camera_detail_supplies_positions_missing_from_bulk_rdf() -> None:
    graph = Graph().parse(FIXTURES / "votes.rdf", format="xml")
    for subject in tuple(graph.subjects(RDF.type, OCD.voto)):
        graph.remove((subject, None, None))
    rdf = graph.serialize(format="xml")
    result = CameraAdapter().normalize(
        CameraArtifactSet(
            votes_rdf=rdf.encode() if isinstance(rdf, str) else rdf,
            deputies_rdf=(FIXTURES / "deputies.rdf").read_bytes(),
            mandates_rdf=(FIXTURES / "mandates.rdf").read_bytes(),
            groups_rdf=(FIXTURES / "groups.rdf").read_bytes(),
            detail_html={
                "https://documenti.camera.it/apps/votazioni/votazionitutte/schedaVotazione.asp?Legislatura=19&RifVotazione=599_44&tipo=dettaglio": (
                    FIXTURES / "vote_detail.html"
                ).read_text()
            },
        )
    )

    roll = next(vote for vote in result.roll_calls if vote.source_vote_id == "599044")
    assert roll.position_coverage == "complete"
    assert (
        len(
            [
                vote
                for vote in result.member_votes
                if vote.roll_call_id == roll.roll_call_id
            ]
        )
        == 2
    )


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


def test_quarantined_detail_does_not_leak_earlier_member_rows() -> None:
    detail = (
        (FIXTURES / "vote_detail.html")
        .read_text()
        .replace("<td>Astensione</td>", "<td>Valore sconosciuto</td>")
    )
    artifacts = CameraArtifactSet(
        votes_rdf=(FIXTURES / "votes.rdf").read_bytes(),
        deputies_rdf=(FIXTURES / "deputies.rdf").read_bytes(),
        mandates_rdf=(FIXTURES / "mandates.rdf").read_bytes(),
        groups_rdf=(FIXTURES / "groups.rdf").read_bytes(),
        detail_html={
            "https://documenti.camera.it/apps/votazioni/votazionitutte/schedaVotazione.asp?Legislatura=19&RifVotazione=599_44&tipo=dettaglio": detail
        },
    )
    result = CameraAdapter().normalize(artifacts)
    roll = next(vote for vote in result.roll_calls if vote.source_vote_id == "599044")
    assert roll.position_coverage == "partial"
    assert not [
        vote for vote in result.member_votes if vote.roll_call_id == roll.roll_call_id
    ]


def test_detail_total_mismatch_is_partial_and_emits_no_rows() -> None:
    detail = (
        (FIXTURES / "vote_detail.html")
        .read_text()
        .replace("<p>FAVOREVOLI</p><p>1</p>", "<p>FAVOREVOLI</p><p>2</p>")
    )
    artifacts = CameraArtifactSet(
        votes_rdf=(FIXTURES / "votes.rdf").read_bytes(),
        deputies_rdf=(FIXTURES / "deputies.rdf").read_bytes(),
        mandates_rdf=(FIXTURES / "mandates.rdf").read_bytes(),
        groups_rdf=(FIXTURES / "groups.rdf").read_bytes(),
        detail_html={
            "https://documenti.camera.it/apps/votazioni/votazionitutte/schedaVotazione.asp?Legislatura=19&RifVotazione=599_44&tipo=dettaglio": detail
        },
    )

    result = CameraAdapter().normalize(artifacts)
    roll = next(vote for vote in result.roll_calls if vote.source_vote_id == "599044")
    assert roll.position_coverage == "partial"
    assert "official totals" in " ".join(result.quarantined)
    assert not [
        vote for vote in result.member_votes if vote.roll_call_id == roll.roll_call_id
    ]


def test_rdf_detail_identity_mismatch_is_partial() -> None:
    rdf = (
        (FIXTURES / "votes.rdf")
        .read_bytes()
        .replace(
            b'<ocd:rif_deputato rdf:resource="http://dati.camera.it/ocd/deputato.rdf/d999002_19"/>',
            b'<ocd:rif_deputato rdf:resource="http://dati.camera.it/ocd/deputato.rdf/d999999_19"/>',
        )
    )
    artifacts = CameraArtifactSet(
        votes_rdf=rdf,
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
    roll = next(vote for vote in result.roll_calls if vote.source_vote_id == "599044")
    assert roll.position_coverage == "partial"
    assert "identity sets disagree" in " ".join(result.quarantined)


def test_rdf_positions_reconcile_official_rdf_totals_without_detail() -> None:
    rdf = (
        (FIXTURES / "votes.rdf")
        .read_bytes()
        .replace(b">1</ocd:favorevoli>", b">2</ocd:favorevoli>", 1)
    )
    result = CameraAdapter().normalize(
        CameraArtifactSet(
            votes_rdf=rdf,
            deputies_rdf=(FIXTURES / "deputies.rdf").read_bytes(),
            mandates_rdf=(FIXTURES / "mandates.rdf").read_bytes(),
            groups_rdf=(FIXTURES / "groups.rdf").read_bytes(),
            detail_html={},
        )
    )
    roll = next(vote for vote in result.roll_calls if vote.source_vote_id == "599044")
    assert roll.position_coverage == "partial"
    assert "RDF positions disagree" in " ".join(result.quarantined)
    assert not [
        vote for vote in result.member_votes if vote.roll_call_id == roll.roll_call_id
    ]
