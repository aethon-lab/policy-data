from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid5

from policy_data.ingest.exports import ExportDataset
from policy_data.ingest.pipeline import (
    ReleaseBuildResult,
    ReleaseBuilder,
    ReleaseInput,
    SourceSnapshot,
)
from policy_data.sources.archive import read_safe_zip
from policy_data.sources.artifacts import StoredArtifact
from policy_data.sources.camera import (
    CameraAdapter,
    CameraAdapterResult,
    CameraArtifactSet,
)
from policy_data.sources.http import SafeFetcher
from policy_data.sources.registry import SourceDefinition, SourceRegistry
from policy_data.sources.senato import (
    SenatoAdapter,
    SenatoAdapterResult,
    SenatoArtifactSet,
)


class SourceAssemblyError(ValueError):
    pass


class _SourcePerson(Protocol):
    @property
    def person_id(self) -> str: ...

    @property
    def identity_id(self) -> str: ...

    @property
    def source_uri(self) -> str: ...

    @property
    def display_name(self) -> str: ...


_ORCHESTRATION_NAMESPACE = UUID("430c97ec-4af5-4645-ad01-c649998277eb")
_REQUIRED_ROLES = {
    "camera_votes_rdf",
    "camera_deputies_rdf",
    "camera_mandates_rdf",
    "camera_groups_rdf",
    "senato_vote_window",
    "senato_people_json",
    "senato_groups_json",
}


def _id(kind: str, *parts: object) -> str:
    value = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return f"{kind}:{uuid5(_ORCHESTRATION_NAMESPACE, f'{kind}:{value}')}"


@dataclass(frozen=True, slots=True)
class AcquiredSource:
    definition: SourceDefinition
    artifact: StoredArtifact
    body: bytes
    observed_at: datetime
    media_type: str


