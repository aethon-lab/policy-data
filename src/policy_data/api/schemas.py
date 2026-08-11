from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from policy_data.domain.enums import ChamberCode, VotePosition


class VoterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vote_id: str
    roll_call_id: str
    person_id: str
    person_name: str
    position: VotePosition
    raw_position: str
    group_id_at_vote: str | None
    group_name_at_vote: str | None
    legislature: int
    chamber: ChamberCode
    occurred_at: str
    official_type: str
    official_result: str | None
    measure_id: str | None
    measure_title: str | None
    measure_url: str | None
    vote_url: str | None
    publisher: str | None
    license_id: str | None


class VoterPageResponse(BaseModel):
    items: list[VoterResponse]
    release_id: str
    data_through: str
    next_cursor: str | None


class HealthResponse(BaseModel):
    status: str
    release_id: str | None


class CanonicalPageResponse(BaseModel):
    items: list[dict[str, Any]]
    release_id: str
    data_through: str
    next_cursor: str | None


class CanonicalRecordResponse(BaseModel):
    item: dict[str, Any]
    release_id: str
    data_through: str


class PersonIdentityResponse(BaseModel):
    authority_id: str
    authority_name: str
    chamber: ChamberCode
    source_person_id: str
    source_display_name: str
    same_as_uri: str | None


class PersonMandateResponse(BaseModel):
    mandate_id: str
    legislature: int
    chamber: ChamberCode
    starts_on: str | None
    ends_on: str | None


class PersonMembershipResponse(BaseModel):
    membership_id: str
    mandate_id: str
    group_id: str
    group_name: str
    group_abbreviation: str | None
    legislature: int
    chamber: ChamberCode
    starts_on: str
    ends_on: str | None


class PersonVoteSummaryResponse(BaseModel):
    recorded: int = 0
    normalized: int = 0
    yes: int = 0
    no: int = 0
    abstain: int = 0
    present_not_voting: int = 0
    did_not_vote: int = 0
    not_participating: int = 0
    mission: int = 0
    leave: int = 0
    leave_or_mission: int = 0
    requester_not_voting: int = 0
    presiding: int = 0
    not_in_office: int = 0
    secret_participation: int = 0
    absent_explicit: int = 0
    not_recorded: int = 0
    unknown: int = 0
    unmapped: int = 0


class PersonCoverageResponse(BaseModel):
    mandate_id: str
    legislature: int
    chamber: ChamberCode
    published_roll_calls: int
    complete_roll_calls: int
    partial_roll_calls: int
    unavailable_roll_calls: int
    secret_roll_calls: int
    roll_calls_with_positions: int
    recorded_person_votes: int
    expected_person_votes: int
    coverage_status: Literal[
        "complete", "partial", "unavailable", "secret", "not_applicable"
    ]


class PersonDisclosureResponse(BaseModel):
    disclosure_id: str
    official_label: str
    official_url: str
    observed_at: str


class PersonProfileResponse(BaseModel):
    person_id: str
    display_name: str
    identities: list[PersonIdentityResponse] = Field(default_factory=list)
    mandates: list[PersonMandateResponse] = Field(default_factory=list)
    memberships: list[PersonMembershipResponse] = Field(default_factory=list)
    vote_summary: PersonVoteSummaryResponse = Field(
        default_factory=PersonVoteSummaryResponse
    )
    coverage: list[PersonCoverageResponse] = Field(default_factory=list)
    disclosures: list[PersonDisclosureResponse] = Field(default_factory=list)


class PersonRecordResponse(BaseModel):
    item: PersonProfileResponse
    release_id: str
    data_through: str
