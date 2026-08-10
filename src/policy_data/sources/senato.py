from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from policy_data.domain.enums import VotePosition
from policy_data.domain.ids import (
    canonical_person_id,
    mandate_id,
    roll_call_id,
    source_identity_id,
)
from policy_data.sources.senato_mapping import POSITION_MAP, predicate_local_name


class SenatoQuarantine(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SenatoArtifactSet:
    vote_windows: tuple[bytes, ...]
    people_json: bytes
    groups_json: bytes
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class SenatoPerson:
    person_id: str
    identity_id: str
    source_uri: str
    display_name: str


@dataclass(frozen=True, slots=True)
class SenatoMandate:
    mandate_id: str
    person_id: str
    source_uri: str
    started_on: date
    ended_on: date | None


@dataclass(frozen=True, slots=True)
class SenatoGroupMembership:
    membership_id: str
    person_id: str
    group_id: str
    group_name: str
    started_on: date
    ended_on: date | None
    role: str | None


@dataclass(frozen=True, slots=True)
class GroupResolution:
    group_id: str | None
    group_name: str | None
    diagnostic: str | None


@dataclass(frozen=True, slots=True)
class SenatoDisclosure:
    person_id: str
    year: int
    official_url: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class SenatoRollCall:
    roll_call_id: str
    source_uri: str
    source_vote_id: str
    sitting_uri: str
    sitting_number: int
    occurred_on: date
    number: int
    title: str
    vote_type: str
    result: str
    totals: dict[str, int]
    object_uri: str | None


@dataclass(frozen=True, slots=True)
class SenatoMemberVote:
    roll_call_id: str
    mandate_id: str
    raw_position: str
    position: VotePosition
    group_id_at_vote: str | None
    group_name_at_vote: str | None


@dataclass(frozen=True, slots=True)
class SenatoAdapterResult:
    people: tuple[SenatoPerson, ...]
    mandates: tuple[SenatoMandate, ...]
    memberships: tuple[SenatoGroupMembership, ...]
    roll_calls: tuple[SenatoRollCall, ...]
    member_votes: tuple[SenatoMemberVote, ...]
    disclosures: tuple[SenatoDisclosure, ...]
    crosswalk_candidates: tuple[str, ...]
    quarantined: tuple[str, ...]


Binding = dict[str, dict[str, str]]


def map_senato_position(predicate: str) -> VotePosition:
    local_name = predicate_local_name(predicate)
    try:
        return POSITION_MAP[local_name]
    except KeyError as error:
        raise SenatoQuarantine(f"unmapped Senato position: {predicate!r}") from error


def resolve_group_at_vote(
    memberships: tuple[SenatoGroupMembership, ...], person_id: str, occurred_on: date
) -> GroupResolution:
    active = [
        membership
        for membership in memberships
        if membership.person_id == person_id
        and membership.started_on <= occurred_on
        and (membership.ended_on is None or occurred_on <= membership.ended_on)
    ]
    if not active:
        return GroupResolution(None, None, "no active group membership")
    if len(active) > 1:
        return GroupResolution(None, None, "ambiguous active group memberships")
    return GroupResolution(active[0].group_id, active[0].group_name, None)


def _bindings(body: bytes) -> list[Binding]:
    try:
        payload: Any = json.loads(body)
        rows = payload["results"]["bindings"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise SenatoQuarantine("invalid Senato SPARQL JSON artifact") from error
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise SenatoQuarantine("Senato SPARQL bindings must be a list of objects")
    return rows


def _optional_value(row: Binding, key: str) -> str | None:
    cell = row.get(key)
    return cell.get("value") if isinstance(cell, dict) else None


def _required_value(row: Binding, key: str) -> str:
    value = _optional_value(row, key)
    if value is None:
        raise SenatoQuarantine(f"Senato binding is missing required {key}")
    return value


def _optional_date(row: Binding, key: str) -> date | None:
    raw = _optional_value(row, key)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise SenatoQuarantine(f"Senato {key} must be an ISO date") from error


def _required_date(row: Binding, key: str) -> date:
    raw = _required_value(row, key)
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise SenatoQuarantine(f"Senato {key} must be an ISO date") from error


def _integer(row: Binding, key: str) -> int:
    raw = _required_value(row, key)
    try:
        return int(raw)
    except ValueError as error:
        raise SenatoQuarantine(f"Senato {key} must be an integer") from error


class SenatoAdapter:
    legislature = 19
    chamber = "senato"

    def normalize(self, artifacts: SenatoArtifactSet) -> SenatoAdapterResult:
        if artifacts.observed_at.tzinfo is None:
            raise SenatoQuarantine("observed_at must be timezone-aware")
        quarantined: list[str] = []
        people: list[SenatoPerson] = []
        mandates: list[SenatoMandate] = []
        disclosures: list[SenatoDisclosure] = []
        crosswalks: list[str] = []
        person_by_uri: dict[str, SenatoPerson] = {}
        mandate_by_person: dict[str, SenatoMandate] = {}

        for row in _bindings(artifacts.people_json):
            try:
                senator_uri = _required_value(row, "senator")
                source_key = senator_uri.rsplit("/", 1)[-1]
                person = SenatoPerson(
                    canonical_person_id("senato", source_key),
                    source_identity_id("senato", source_key),
                    senator_uri,
                    f"{_required_value(row, 'last_name')} {_required_value(row, 'first_name')}",
                )
                mandate_uri = _required_value(row, "mandate")
                started_on = _required_date(row, "start")
                mandate = SenatoMandate(
                    mandate_id(person.person_id, self.legislature, self.chamber),
                    person.person_id,
                    mandate_uri,
                    started_on,
                    _optional_date(row, "end"),
                )
                disclosure_url = _optional_value(row, "disclosure_url")
                disclosure_year = _optional_value(row, "disclosure_year")
                if (disclosure_url is None) != (disclosure_year is None):
                    raise SenatoQuarantine(
                        "disclosure URL and year must occur together"
                    )
                if disclosure_url and disclosure_year:
                    disclosures.append(
                        SenatoDisclosure(
                            person.person_id,
                            int(disclosure_year),
                            disclosure_url,
                            artifacts.observed_at,
                        )
                    )
                same_as = _optional_value(row, "same_as")
                if same_as:
                    crosswalks.append(same_as)
            except (SenatoQuarantine, ValueError) as error:
                quarantined.append(f"person: {error}")
                continue
            people.append(person)
            mandates.append(mandate)
            person_by_uri[senator_uri] = person
            mandate_by_person[person.person_id] = mandate

        memberships: list[SenatoGroupMembership] = []
        for row in _bindings(artifacts.groups_json):
            try:
                senator_uri = _required_value(row, "senator")
                membership_person = person_by_uri.get(senator_uri)
                if membership_person is None:
                    raise SenatoQuarantine(f"unknown senator {senator_uri}")
                start = _required_date(row, "start")
                membership_uri = _required_value(row, "membership")
                group_uri = _required_value(row, "group")
                group_name = _required_value(row, "group_name")
                memberships.append(
                    SenatoGroupMembership(
                        membership_uri,
                        membership_person.person_id,
                        group_uri,
                        group_name,
                        start,
                        _optional_date(row, "end"),
                        _optional_value(row, "role"),
                    )
                )
            except SenatoQuarantine as error:
                quarantined.append(f"membership: {error}")

        memberships_by_person: dict[str, list[SenatoGroupMembership]] = {}
        for membership in memberships:
            memberships_by_person.setdefault(membership.person_id, []).append(
                membership
            )

        rows_by_vote: dict[str, dict[str, Binding]] = {}
        for window in artifacts.vote_windows:
            for row in _bindings(window):
                vote_uri = _required_value(row, "vote")
                senator_uri = _required_value(row, "senator")
                rows_by_vote.setdefault(vote_uri, {})[senator_uri] = row

        roll_calls: list[SenatoRollCall] = []
        member_votes: list[SenatoMemberVote] = []
        for vote_uri, rows_by_senator in sorted(rows_by_vote.items()):
            rows = list(rows_by_senator.values())
            representative = rows[-1]
            try:
                source_vote_id = vote_uri.rsplit("/", 1)[-1]
                stable_roll_id = roll_call_id(
                    self.legislature, self.chamber, source_vote_id
                )
                occurred_on = _required_date(representative, "date")
                normalized_roll_call = SenatoRollCall(
                    stable_roll_id,
                    vote_uri,
                    source_vote_id,
                    _required_value(representative, "sitting"),
                    _integer(representative, "sitting_number"),
                    occurred_on,
                    _integer(representative, "number"),
                    _required_value(representative, "title"),
                    _required_value(representative, "vote_type"),
                    _required_value(representative, "result"),
                    {
                        key: _integer(representative, key)
                        for key in (
                            "present",
                            "voting",
                            "yes",
                            "no",
                            "abstain",
                            "legal_number",
                            "majority",
                        )
                    },
                    _optional_value(representative, "object"),
                )
                normalized_member_votes: list[SenatoMemberVote] = []
                group_diagnostics: list[str] = []
                for row in rows:
                    senator_uri = _required_value(row, "senator")
                    vote_person = person_by_uri.get(senator_uri)
                    if vote_person is None:
                        raise SenatoQuarantine(
                            f"vote references unknown senator {senator_uri}"
                        )
                    vote_mandate = mandate_by_person.get(vote_person.person_id)
                    if vote_mandate is None:
                        raise SenatoQuarantine(
                            f"vote senator {senator_uri} has no mandate"
                        )
                    raw_position = _required_value(row, "position_predicate")
                    group_resolution = resolve_group_at_vote(
                        tuple(memberships_by_person.get(vote_person.person_id, ())),
                        vote_person.person_id,
                        occurred_on,
                    )
                    if group_resolution.diagnostic is not None:
                        group_diagnostics.append(
                            f"{source_vote_id}/{senator_uri}: {group_resolution.diagnostic}"
                        )
                    normalized_member_votes.append(
                        SenatoMemberVote(
                            stable_roll_id,
                            vote_mandate.mandate_id,
                            raw_position,
                            map_senato_position(raw_position),
                            group_resolution.group_id,
                            group_resolution.group_name,
                        )
                    )
                roll_calls.append(normalized_roll_call)
                member_votes.extend(normalized_member_votes)
                quarantined.extend(group_diagnostics)
            except SenatoQuarantine as error:
                quarantined.append(f"vote {vote_uri}: {error}")

        return SenatoAdapterResult(
            tuple(people),
            tuple(mandates),
            tuple(memberships),
            tuple(roll_calls),
            tuple(member_votes),
            tuple(disclosures),
            tuple(crosswalks),
            tuple(quarantined),
        )
