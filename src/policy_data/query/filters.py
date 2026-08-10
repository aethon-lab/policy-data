from __future__ import annotations

from dataclasses import dataclass

from policy_data.domain.enums import ChamberCode, VotePosition


@dataclass(frozen=True, slots=True)
class VoteQuery:
    text: str | None = None
    position: VotePosition | None = None
    chamber: ChamberCode | None = None
    legislature: int | None = None
    group_id: str | None = None
    person_id: str | None = None

    def __post_init__(self) -> None:
        if self.text is not None and len(self.text) > 200:
            raise ValueError("query text cannot exceed 200 characters")
        if self.legislature is not None and (
            isinstance(self.legislature, bool) or self.legislature <= 0
        ):
            raise ValueError("legislature must be a positive integer")
        for value, field in (
            (self.group_id, "group_id"),
            (self.person_id, "person_id"),
        ):
            if value is not None and not value.strip():
                raise ValueError(f"{field} cannot be blank")
