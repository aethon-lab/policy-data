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
) -> tuple[str | None, str]:
    active = [
        membership
        for membership in memberships
        if membership.person_id == person_id
        and membership.started_on <= occurred_on
        and (membership.ended_on is None or occurred_on <= membership.ended_on)
    ]
    if not active:
        return None, "no active group membership"
    if len(active) > 1:
        return None, "ambiguous active group memberships"
    return active[0].group_id, active[0].group_name


def _bindings(body: bytes) -> list[Binding]:
    try:
        payload: Any = json.loads(body)
        rows = payload["results"]["bindings"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise SenatoQuarantine("invalid Senato SPARQL JSON artifact") from error
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise SenatoQuarantine("Senato SPARQL bindings must be a list of objects")
    return rows


def _value(row: Binding, key: str, *, required: bool = True) -> str | None:
    cell = row.get(key)
    value = cell.get("value") if isinstance(cell, dict) else None
    if value is None and required:
        raise SenatoQuarantine(f"Senato binding is missing required {key}")
    return value


def _date(row: Binding, key: str, *, required: bool = True) -> date | None:
    raw = _value(row, key, required=required)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise SenatoQuarantine(f"Senato {key} must be an ISO date") from error


def _integer(row: Binding, key: str) -> int:
    raw = _value(row, key)
    try:
        return int(raw) if raw is not None else 0
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
                senator_uri = _value(row, "senator")
                assert senator_uri is not None
                source_key = senator_uri.rsplit("/", 1)[-1]
                person = SenatoPerson(
                    canonical_person_id("senato", source_key),
                    source_identity_id("senato", source_key),
                    senator_uri,
                    f"{_value(row, 'last_name')} {_value(row, 'first_name')}",
                )
                mandate_uri = _value(row, "mandate")
                started_on = _date(row, "start")
                assert mandate_uri is not None and started_on is not None
                mandate = SenatoMandate(
                    mandate_id(person.person_id, self.legislature, self.chamber),
                    person.person_id,
                    mandate_uri,
                    started_on,
                    _date(row, "end", required=False),
                )
                disclosure_url = _value(row, "disclosure_url", required=False)
                disclosure_year = _value(row, "disclosure_year", required=False)
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
                same_as = _value(row, "same_as", required=False)
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
                senator_uri = _value(row, "senator")
                membership_person = person_by_uri.get(senator_uri or "")
                if membership_person is None:
                    raise SenatoQuarantine(f"unknown senator {senator_uri}")
                start = _date(row, "start")
                membership_uri = _value(row, "membership")
                group_uri = _value(row, "group")
                group_name = _value(row, "group_name")
                assert start is not None
                assert membership_uri and group_uri and group_name
                memberships.append(
                    SenatoGroupMembership(
                        membership_uri,
                        membership_person.person_id,
                        group_uri,
                        group_name,
                        start,
                        _date(row, "end", required=False),
                        _value(row, "role", required=False),
                    )
                )
            except SenatoQuarantine as error:
                quarantined.append(f"membership: {error}")

        vote_rows: dict[tuple[str, str], Binding] = {}
        for window in artifacts.vote_windows:
            for row in _bindings(window):
                vote_uri = _value(row, "vote")
                senator_uri = _value(row, "senator")
                assert vote_uri and senator_uri
                vote_rows[(vote_uri, senator_uri)] = row

        roll_calls: list[SenatoRollCall] = []
        member_votes: list[SenatoMemberVote] = []
        rows_by_vote: dict[str, list[Binding]] = {}
        for (vote_uri, _), row in vote_rows.items():
            rows_by_vote.setdefault(vote_uri, []).append(row)
        for vote_uri, rows in sorted(rows_by_vote.items()):
            representative = rows[-1]
            try:
                source_vote_id = vote_uri.rsplit("/", 1)[-1]
                stable_roll_id = roll_call_id(
                    self.legislature, self.chamber, source_vote_id
                )
                occurred_on = _date(representative, "date")
                sitting_uri = _value(representative, "sitting")
                title = _value(representative, "title")
                vote_type = _value(representative, "vote_type")
                result = _value(representative, "result")
                assert occurred_on and sitting_uri and title and vote_type and result
                roll_calls.append(
                    SenatoRollCall(
                        stable_roll_id,
                        vote_uri,
                        source_vote_id,
                        sitting_uri,
                        _integer(representative, "sitting_number"),
                        occurred_on,
                        _integer(representative, "number"),
                        title,
                        vote_type,
                        result,
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
                        _value(representative, "object", required=False),
                    )
                )
                for row in rows:
                    senator_uri = _value(row, "senator")
                    vote_person = person_by_uri.get(senator_uri or "")
                    if vote_person is None:
                        raise SenatoQuarantine(
                            f"vote references unknown senator {senator_uri}"
                        )
                    vote_mandate = mandate_by_person.get(vote_person.person_id)
                    if vote_mandate is None:
                        raise SenatoQuarantine(
                            f"vote senator {senator_uri} has no mandate"
                        )
                    raw_position = _value(row, "position_predicate")
                    assert raw_position is not None
                    group_id, group_description = resolve_group_at_vote(
                        tuple(memberships), vote_person.person_id, occurred_on
                    )
                    group_name = group_description if group_id is not None else None
                    if group_id is None:
                        quarantined.append(
                            f"{source_vote_id}/{senator_uri}: {group_description}"
                        )
                    member_votes.append(
                        SenatoMemberVote(
                            stable_roll_id,
                            vote_mandate.mandate_id,
                            raw_position,
                            map_senato_position(raw_position),
                            group_id,
                            group_name,
                        )
                    )
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
