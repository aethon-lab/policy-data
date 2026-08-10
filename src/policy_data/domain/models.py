"""Small immutable domain contracts independent of persistence technology."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from policy_data.domain.enums import (
    ChamberCode,
    CrosswalkStatus,
    NormalizationStatus,
    VotePosition,
)


def _non_empty(value: str, field_name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value.strip()


@dataclass(frozen=True, slots=True, order=True)
class MandateScope:
    legislature: int
    chamber: ChamberCode

    def __post_init__(self) -> None:
        if isinstance(self.legislature, bool) or self.legislature <= 0:
            raise ValueError("legislature must be a positive integer")


@dataclass(frozen=True, slots=True)
class Mandate:
    mandate_id: str
    person_id: str
    legislature: int
    chamber: ChamberCode

    def __post_init__(self) -> None:
        _non_empty(self.mandate_id, "mandate_id")
        _non_empty(self.person_id, "person_id")
        MandateScope(self.legislature, self.chamber)

    @property
    def scope(self) -> MandateScope:
        return MandateScope(self.legislature, self.chamber)


@dataclass(frozen=True, slots=True)
class PersonMatchEvidence:
    left_identity_id: str
    right_identity_id: str
    evidence_type: str
    evidence_value: str

    def __post_init__(self) -> None:
        for field_name in (
            "left_identity_id",
            "right_identity_id",
            "evidence_type",
            "evidence_value",
        ):
            _non_empty(getattr(self, field_name), field_name)
        if self.left_identity_id == self.right_identity_id:
            raise ValueError("match evidence must relate two distinct identities")


@dataclass(frozen=True, slots=True)
class PersonResolution:
    survivor_id: str
    alias_ids: tuple[str, ...]


def resolve_approved_person_ids(
    person_ids: Iterable[str],
    *,
    existing_aliases: Mapping[str, str] | None = None,
) -> PersonResolution:
    """Resolve an already-approved match deterministically.

    An existing alias target is permanent and wins over lexical ordering. For
    a new match, the lexical minimum makes rebuild order irrelevant.
    """

    candidates = {_non_empty(person_id, "person_id") for person_id in person_ids}
    if not candidates:
        raise ValueError("at least one person_id is required")

    aliases = dict(existing_aliases or {})
    applicable_targets = {
        target
        for alias, target in aliases.items()
        if alias in candidates or target in candidates
    }
    if len(applicable_targets) > 1:
        raise ValueError("existing aliases disagree on the permanent survivor")
    survivor = next(iter(applicable_targets), min(candidates))
    candidates.add(survivor)
    return PersonResolution(survivor, tuple(sorted(candidates - {survivor})))


@dataclass(frozen=True, slots=True)
class CrosswalkDecision:
    crosswalk_id: str
    version: int
    status: CrosswalkStatus
    canonical_person_id: str
    identity_ids: tuple[str, ...]
    reviewed_by: str | None
    reviewed_at: datetime | None

    def __post_init__(self) -> None:
        _non_empty(self.crosswalk_id, "crosswalk_id")
        _non_empty(self.canonical_person_id, "canonical_person_id")
        if self.version <= 0:
            raise ValueError("crosswalk version must be positive")
        if len(set(self.identity_ids)) < 2:
            raise ValueError("a crosswalk must contain at least two identities")
        if self.status in {
            CrosswalkStatus.APPROVED,
            CrosswalkStatus.REJECTED,
            CrosswalkStatus.SUPERSEDED,
        } and (not self.reviewed_by or self.reviewed_at is None):
            raise ValueError("reviewed decisions require reviewer and reviewed_at")

    @property
    def permits_merge(self) -> bool:
        return self.status is CrosswalkStatus.APPROVED

    def resolve(self, person_ids: Iterable[str]) -> PersonResolution:
        if not self.permits_merge:
            raise ValueError("only an approved crosswalk permits a merge")
        candidates = {_non_empty(person_id, "person_id") for person_id in person_ids}
        candidates.add(self.canonical_person_id)
        return PersonResolution(
            self.canonical_person_id,
            tuple(sorted(candidates - {self.canonical_person_id})),
        )


@dataclass(frozen=True, slots=True)
class NormalizedVote:
    official_value: str | None
    normalized_position: VotePosition | None
    normalization_status: NormalizationStatus

    def __post_init__(self) -> None:
        if self.normalization_status is NormalizationStatus.NORMALIZED:
            if self.normalized_position is None:
                raise ValueError("normalized status requires a normalized position")
        elif self.normalized_position is not None:
            raise ValueError(
                f"{self.normalization_status.value} status requires a missing normalized position"
            )
