from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DC, FOAF, RDF, RDFS, XSD

from policy_data.domain.enums import VotePosition
from policy_data.domain.ids import (
    canonical_person_id,
    mandate_id,
    roll_call_id,
    source_identity_id,
)
from policy_data.sources.archive import reject_xml_dtd
from policy_data.sources.camera_mapping import POSITION_MAP, normalize_vote_type

OCD = Namespace("http://dati.camera.it/ocd/")


class CameraQuarantine(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedBoolean:
    value: bool
    raw_value: str
    raw_datatype: str


@dataclass(frozen=True, slots=True)
class CameraDetailRow:
    name: str
    group: str
    raw_position: str


@dataclass(frozen=True, slots=True)
class CameraVoteDetail:
    sitting_number: str
    vote_number: str
    title: str | None
    totals: dict[str, int]
    rows: tuple[CameraDetailRow, ...]


@dataclass(frozen=True, slots=True)
class CameraArtifactSet:
    votes_rdf: bytes
    deputies_rdf: bytes
    mandates_rdf: bytes
    groups_rdf: bytes
    detail_html: dict[str, str]


@dataclass(frozen=True, slots=True)
class CameraPerson:
    person_id: str
    identity_id: str
    source_uri: str
    display_name: str


@dataclass(frozen=True, slots=True)
class CameraMandate:
    mandate_id: str
    person_id: str
    source_uri: str


@dataclass(frozen=True, slots=True)
class CameraGroup:
    group_id: str
    source_uri: str
    name: str


@dataclass(frozen=True, slots=True)
class CameraRollCall:
    roll_call_id: str
    source_uri: str
    source_vote_id: str
    title: str
    description: str | None
    official_type: str
    occurred_on: str
    detail_url: str | None
    is_secret: bool
    position_coverage: str


@dataclass(frozen=True, slots=True)
class CameraMemberVote:
    roll_call_id: str
    mandate_id: str
    raw_position: str
    position: VotePosition
    group_label: str


@dataclass(frozen=True, slots=True)
class CameraAdapterResult:
    people: tuple[CameraPerson, ...]
    mandates: tuple[CameraMandate, ...]
    groups: tuple[CameraGroup, ...]
    roll_calls: tuple[CameraRollCall, ...]
    member_votes: tuple[CameraMemberVote, ...]
    quarantined: tuple[str, ...]


def map_camera_position(raw: str) -> VotePosition:
    try:
        return POSITION_MAP[raw.strip()]
    except KeyError as error:
        raise CameraQuarantine(f"unmapped Camera position: {raw!r}") from error


def parse_camera_boolean(value: Literal) -> ParsedBoolean:
    datatype = str(value.datatype or "")
    lexical = str(value).strip().casefold()
    if value.datatype == XSD.boolean and lexical in {"true", "false", "1", "0"}:
        return ParsedBoolean(lexical in {"true", "1"}, str(value), datatype)
    if value.datatype in {XSD.integer, XSD.int} and lexical in {"1", "0"}:
        return ParsedBoolean(lexical == "1", str(value), datatype)
    raise CameraQuarantine("Camera boolean must be xsd:boolean or integer 0/1")


class _DetailParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[tuple[str, str]] = []
        self.paragraphs: list[str] = []
        self.rows: list[list[str]] = []
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._row: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h1", "h2", "h3", "h4", "p", "td", "th"}:
            self._capture = tag
            self._buffer = []
        if tag == "tr":
            self._row = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture == tag:
            text = " ".join("".join(self._buffer).split())
            if tag.startswith("h") and text:
                self.headings.append((tag, text))
            elif tag == "p" and text:
                self.paragraphs.append(text)
            elif tag in {"td", "th"} and self._row is not None:
                self._row.append(text)
            self._capture = None
            self._buffer = []
        if tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def parse_vote_detail(html: str) -> CameraVoteDetail:
    if "checking your browser" in html.casefold() or "js-challenge-form" in html:
        raise CameraQuarantine("Camera browser challenge is not vote-detail data")
    parser = _DetailParser()
    parser.feed(html)
    heading_text = " ".join(text for _, text in parser.headings)
    vote_match = re.search(r"(?:nominale|segreta)\s+n\.\s*(\d+)", heading_text, re.I)
    sitting_match = re.search(r"seduta\s+n\.\s*(\d+)", heading_text, re.I)
    if not vote_match or not sitting_match:
        raise CameraQuarantine("Camera detail is missing vote or sitting identity")
    labels = {
        "PRESENTI": "present",
        "VOTANTI": "voting",
        "ASTENUTI": "abstain",
        "MAGGIORANZA": "majority",
        "FAVOREVOLI": "yes",
        "CONTRARI": "no",
    }
    totals: dict[str, int] = {}
    for index, value in enumerate(parser.paragraphs[:-1]):
        key = labels.get(value.upper())
        if key and parser.paragraphs[index + 1].isdigit():
            totals[key] = int(parser.paragraphs[index + 1])
    rows = []
    for cells in parser.rows:
        if len(cells) >= 3 and cells[:3] != ["Nominativo", "Gruppo", "Voto"]:
            rows.append(CameraDetailRow(cells[0], cells[1], cells[2]))
    title = next((text for tag, text in parser.headings if tag == "h4"), None)
    return CameraVoteDetail(
        sitting_number=sitting_match.group(1),
        vote_number=vote_match.group(1),
        title=title,
        totals=totals,
        rows=tuple(rows),
    )


def _graph(body: bytes) -> Graph:
    reject_xml_dtd(body)
    return Graph().parse(data=body, format="xml")


def _required_text(graph: Graph, subject: URIRef, predicate: URIRef, field: str) -> str:
    value = graph.value(subject, predicate)
    if value is None:
        raise CameraQuarantine(f"Camera record is missing required {field}")
    return str(value)


class CameraAdapter:
    legislature = 19
    chamber = "camera"

    def normalize(self, artifacts: CameraArtifactSet) -> CameraAdapterResult:
        votes_graph = _graph(artifacts.votes_rdf)
        deputies_graph = _graph(artifacts.deputies_rdf)
        mandates_graph = _graph(artifacts.mandates_rdf)
        groups_graph = _graph(artifacts.groups_rdf)
        quarantined: list[str] = []

        people: list[CameraPerson] = []
        names: dict[str, list[CameraPerson]] = {}
        for subject in sorted(deputies_graph.subjects(FOAF.surname), key=str):
            assert isinstance(subject, URIRef)
            surname = _required_text(deputies_graph, subject, FOAF.surname, "surname")
            first_name = _required_text(
                deputies_graph, subject, FOAF.firstName, "first name"
            )
            source_key = str(subject).rsplit("/", 1)[-1]
            person = CameraPerson(
                canonical_person_id("camera", source_key),
                source_identity_id("camera", source_key),
                str(subject),
                f"{surname} {first_name}",
            )
            people.append(person)
            names.setdefault(person.display_name.casefold(), []).append(person)

        people_by_uri = {person.source_uri: person for person in people}
        mandates: list[CameraMandate] = []
        mandate_by_person: dict[str, CameraMandate] = {}
        for subject, deputy in mandates_graph.subject_objects(OCD.rif_deputato):
            mandate_person = people_by_uri.get(str(deputy))
            if mandate_person is None:
                quarantined.append(
                    f"mandate {subject} references unknown deputy {deputy}"
                )
                continue
            record = CameraMandate(
                mandate_id(mandate_person.person_id, self.legislature, self.chamber),
                mandate_person.person_id,
                str(subject),
            )
            mandates.append(record)
            mandate_by_person[mandate_person.person_id] = record

        groups = tuple(
            CameraGroup(
                f"group:camera:19:{str(subject).rsplit('/', 1)[-1]}",
                str(subject),
                str(label),
            )
            for subject, label in groups_graph.subject_objects(RDFS.label)
        )

        rdf_positions: dict[tuple[str, str], str] = {}
        for vote_subject in votes_graph.subjects(RDF.type, OCD.voto):
            roll = votes_graph.value(vote_subject, OCD.rif_votazione)
            deputy_node = votes_graph.value(vote_subject, OCD.rif_deputato)
            raw = votes_graph.value(vote_subject, DC.type)
            if roll is None or deputy_node is None or raw is None:
                quarantined.append(
                    f"position {vote_subject} misses a required relationship"
                )
                continue
            rdf_positions[(str(roll), str(deputy_node))] = str(raw)

        roll_calls: list[CameraRollCall] = []
        member_votes: list[CameraMemberVote] = []
        for subject in sorted(votes_graph.subjects(RDF.type, OCD.votazione), key=str):
            assert isinstance(subject, URIRef)
            try:
                source_id = _required_text(
                    votes_graph, subject, DC.identifier, "identifier"
                )
                title = _required_text(votes_graph, subject, RDFS.label, "label")
                occurred = _required_text(votes_graph, subject, DC.date, "date")
                raw_type = str(votes_graph.value(subject, DC.type) or title)
                secret_literal = votes_graph.value(subject, OCD.segreta)
                is_secret = (
                    parse_camera_boolean(secret_literal).value
                    if isinstance(secret_literal, Literal)
                    else False
                )
            except CameraQuarantine as error:
                quarantined.append(f"{subject}: {error}")
                continue
            detail_value = votes_graph.value(subject, DC.relation)
            detail_url = str(detail_value) if detail_value is not None else None
            coverage = "secret" if is_secret else "unavailable"
            stable_roll_id = roll_call_id(self.legislature, self.chamber, source_id)
            if detail_url and not is_secret:
                detail_html = artifacts.detail_html.get(detail_url)
                if detail_html is None:
                    quarantined.append(f"{source_id}: missing verified detail artifact")
                    coverage = "partial"
                else:
                    try:
                        detail = parse_vote_detail(detail_html)
                        expected_identity = (
                            f"{detail.sitting_number}{int(detail.vote_number):03d}"
                        )
                        if expected_identity != source_id:
                            raise CameraQuarantine("RDF/detail vote identity disagrees")
                        normalized_detail_votes: list[CameraMemberVote] = []
                        for row in detail.rows:
                            candidates = names.get(row.name.casefold(), [])
                            if len(candidates) != 1:
                                raise CameraQuarantine(
                                    f"detail row {row.name!r} has {len(candidates)} deputy matches"
                                )
                            person = candidates[0]
                            mandate = mandate_by_person.get(person.person_id)
                            if mandate is None:
                                raise CameraQuarantine(
                                    f"detail row {row.name!r} has no active mandate"
                                )
                            position = map_camera_position(row.raw_position)
                            rdf_raw = rdf_positions.get(
                                (str(subject), person.source_uri)
                            )
                            if (
                                rdf_raw is not None
                                and map_camera_position(rdf_raw) is not position
                            ):
                                raise CameraQuarantine(
                                    f"RDF/detail position disagreement for {row.name}"
                                )
                            normalized_detail_votes.append(
                                CameraMemberVote(
                                    stable_roll_id,
                                    mandate.mandate_id,
                                    row.raw_position,
                                    position,
                                    row.group,
                                )
                            )
                        member_votes.extend(normalized_detail_votes)
                        coverage = "complete"
                    except CameraQuarantine as error:
                        quarantined.append(f"{source_id}: {error}")
                        coverage = "partial"
            description_value = votes_graph.value(subject, DC.description)
            roll_calls.append(
                CameraRollCall(
                    stable_roll_id,
                    str(subject),
                    source_id,
                    title,
                    str(description_value) if description_value is not None else None,
                    normalize_vote_type(raw_type),
                    occurred,
                    detail_url,
                    is_secret,
                    coverage,
                )
            )
        return CameraAdapterResult(
            tuple(people),
            tuple(mandates),
            groups,
            tuple(roll_calls),
            tuple(member_votes),
            tuple(quarantined),
        )
