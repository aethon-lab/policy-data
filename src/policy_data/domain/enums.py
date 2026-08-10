"""Closed vocabularies shared by ingestion, storage, and presentation."""

from __future__ import annotations

from enum import StrEnum


class ChamberCode(StrEnum):
    CAMERA = "camera"
    SENATO = "senato"


class VotePosition(StrEnum):
    YES = "yes"
    NO = "no"
    ABSTAIN = "abstain"
    PRESENT_NOT_VOTING = "present_not_voting"
    DID_NOT_VOTE = "did_not_vote"
    NOT_PARTICIPATING = "not_participating"
    MISSION = "mission"
    LEAVE = "leave"
    LEAVE_OR_MISSION = "leave_or_mission"
    REQUESTER_NOT_VOTING = "requester_not_voting"
    PRESIDING = "presiding"
    NOT_IN_OFFICE = "not_in_office"
    SECRET_PARTICIPATION = "secret_participation"
    ABSENT_EXPLICIT = "absent_explicit"
    NOT_RECORDED = "not_recorded"
    UNKNOWN = "unknown"


class NormalizationStatus(StrEnum):
    NORMALIZED = "normalized"
    MISSING = "missing"
    UNMAPPED = "unmapped"


class FactLayer(StrEnum):
    SOURCE = "source"
    NORMALIZED = "normalized"
    DERIVED = "derived"
    INTERPRETED = "interpreted"


class CrosswalkStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ParliamentaryItemType(StrEnum):
    BILL = "bill"
    LAW = "law"
    DECREE_LAW = "decree_law"
    ARTICLE = "article"
    MOTION = "motion"
    RESOLUTION = "resolution"
    AMENDMENT = "amendment"
    AGENDA_ITEM = "agenda_item"
    OTHER = "other"


class ItemRelationType(StrEnum):
    AMENDS = "amends"
    IMPLEMENTS = "implements"
    REFERS_TO = "refers_to"
    SUPERSEDES = "supersedes"
    VERSION_OF = "version_of"


class ReleaseStatus(StrEnum):
    BUILDING = "building"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"
