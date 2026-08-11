from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from policy_data.api.schemas import PersonProfileResponse, VoterResponse


class McpVoterPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[VoterResponse]
    release_id: str
    data_through: str
    next_cursor: str | None


class McpCanonicalPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    release_id: str
    data_through: str
    next_cursor: str | None


class McpCanonicalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: dict[str, object]
    release_id: str
    data_through: str


class McpPersonRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: PersonProfileResponse
    release_id: str
    data_through: str
