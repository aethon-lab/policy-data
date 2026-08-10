from __future__ import annotations

from datetime import UTC, datetime

import pytest

from policy_data.domain.enums import (
    ChamberCode,
    CrosswalkStatus,
    FactLayer,
    NormalizationStatus,
    VotePosition,
)
from policy_data.domain.models import (
    CrosswalkDecision,
    Mandate,
    NormalizedVote,
    PersonMatchEvidence,
    resolve_approved_person_ids,
)
from policy_data.domain.provenance import FactValue


def test_approved_person_resolution_is_order_independent_and_keeps_aliases() -> None:
    candidates = ["person:z", "person:a", "person:m"]

    forward = resolve_approved_person_ids(candidates)
    backward = resolve_approved_person_ids(reversed(candidates))

    assert forward == backward
    assert forward.survivor_id == "person:a"
    assert forward.alias_ids == ("person:m", "person:z")


def test_existing_permanent_alias_controls_future_resolution() -> None:
    resolution = resolve_approved_person_ids(
        ["person:0", "person:survivor", "person:old-alias"],
        existing_aliases={"person:old-alias": "person:survivor"},
    )

    assert resolution.survivor_id == "person:survivor"
    assert resolution.alias_ids == ("person:0", "person:old-alias")


def test_matching_evidence_does_not_merge_people_without_reviewed_approval() -> None:
    evidence = PersonMatchEvidence(
        left_identity_id="source_identity:camera",
        right_identity_id="source_identity:senato",
        evidence_type="owl:sameAs",
        evidence_value="https://example.test/person/42",
    )
    proposed = CrosswalkDecision(
        crosswalk_id="crosswalk:42",
        version=1,
        status=CrosswalkStatus.PROPOSED,
        canonical_person_id="person:42",
        identity_ids=(evidence.left_identity_id, evidence.right_identity_id),
        reviewed_by=None,
        reviewed_at=None,
    )

    assert not proposed.permits_merge
    with pytest.raises(ValueError, match="approved"):
        proposed.resolve(["person:camera-42", "person:senato-42"])


def test_approved_crosswalk_requires_review_attribution() -> None:
    with pytest.raises(ValueError, match="reviewed"):
        CrosswalkDecision(
            crosswalk_id="crosswalk:42",
            version=1,
            status=CrosswalkStatus.APPROVED,
            canonical_person_id="person:42",
            identity_ids=("source_identity:camera", "source_identity:senato"),
            reviewed_by=None,
            reviewed_at=datetime.now(UTC),
        )


def test_person_id_is_independent_while_mandates_are_scoped() -> None:
    camera = Mandate(
        mandate_id="mandate:camera-19",
        person_id="person:42",
        legislature=19,
        chamber=ChamberCode.CAMERA,
    )
    senato = Mandate(
        mandate_id="mandate:senato-18",
        person_id="person:42",
        legislature=18,
        chamber=ChamberCode.SENATO,
    )

    assert camera.person_id == senato.person_id
    assert camera.scope != senato.scope


def test_missing_normalized_vote_remains_explicitly_missing() -> None:
    vote = NormalizedVote(
        official_value="Non ha partecipato",
        normalized_position=None,
        normalization_status=NormalizationStatus.MISSING,
    )

    assert vote.normalized_position is None
    assert vote.official_value == "Non ha partecipato"


def test_normalized_vote_rejects_value_status_contradictions() -> None:
    with pytest.raises(ValueError, match="missing"):
        NormalizedVote(
            official_value="Favorevole",
            normalized_position=VotePosition.YES,
            normalization_status=NormalizationStatus.MISSING,
        )


def test_fact_layers_keep_source_normalized_derived_and_interpreted_distinct() -> None:
    source = FactValue(
        layer=FactLayer.SOURCE,
        value="Favorevole",
        source_record_id="source_record:1",
    )
    normalized = FactValue(
        layer=FactLayer.NORMALIZED,
        value=VotePosition.YES.value,
        source_record_id="source_record:1",
    )

    assert source.layer is FactLayer.SOURCE
    assert normalized.layer is FactLayer.NORMALIZED
    assert source.value != normalized.value
