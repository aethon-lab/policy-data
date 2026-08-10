from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from policy_data.domain.enums import ChamberCode, VotePosition


@dataclass(frozen=True, slots=True)
class VoterResult:
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


@dataclass(frozen=True, slots=True)
class VoterPage:
    items: tuple[VoterResult, ...]
    release_id: str
    next_cursor: str | None
    data_through: str = ""


@dataclass(frozen=True, slots=True)
class CanonicalPage:
    items: tuple[dict[str, Any], ...]
    release_id: str
    data_through: str
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class CanonicalRecord:
    item: dict[str, Any]
    release_id: str
    data_through: str
