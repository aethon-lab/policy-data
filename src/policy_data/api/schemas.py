from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

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