class OfficialRefresh:
    """Acquire, normalize, validate, and atomically activate one official release."""

    def __init__(
        self,
        registry: SourceRegistry,
        fetcher: SafeFetcher,
        builder: ReleaseBuilder,
        *,
        now: datetime | None = None,
    ) -> None:
        self.registry = registry
        self.fetcher = fetcher
        self.builder = builder
        self.now = now or datetime.now(UTC)
        if self.now.tzinfo is None:
            raise ValueError("refresh time must be timezone-aware")

    def run(self, *, use_cached: bool = False) -> ReleaseBuildResult:
        configured_roles = {
            source.role for source in self.registry.all() if source.role is not None
        }
        missing = sorted(_REQUIRED_ROLES - configured_roles)
        if missing:
            raise SourceAssemblyError(
                "official source configuration is incomplete; missing roles: "
                + ", ".join(missing)
            )
        acquire = self._acquire_cached if use_cached else self._acquire
        acquired = tuple(acquire(source) for source in self.registry.all())
        release = self.assemble(acquired)
        return self.builder.build(release)

    def _acquire(self, source: SourceDefinition) -> AcquiredSource:
        if source.role is None:
            raise SourceAssemblyError(
                f"source {source.source_id} has no orchestration role"
            )
        artifact = self.fetcher.fetch(source)
        return self._materialize(source, artifact)

    def _acquire_cached(self, source: SourceDefinition) -> AcquiredSource:
        artifact = self.fetcher.store.latest(source.source_id)
        if artifact is None:
            raise SourceAssemblyError(
                f"source {source.source_id} has no verified cached artifact"
            )
        return self._materialize(source, artifact, require_current_definition=True)

    def _materialize(
        self,
        source: SourceDefinition,
        artifact: StoredArtifact,
        *,
        require_current_definition: bool = False,
    ) -> AcquiredSource:
        metadata = json.loads(artifact.metadata_path.read_text(encoding="utf-8"))
        if require_current_definition and (
            metadata.get("source_id") != source.source_id
            or metadata.get("source_url") != source.request_url
            or metadata.get("adapter_version") != source.adapter_version
        ):
            raise SourceAssemblyError(
                f"source {source.source_id} cached artifact does not match the registry"
            )
        body = self._bounded_body(source, artifact.path)
        observed_at = datetime.fromisoformat(metadata["observed_at"])
        return AcquiredSource(
            source, artifact, body, observed_at, str(metadata["media_type"])
        )

    @staticmethod
    def _bounded_body(source: SourceDefinition, path: Path) -> bytes:
        if path.stat().st_size > source.max_bytes:
            raise SourceAssemblyError(
                f"source {source.source_id} exceeds its assembly byte bound"
            )
        body = path.read_bytes()
        if body.startswith(b"PK\x03\x04"):
            entries = read_safe_zip(
                body,
                max_entries=16,
                max_expanded_bytes=min(source.max_bytes * 4, 1024 * 1024 * 1024),
            )
            if source.archive_member is not None:
                try:
                    return entries[source.archive_member]
                except KeyError as error:
                    raise SourceAssemblyError(
                        f"source {source.source_id} archive misses configured member "
                        f"{source.archive_member!r}"
                    ) from error
            if len(entries) != 1:
                raise SourceAssemblyError(
                    f"source {source.source_id} archive has {len(entries)} members; "
                    "configure archive_member"
                )
            return next(iter(entries.values()))
        return body

    def assemble(self, acquired: tuple[AcquiredSource, ...]) -> ReleaseInput:
        by_role: dict[str, list[AcquiredSource]] = defaultdict(list)
        for item in acquired:
            if item.definition.role is not None:
                by_role[item.definition.role].append(item)
        missing = sorted(role for role in _REQUIRED_ROLES if not by_role[role])
        if missing:
            raise SourceAssemblyError(
                "official source configuration is incomplete; missing roles: "
                + ", ".join(missing)
            )
        duplicates = sorted(
            role
            for role in _REQUIRED_ROLES - {"camera_vote_detail", "senato_vote_window"}
            if len(by_role[role]) != 1
        )
        if duplicates:
            raise SourceAssemblyError(
                "official source configuration has duplicate singleton roles: "
                + ", ".join(duplicates)
            )

        camera_details = {
            item.definition.url: item.body.decode("utf-8")
            for item in by_role.get("camera_vote_detail", [])
        }
        camera = CameraAdapter().normalize(
            CameraArtifactSet(
                votes_rdf=by_role["camera_votes_rdf"][0].body,
                deputies_rdf=by_role["camera_deputies_rdf"][0].body,
                mandates_rdf=by_role["camera_mandates_rdf"][0].body,
                groups_rdf=by_role["camera_groups_rdf"][0].body,
                detail_html=camera_details,
            )
        )
        senato = SenatoAdapter().normalize(
            SenatoArtifactSet(
                vote_windows=tuple(item.body for item in by_role["senato_vote_window"]),
                people_json=by_role["senato_people_json"][0].body,
                groups_json=by_role["senato_groups_json"][0].body,
                observed_at=self.now,
            )
        )
        hard_quarantine = [
            message
            for message in (*camera.quarantined, *senato.quarantined)
            if "group membership" not in message
            and not message.endswith("missing verified detail artifact")
            and "normalized positions disagree with official totals" not in message
        ]
        if hard_quarantine:
            raise SourceAssemblyError(
                "adapter quarantine prevents release: "
                + "; ".join(hard_quarantine[:20])
            )
        return self._canonical_release(acquired, by_role, camera, senato)

    def _canonical_release(
        self,
        acquired: tuple[AcquiredSource, ...],
        by_role: dict[str, list[AcquiredSource]],
        camera: CameraAdapterResult,
        senato: SenatoAdapterResult,
    ) -> ReleaseInput:
        tables: dict[str, list[dict[str, Any]]] = defaultdict(list)
        source_records: list[dict[str, Any]] = []
        lineage: list[dict[str, Any]] = []

        role_source = {role: values[0] for role, values in by_role.items()}
        senato_metadata_sources: dict[str, AcquiredSource] = {}
        senato_member_sources: dict[tuple[str, str], AcquiredSource] = {}
        for source in by_role["senato_vote_window"]:
            payload = json.loads(source.body)
            for binding in payload.get("results", {}).get("bindings", []):
                vote = binding.get("vote", {}).get("value")
                if isinstance(vote, str):
                    source_vote_id = vote.rsplit("/", 1)[-1]
                    senator = binding.get("senator", {}).get("value")
                    if isinstance(senator, str):
                        senato_member_sources[(source_vote_id, senator)] = source
                    else:
                        senato_metadata_sources[source_vote_id] = source
                        for cell in binding.values():
                            value = (
                                cell.get("value") if isinstance(cell, dict) else None
                            )
                            if isinstance(value, str):
                                senato_metadata_sources.setdefault(value, source)
        senato_person_source = {
            person.person_id: person.source_uri for person in senato.people
        }
        senato_mandate_source = {
            mandate.mandate_id: senato_person_source[mandate.person_id]
            for mandate in senato.mandates
        }

        def fact(
            fact_type: str,
            fact_id: str,
            role: str,
            upstream_key: str,
            locator: str,
            rule: str,
        ) -> None:
            source = role_source[role]
            if role == "senato_vote_window":
                if fact_type == "vote" and ":mandate:" in upstream_key:
                    source_vote_id, mandate_suffix = upstream_key.split(":mandate:", 1)
                    senator_uri = senato_mandate_source.get(f"mandate:{mandate_suffix}")
                    if senator_uri is not None:
                        source = senato_member_sources.get(
                            (source_vote_id, senator_uri), source
                        )
                else:
                    source = senato_metadata_sources.get(upstream_key, source)
            record_id = _id("source_record", fact_type, fact_id)
            source_records.append(
                {
                    "source_record_id": record_id,
                    "artifact_id": f"sha256:{source.artifact.sha256}",
                    "upstream_key": upstream_key,
                    "record_locator": locator,
                    "raw_scope": fact_type,
                    "mapping_version": source.definition.adapter_version,
                }
            )
            lineage.append(
                {
                    "fact_type": fact_type,
                    "fact_id": fact_id,
                    "source_record_id": record_id,
                    "resolution_rule": rule,
                }
            )

        tables["source_authorities"].extend(
            (
                {
                    "authority_id": "camera",
                    "chamber_code": "camera",
                    "name": "Camera dei deputati",
                },
                {
                    "authority_id": "senato",
                    "chamber_code": "senato",
                    "name": "Senato della Repubblica",
                },
            )
        )

        def add_people(
            chamber: str,
            people: Sequence[_SourcePerson],
            role: str,
        ) -> None:
            for index, person in enumerate(people):
                tables["people"].append(
                    {"person_id": person.person_id, "display_name": person.display_name}
                )
                source_key = person.source_uri.rsplit("/", 1)[-1]
                tables["source_identities"].append(
                    {
                        "identity_id": person.identity_id,
                        "authority_id": chamber,
                        "source_person_id": source_key,
                        "display_name": person.display_name,
                        "canonical_person_id": person.person_id,
                        "same_as_uri": None,
                    }
                )
                fact(
                    "person",
                    person.person_id,
                    role,
                    person.source_uri,
                    f"people[{index}]",
                    "official source identity",
                )

        add_people("camera", camera.people, "camera_deputies_rdf")
        add_people("senato", senato.people, "senato_people_json")

        for index, camera_mandate in enumerate(camera.mandates):
            tables["mandates"].append(
                {
                    "mandate_id": camera_mandate.mandate_id,
                    "person_id": camera_mandate.person_id,
                    "legislature_number": 19,
                    "chamber_code": "camera",
                    "starts_on": None,
                    "ends_on": None,
                }
            )
            fact(
                "mandate",
                camera_mandate.mandate_id,
                "camera_mandates_rdf",
                camera_mandate.source_uri,
                f"mandates[{index}]",
                "official mandate",
            )
        for index, senato_mandate in enumerate(senato.mandates):
            tables["mandates"].append(
                {
                    "mandate_id": senato_mandate.mandate_id,
                    "person_id": senato_mandate.person_id,
                    "legislature_number": 19,
                    "chamber_code": "senato",
                    "starts_on": senato_mandate.started_on.isoformat(),
                    "ends_on": senato_mandate.ended_on.isoformat()
                    if senato_mandate.ended_on
                    else None,
                }
            )
            fact(
                "mandate",
                senato_mandate.mandate_id,
                "senato_people_json",
                senato_mandate.source_uri,
                f"mandates[{index}]",
                "official mandate",
            )

        for index, group in enumerate(camera.groups):
            tables["political_groups"].append(
                {
                    "group_id": group.group_id,
                    "legislature_number": 19,
                    "chamber_code": "camera",
                    "name": group.name,
                    "abbreviation": None,
                }
            )
            fact(
                "political_group",
                group.group_id,
                "camera_groups_rdf",
                group.source_uri,
                f"groups[{index}]",
                "official group",
            )
        senato_groups = {
            membership.group_id: membership.group_name
            for membership in senato.memberships
        }
        for index, (group_id, name) in enumerate(sorted(senato_groups.items())):
            tables["political_groups"].append(
                {
                    "group_id": group_id,
                    "legislature_number": 19,
                    "chamber_code": "senato",
                    "name": name,
                    "abbreviation": None,
                }
            )
            fact(
                "political_group",
                group_id,
                "senato_groups_json",
                group_id,
                f"groups[{index}]",
                "official group membership",
            )
        senato_mandates = {
            mandate.person_id: mandate.mandate_id for mandate in senato.mandates
        }
        for index, membership in enumerate(senato.memberships):
            tables["memberships"].append(
                {
                    "membership_id": membership.membership_id,
                    "mandate_id": senato_mandates[membership.person_id],
                    "group_id": membership.group_id,
                    "legislature_number": 19,
                    "chamber_code": "senato",
                    "starts_on": membership.started_on.isoformat(),
                    "ends_on": membership.ended_on.isoformat()
                    if membership.ended_on
                    else None,
                }
            )
            fact(
                "membership",
                membership.membership_id,
                "senato_groups_json",
                membership.membership_id,
                f"memberships[{index}]",
                "official dated membership",
            )

        camera_groups = {
            group.name.casefold(): group.group_id for group in camera.groups
        }
        votes_by_chamber: dict[str, list[dict[str, Any]]] = defaultdict(list)

        def add_roll(
            chamber: str,
            roll_id: str,
            source_vote_id: str,
            sitting_source: str,
            occurred_on: str,
            title: str,
            description: str | None,
            vote_type: str,
            result: str | None,
            official_url: str | None,
            item_url: str | None,
            totals: dict[str, int],
            coverage: str,
            item_source: str,
            role: str,
            index: int,
        ) -> tuple[str, str]:
            sitting_id = _id("sitting", chamber, sitting_source)
            item_id = _id("parliamentary_item", chamber, item_source)
            if not any(row["sitting_id"] == sitting_id for row in tables["sittings"]):
                tables["sittings"].append(
                    {
                        "sitting_id": sitting_id,
                        "legislature_number": 19,
                        "chamber_code": chamber,
                        "source_sitting_id": sitting_source,
                        "sitting_date": occurred_on,
                    }
                )
                fact(
                    "sitting",
                    sitting_id,
                    role,
                    sitting_source,
                    f"roll_calls[{index}]",
                    "derived from official roll call",
                )
            if not any(
                row["item_id"] == item_id for row in tables["parliamentary_items"]
            ):
                tables["parliamentary_items"].append(
                    {
                        "item_id": item_id,
                        "legislature_number": 19,
                        "chamber_code": chamber,
                        "item_type": vote_type,
                        "source_item_id": item_source,
                        "title": title,
                        "official_url": item_url,
                    }
                )
                fact(
                    "parliamentary_item",
                    item_id,
                    role,
                    item_source,
                    f"roll_calls[{index}]",
                    "official vote object",
                )
            tables["roll_calls"].append(
                {
                    "roll_call_id": roll_id,
                    "legislature_number": 19,
                    "chamber_code": chamber,
                    "sitting_id": sitting_id,
                    "primary_item_id": item_id,
                    "source_vote_id": source_vote_id,
                    "occurred_at": f"{occurred_on}T00:00:00Z",
                    "official_type": vote_type,
                    "official_title": title,
                    "official_description": description,
                    "official_result": result,
                    "official_url": official_url,
                    "present_count": totals.get("present"),
                    "voting_count": totals.get("voting"),
                    "yes_count": totals.get("yes"),
                    "no_count": totals.get("no"),
                    "abstain_count": totals.get("abstain"),
                    "majority_count": totals.get("majority"),
                    "legal_number_count": totals.get("legal_number"),
                    "positions_available": int(coverage == "complete"),
                    "position_coverage": coverage,
                }
            )
            tables["roll_call_items"].append(
                {
                    "roll_call_id": roll_id,
                    "item_id": item_id,
                    "legislature_number": 19,
                    "chamber_code": chamber,
                    "role": "primary",
                    "raw_predicate": (
                        "ocd:rif_attoCamera"
                        if chamber == "camera" and item_url
                        else "osr:oggetto"
                        if chamber == "senato" and item_url
                        else None
                    ),
                }
            )
            fact(
                "roll_call",
                roll_id,
                role,
                source_vote_id,
                f"roll_calls[{index}]",
                "official roll call",
            )
            return sitting_id, item_id

        camera_member_by_roll: dict[str, list[Any]] = defaultdict(list)
        for member in camera.member_votes:
            camera_member_by_roll[member.roll_call_id].append(member)
        for index, roll in enumerate(camera.roll_calls):
            occurred = datetime.strptime(roll.occurred_on, "%Y%m%d").date().isoformat()
            members = camera_member_by_roll[roll.roll_call_id]
            counts = Counter(member.position.value for member in members)
            add_roll(
                "camera",
                roll.roll_call_id,
                roll.source_vote_id,
                roll.source_vote_id[:-3],
                occurred,
                roll.title,
                roll.description,
                roll.official_type,
                None,
                roll.detail_url or roll.source_uri,
                roll.item_uri or roll.source_uri,
                roll.totals
                or {
                    "present": len(members),
                    "voting": counts["yes"] + counts["no"],
                    "yes": counts["yes"],
                    "no": counts["no"],
                    "abstain": counts["abstain"],
                },
                roll.position_coverage,
                roll.item_uri or roll.source_vote_id,
                "camera_votes_rdf",
                index,
            )
            for vote_index, member in enumerate(members):
                vote_id = _id("vote", roll.roll_call_id, member.mandate_id)
                row = {
                    "vote_id": vote_id,
                    "roll_call_id": roll.roll_call_id,
                    "mandate_id": member.mandate_id,
                    "legislature_number": 19,
                    "chamber_code": "camera",
                    "raw_position": member.raw_position,
                    "normalized_position": member.position.value,
                    "normalization_status": "normalized",
                    "group_id_at_vote": camera_groups.get(
                        member.group_label.casefold()
                    ),
                }
                tables["votes"].append(row)
                votes_by_chamber["camera"].append(
                    {
                        "vote_id": vote_id,
                        "roll_call_id": roll.roll_call_id,
                        "title": roll.title,
                        "position": member.position.value,
                    }
                )
                fact(
                    "vote",
                    vote_id,
                    "camera_votes_rdf",
                    f"{roll.source_vote_id}:{member.mandate_id}",
                    f"member_votes[{vote_index}]",
                    "official RDF position, detail-reconciled when supplied",
                )

        senato_member_by_roll: dict[str, list[Any]] = defaultdict(list)
        for senato_member in senato.member_votes:
            senato_member_by_roll[senato_member.roll_call_id].append(senato_member)
        for index, senato_roll in enumerate(senato.roll_calls):
            item_source = senato_roll.object_uri or senato_roll.source_vote_id
            add_roll(
                "senato",
                senato_roll.roll_call_id,
                senato_roll.source_vote_id,
                senato_roll.sitting_uri,
                senato_roll.occurred_on.isoformat(),
                senato_roll.title,
                None,
                senato_roll.vote_type,
                senato_roll.result,
                senato_roll.source_uri,
                senato_roll.object_uri or senato_roll.source_uri,
                senato_roll.totals,
                senato_roll.position_coverage,
                item_source,
                "senato_vote_window",
                index,
            )
            for vote_index, senato_member in enumerate(
                senato_member_by_roll[senato_roll.roll_call_id]
            ):
                vote_id = _id(
                    "vote", senato_roll.roll_call_id, senato_member.mandate_id
                )
                row = {
                    "vote_id": vote_id,
                    "roll_call_id": senato_roll.roll_call_id,
                    "mandate_id": senato_member.mandate_id,
                    "legislature_number": 19,
                    "chamber_code": "senato",
                    "raw_position": senato_member.raw_position,
                    "normalized_position": senato_member.position.value,
                    "normalization_status": "normalized",
                    "group_id_at_vote": senato_member.group_id_at_vote,
                }
                tables["votes"].append(row)
                votes_by_chamber["senato"].append(
                    {
                        "vote_id": vote_id,
                        "roll_call_id": senato_roll.roll_call_id,
                        "title": senato_roll.title,
                        "position": senato_member.position.value,
                    }
                )
                fact(
                    "vote",
                    vote_id,
                    "senato_vote_window",
                    f"{senato_roll.source_vote_id}:{senato_member.mandate_id}",
                    f"member_votes[{vote_index}]",
                    "mapped official vote predicate",
                )

        for index, disclosure in enumerate(senato.disclosures):
            disclosure_id = _id(
                "disclosure",
                disclosure.person_id,
                disclosure.year,
                disclosure.official_url,
            )
            tables["disclosure_documents"].append(
                {
                    "disclosure_id": disclosure_id,
                    "mandate_id": senato_mandates[disclosure.person_id],
                    "official_label": str(disclosure.year),
                    "official_url": disclosure.official_url,
                    "observed_at": disclosure.observed_at.isoformat(),
                }
            )
            fact(
                "disclosure_document",
                disclosure_id,
                "senato_people_json",
                disclosure.official_url,
                f"disclosures[{index}]",
                "official disclosure link",
            )

        tables["source_records"] = source_records
        tables["fact_lineage"] = lineage
        snapshots = tuple(
            SourceSnapshot(
                dataset_id=item.definition.source_id,
                publisher=item.definition.publisher,
                license_id=item.definition.license_id,
                canonical_url=item.definition.request_url,
                artifact_id=f"sha256:{item.artifact.sha256}",
                sha256=item.artifact.sha256,
                observed_at=item.observed_at,
                media_type=item.media_type,
                byte_count=item.artifact.path.stat().st_size,
                adapter_version=item.definition.adapter_version,
            )
            for item in acquired
        )
        dates = [roll.occurred_on for roll in senato.roll_calls] + [
            datetime.strptime(roll.occurred_on, "%Y%m%d").date()
            for roll in camera.roll_calls
        ]
        exports = tuple(
            ExportDataset(
                chamber,
                role_source[f"{chamber}_votes_rdf"].definition.source_id
                if chamber == "camera"
                else role_source["senato_vote_window"].definition.source_id,
                role_source[
                    "camera_votes_rdf" if chamber == "camera" else "senato_vote_window"
                ].definition.publisher,
                role_source[
                    "camera_votes_rdf" if chamber == "camera" else "senato_vote_window"
                ].definition.license_id,
                tuple(rows),
            )
            for chamber, rows in sorted(votes_by_chamber.items())
        )
        return ReleaseInput(
            data_through=max(dates, default=date.min).isoformat(),
            created_at=self.now,
            sources=snapshots,
            tables={name: tuple(rows) for name, rows in tables.items()},
            exports=exports,
        )
