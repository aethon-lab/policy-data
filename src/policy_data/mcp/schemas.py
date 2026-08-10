from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from policy_data.api.schemas import VoterResponse


class McpVoterPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[VoterResponse]
    release_id: str
    next_cursor: str | None
