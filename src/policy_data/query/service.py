from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from policy_data.domain.enums import ChamberCode, NormalizationStatus, VotePosition
from policy_data.ingest.publish import read_active_release
from policy_data.query.filters import VoteQuery
from policy_data.query.pagination import (
    CursorCodec,
    CursorState,
    InvalidCursor,
    filter_digest,
)
from policy_data.query.results import (
    CanonicalPage,
    CanonicalRecord,
    VoterPage,
    VoterResult,
)


class QueryTimeout(TimeoutError):
    pass


class NoActiveRelease(RuntimeError):
    pass


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


_PROFILE_POSITION_FIELDS = tuple(
    (position.value, position) for position in VotePosition
)


def _placeholders(values: tuple[object, ...]) -> str:
    return ", ".join("?" for _ in values)


class QueryService:
    def __init__(
        self,
        release_root: Path,
        *,
        cursor_secret: bytes,
        query_timeout_ms: int = 2_000,
        max_page_size: int = 100,
    ) -> None:
        self.release_root = release_root
        self.cursors = CursorCodec(cursor_secret)
        self.query_timeout_ms = query_timeout_ms
        self.max_page_size = max_page_size

    @contextmanager
    def _connection(self, release_id: str) -> Iterator[sqlite3.Connection]:
        if (
            not release_id.startswith("release-")
            or not release_id.replace("-", "").isalnum()
        ):
            raise InvalidCursor("cursor release identity is invalid")
        database = self.release_root / "releases" / release_id / "canonical.sqlite3"
        if not database.is_file() or database.is_symlink():
            raise InvalidCursor("cursor release is no longer available")
        connection = sqlite3.connect(
            f"file:{database}?mode=ro&immutable=1", uri=True, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        deadline = time.monotonic() + (self.query_timeout_ms / 1_000)
        connection.set_progress_handler(
            lambda: int(time.monotonic() >= deadline), 1_000
        )
        try:
            yield connection
        except sqlite3.OperationalError as error:
            if "interrupted" in str(error).casefold():
                raise QueryTimeout("canonical query exceeded its deadline") from error
            raise
        finally:
            connection.set_progress_handler(None, 0)
            connection.close()

    def find_voters(
        self,
        query: VoteQuery,
        *,
        limit: int = 25,
        cursor: str | None = None,
        release_id: str | None = None,
    ) -> VoterPage:
        if isinstance(limit, bool) or limit <= 0 or limit > self.max_page_size:
            raise ValueError(f"limit must be between 1 and {self.max_page_size}")
        digest = filter_digest(
            {
                key: value.value
                if isinstance(value, (ChamberCode, VotePosition))
                else value
                for key, value in asdict(query).items()
            }
        )
        state = self.cursors.decode(cursor) if cursor else None
        if state is not None and state.filter_digest != digest:
            raise InvalidCursor("cursor does not belong to these filters")
        if (
            state is not None
            and release_id is not None
            and state.release_id != release_id
        ):
            raise InvalidCursor("cursor does not belong to the requested release")
        release_id = (
            state.release_id
            if state
            else release_id or read_active_release(self.release_root)
        )
        if release_id is None:
            raise NoActiveRelease("no active canonical release")

        clauses = ["votes.normalization_status = 'normalized'"]
        parameters: list[object] = []
        if query.text:
            pattern = _like_pattern(query.text)
            clauses.append(
                "(items.title LIKE ? ESCAPE '\\' OR roll_calls.official_title LIKE ? ESCAPE '\\' OR roll_calls.official_description LIKE ? ESCAPE '\\')"
            )
            parameters.extend((pattern, pattern, pattern))
        if query.position is not None:
            clauses.append("votes.normalized_position = ?")
            parameters.append(query.position.value)
        if query.chamber is not None:
            clauses.append("votes.chamber_code = ?")
            parameters.append(query.chamber.value)
        if query.legislature is not None:
            clauses.append("votes.legislature_number = ?")
            parameters.append(query.legislature)
        if query.group_id is not None:
            clauses.append("votes.group_id_at_vote = ?")
            parameters.append(query.group_id)
        if query.person_id is not None:
            clauses.append("people.person_id = ?")
            parameters.append(query.person_id)
        if state is not None:
            clauses.append(
                "(roll_calls.occurred_at < ? OR (roll_calls.occurred_at = ? AND roll_calls.roll_call_id > ?) OR (roll_calls.occurred_at = ? AND roll_calls.roll_call_id = ? AND votes.vote_id > ?))"
            )
            parameters.extend(
                (
                    state.occurred_at,
                    state.occurred_at,
                    state.roll_call_id,
                    state.occurred_at,
                    state.roll_call_id,
                    state.vote_id,
                )
            )
        parameters.append(limit + 1)
        sql = f"""
            SELECT
                votes.vote_id, votes.roll_call_id, people.person_id,
                people.display_name AS person_name, votes.normalized_position,
                votes.raw_position, votes.group_id_at_vote,
                political_groups.name AS group_name_at_vote,
                votes.legislature_number, votes.chamber_code,
                roll_calls.occurred_at, roll_calls.official_type,
                roll_calls.official_result, items.item_id AS measure_id,
                items.title AS measure_title, items.official_url AS measure_url,
                roll_calls.official_url AS vote_url,
                (SELECT group_concat(DISTINCT datasets.publisher)
                   FROM fact_lineage AS lineage
                   JOIN source_records AS records USING(source_record_id)
                   JOIN source_artifacts AS artifacts USING(artifact_id)
                   JOIN source_datasets AS datasets USING(dataset_id)
                  WHERE lineage.fact_type = 'vote' AND lineage.fact_id = votes.vote_id
                ) AS publisher,
                (SELECT group_concat(DISTINCT datasets.license_id)
                   FROM fact_lineage AS lineage
                   JOIN source_records AS records USING(source_record_id)
                   JOIN source_artifacts AS artifacts USING(artifact_id)
                   JOIN source_datasets AS datasets USING(dataset_id)
                  WHERE lineage.fact_type = 'vote' AND lineage.fact_id = votes.vote_id
                ) AS license_id
            FROM votes
            JOIN roll_calls USING(roll_call_id, legislature_number, chamber_code)
            JOIN mandates USING(mandate_id, legislature_number, chamber_code)
            JOIN people USING(person_id)
            LEFT JOIN political_groups
              ON political_groups.group_id = votes.group_id_at_vote
             AND political_groups.legislature_number = votes.legislature_number
             AND political_groups.chamber_code = votes.chamber_code
            LEFT JOIN parliamentary_items AS items
              ON items.item_id = roll_calls.primary_item_id
             AND items.legislature_number = roll_calls.legislature_number
             AND items.chamber_code = roll_calls.chamber_code
            WHERE {" AND ".join(clauses)}
            ORDER BY roll_calls.occurred_at DESC, roll_calls.roll_call_id, votes.vote_id
            LIMIT ?
        """
        with self._connection(release_id) as connection:
            rows = connection.execute(sql, parameters).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = tuple(
            VoterResult(
                row["vote_id"],
                row["roll_call_id"],
                row["person_id"],
                row["person_name"],
                VotePosition(row["normalized_position"]),
                row["raw_position"],
                row["group_id_at_vote"],
                row["group_name_at_vote"],
                row["legislature_number"],
                ChamberCode(row["chamber_code"]),
                row["occurred_at"],
                row["official_type"],
                row["official_result"],
                row["measure_id"],
                row["measure_title"],
                row["measure_url"],
                row["vote_url"],
                row["publisher"],
                row["license_id"],
            )
            for row in rows
        )
        next_cursor = None
        if has_more:
            last = items[-1]
            next_cursor = self.cursors.encode(
                CursorState(
                    release_id,
                    digest,
                    last.occurred_at,
                    last.roll_call_id,
                    last.vote_id,
                )
            )
        return VoterPage(items, release_id, next_cursor, self._data_through(release_id))

    def dataset_status(self) -> CanonicalRecord:
        release_id = self._active_release()
        with self._connection(release_id) as connection:
            row = connection.execute(
                "SELECT release_id, schema_version, source_fingerprint, data_through, created_at FROM releases WHERE release_id = ?",
                (release_id,),
            ).fetchone()
            if row is None:
                raise NoActiveRelease("active release metadata is unavailable")
            counts = {
                table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("people", "political_groups", "roll_calls", "votes")
            }
        item = dict(row)
        item["counts"] = counts
        return CanonicalRecord(item, release_id, row["data_through"])

    def list_legislatures(
        self, *, limit: int = 25, cursor: str | None = None
    ) -> CanonicalPage:
        return self._collection(
            "legislatures",
            "number",
            "SELECT printf('%010d', number) AS record_id, number, roman_numeral FROM legislatures",
            (),
            limit=limit,
            cursor=cursor,
        )

    def list_people(
        self, *, text: str | None = None, limit: int = 25, cursor: str | None = None
    ) -> CanonicalPage:
        if text is not None and len(text) > 200:
            raise ValueError("query text cannot exceed 200 characters")
        where, params = (
            (" WHERE display_name LIKE ? ESCAPE '\\'", (_like_pattern(text),))
            if text
            else ("", ())
        )
        return self._collection(
            "people",
            "person_id",
            f"SELECT person_id AS record_id, person_id, display_name FROM people{where}",
            params,
            limit=limit,
            cursor=cursor,
            filters={"text": text},
        )

    def get_person(self, person_id: str) -> CanonicalRecord | None:
        position_columns = ",\n                           ".join(
            f"sum(CASE WHEN normalized_position = ? THEN 1 ELSE 0 END) AS {field}_count"
            for field, _ in _PROFILE_POSITION_FIELDS
        )
        return self._record(
            f"""WITH target_person AS (
                    SELECT person_id, display_name FROM people WHERE person_id = ?
                ), person_mandates AS (
                    SELECT m.* FROM mandates m JOIN target_person USING(person_id)
                ), person_votes AS MATERIALIZED (
                    SELECT v.* FROM votes v JOIN person_mandates USING(mandate_id)
                ), votes_by_mandate AS (
                    SELECT mandate_id,
                           count(*) AS recorded,
                           count(DISTINCT roll_call_id) AS recorded_roll_calls,
                           sum(CASE WHEN normalization_status = ? THEN 1 ELSE 0 END) AS normalized,
                           {position_columns},
                           sum(CASE WHEN normalized_position IS NULL THEN 1 ELSE 0 END) AS unmapped_count
                      FROM person_votes GROUP BY mandate_id
                )
                SELECT target_person.person_id, target_person.display_name,
               (SELECT json_group_array(json_object(
                    'authority_id', identity.authority_id,
                    'authority_name', identity.authority_name,
                    'chamber', identity.chamber_code,
                    'source_person_id', identity.source_person_id,
                    'source_display_name', identity.source_display_name,
                    'same_as_uri', identity.same_as_uri
                )) FROM (
                    SELECT i.authority_id, a.name AS authority_name, a.chamber_code,
                           i.source_person_id, i.display_name AS source_display_name,
                           i.same_as_uri
                      FROM source_identities i
                      JOIN source_authorities a USING(authority_id)
                      JOIN target_person ON target_person.person_id = i.canonical_person_id
                     ORDER BY a.chamber_code, i.authority_id, i.source_person_id
                ) identity) AS identities,
               (SELECT json_group_array(json_object(
                    'mandate_id', ordered_mandates.mandate_id,
                    'legislature', ordered_mandates.legislature_number,
                    'chamber', ordered_mandates.chamber_code,
                    'starts_on', ordered_mandates.starts_on,
                    'ends_on', ordered_mandates.ends_on
                )) FROM (
                    SELECT * FROM person_mandates
                     ORDER BY legislature_number DESC, chamber_code, starts_on
                ) ordered_mandates) AS mandates,
               (SELECT json_group_array(json_object(
                    'membership_id', membership.membership_id,
                    'mandate_id', membership.mandate_id,
                    'group_id', membership.group_id,
                    'group_name', membership.group_name,
                    'group_abbreviation', membership.group_abbreviation,
                    'legislature', membership.legislature_number,
                    'chamber', membership.chamber_code,
                    'starts_on', membership.starts_on,
                    'ends_on', membership.ends_on
                )) FROM (
                    SELECT ms.*, g.name AS group_name,
                           g.abbreviation AS group_abbreviation
                      FROM memberships ms
                      JOIN person_mandates USING(mandate_id)
                      JOIN political_groups g USING(group_id, legislature_number, chamber_code)
                     ORDER BY ms.starts_on DESC, ms.membership_id
                ) membership) AS memberships,
               (SELECT json_object(
                    'recorded', coalesce(sum(recorded), 0),
                    'normalized', coalesce(sum(normalized), 0),
                    {", ".join(f"'{field}', coalesce(sum({field}_count), 0)" for field, _ in _PROFILE_POSITION_FIELDS)},
                    'unmapped', coalesce(sum(unmapped_count), 0)
                ) FROM votes_by_mandate) AS vote_summary,
               (SELECT json_group_array(json_object(
                    'mandate_id', scope.mandate_id,
                    'legislature', scope.legislature_number,
                    'chamber', scope.chamber_code,
                    'published_roll_calls', scope.published_roll_calls,
                    'complete_roll_calls', scope.complete_roll_calls,
                    'partial_roll_calls', scope.partial_roll_calls,
                    'unavailable_roll_calls', scope.unavailable_roll_calls,
                    'secret_roll_calls', scope.secret_roll_calls,
                    'roll_calls_with_positions', scope.roll_calls_with_positions,
                    'recorded_person_votes', scope.recorded_person_votes,
                    'expected_person_votes', scope.complete_roll_calls,
                    'coverage_status', CASE
                        WHEN scope.published_roll_calls = 0 THEN 'not_applicable'
                        WHEN scope.partial_roll_calls > 0 THEN 'partial'
                        WHEN scope.unavailable_roll_calls > 0 THEN 'unavailable'
                        WHEN scope.complete_roll_calls = 0 AND scope.secret_roll_calls > 0 THEN 'secret'
                        WHEN scope.recorded_person_votes < scope.complete_roll_calls THEN 'partial'
                        ELSE 'complete'
                    END
                )) FROM (
                    SELECT m.mandate_id, m.legislature_number, m.chamber_code,
                           count(DISTINCT rc.roll_call_id) AS published_roll_calls,
                           count(DISTINCT CASE WHEN rc.position_coverage = 'complete' THEN rc.roll_call_id END) AS complete_roll_calls,
                           count(DISTINCT CASE WHEN rc.position_coverage = 'partial' THEN rc.roll_call_id END) AS partial_roll_calls,
                           count(DISTINCT CASE WHEN rc.position_coverage = 'unavailable' THEN rc.roll_call_id END) AS unavailable_roll_calls,
                           count(DISTINCT CASE WHEN rc.position_coverage = 'secret' THEN rc.roll_call_id END) AS secret_roll_calls,
                           count(DISTINCT CASE WHEN rc.positions_available = 1 THEN rc.roll_call_id END) AS roll_calls_with_positions,
                           coalesce(max(vbm.recorded_roll_calls), 0) AS recorded_person_votes
                      FROM person_mandates m
                      LEFT JOIN roll_calls rc
                        ON rc.legislature_number = m.legislature_number
                       AND rc.chamber_code = m.chamber_code
                       AND (m.starts_on IS NULL OR rc.occurred_at >= m.starts_on)
                       AND (m.ends_on IS NULL OR rc.occurred_at < date(m.ends_on, '+1 day'))
                      LEFT JOIN votes_by_mandate vbm USING(mandate_id)
                     GROUP BY m.mandate_id, m.legislature_number, m.chamber_code
                     ORDER BY m.legislature_number DESC, m.chamber_code, m.mandate_id
               ) scope) AS coverage,
               (SELECT json_group_array(json_object(
                    'disclosure_id', disclosure_id,
                    'official_label', official_label,
                    'official_url', official_url,
                    'observed_at', observed_at
                )) FROM (
                    SELECT d.* FROM person_mandates m
                    JOIN disclosure_documents d USING(mandate_id)
                    ORDER BY d.observed_at DESC, d.disclosure_id
                )) AS disclosures
               FROM target_person""",
            (
                person_id,
                NormalizationStatus.NORMALIZED.value,
                *(position.value for _, position in _PROFILE_POSITION_FIELDS),
            ),
            json_fields=(
                "identities",
                "mandates",
                "memberships",
                "vote_summary",
                "coverage",
                "disclosures",
            ),
        )

    def list_groups(
        self,
        *,
        legislature: int | None = None,
        chamber: ChamberCode | None = None,
        limit: int = 25,
        cursor: str | None = None,
    ) -> CanonicalPage:
        clauses: list[str] = []
        params: list[object] = []
        if legislature is not None:
            clauses.append("legislature_number = ?")
            params.append(legislature)
        if chamber is not None:
            clauses.append("chamber_code = ?")
            params.append(chamber.value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return self._collection(
            "groups",
            "group_id",
            f"SELECT group_id AS record_id, group_id, legislature_number AS legislature, chamber_code AS chamber, name, abbreviation FROM political_groups{where}",
            tuple(params),
            limit=limit,
            cursor=cursor,
            filters={
                "legislature": legislature,
                "chamber": chamber.value if chamber else None,
            },
        )

    def list_roll_calls(
        self,
        *,
        text: str | None = None,
        legislature: int | None = None,
        chamber: ChamberCode | None = None,
        limit: int = 25,
        cursor: str | None = None,
    ) -> CanonicalPage:
        if text is not None and len(text) > 200:
            raise ValueError("query text cannot exceed 200 characters")
        clauses: list[str] = []
        params: list[object] = []
        if text:
            clauses.append(
                "(official_title LIKE ? ESCAPE '\\' OR official_description LIKE ? ESCAPE '\\')"
            )
            params.extend((_like_pattern(text), _like_pattern(text)))
        if legislature is not None:
            clauses.append("legislature_number = ?")
            params.append(legislature)
        if chamber is not None:
            clauses.append("chamber_code = ?")
            params.append(chamber.value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return self._collection(
            "roll_calls",
            "roll_call_id",
            f"SELECT roll_call_id AS record_id, roll_call_id, legislature_number AS legislature, chamber_code AS chamber, occurred_at, official_type, official_title, official_description, official_result, official_url, position_coverage FROM roll_calls{where}",
            tuple(params),
            limit=limit,
            cursor=cursor,
            filters={
                "text": text,
                "legislature": legislature,
                "chamber": chamber.value if chamber else None,
            },
        )

    def get_roll_call(self, roll_call_id: str) -> CanonicalRecord | None:
        return self._record(
            "SELECT roll_call_id, legislature_number AS legislature, chamber_code AS chamber, occurred_at, official_type, official_title, official_description, official_result, official_url, present_count, voting_count, yes_count, no_count, abstain_count, position_coverage FROM roll_calls WHERE roll_call_id = ?",
            (roll_call_id,),
        )

    def list_person_votes(
        self,
        person_id: str,
        *,
        limit: int = 25,
        cursor: str | None = None,
        release_id: str | None = None,
    ) -> VoterPage:
        return self.find_voters(
            VoteQuery(person_id=person_id),
            limit=limit,
            cursor=cursor,
            release_id=release_id,
        )

    def list_roll_call_positions(
        self, roll_call_id: str, *, limit: int = 25, cursor: str | None = None
    ) -> CanonicalPage:
        return self._collection(
            "roll_call_positions",
            "vote_id",
            """SELECT votes.vote_id AS record_id, votes.vote_id, votes.roll_call_id, people.person_id, people.display_name AS person_name, votes.raw_position, votes.normalized_position AS position, votes.group_id_at_vote
            FROM votes JOIN mandates USING(mandate_id, legislature_number, chamber_code) JOIN people USING(person_id)
            WHERE votes.roll_call_id = ? AND votes.normalization_status = 'normalized'""",
            (roll_call_id,),
            limit=limit,
            cursor=cursor,
            filters={"roll_call_id": roll_call_id},
        )

    def list_disclosures(
        self,
        *,
        person_id: str | None = None,
        limit: int = 25,
        cursor: str | None = None,
    ) -> CanonicalPage:
        where = " WHERE mandates.person_id = ?" if person_id else ""
        params: tuple[object, ...] = (person_id,) if person_id else ()
        return self._collection(
            "disclosures",
            "disclosure_id",
            f"""SELECT disclosure_documents.disclosure_id AS record_id, disclosure_documents.disclosure_id, mandates.person_id, disclosure_documents.official_label, disclosure_documents.official_url, disclosure_documents.observed_at
            FROM disclosure_documents JOIN mandates USING(mandate_id){where}""",
            params,
            limit=limit,
            cursor=cursor,
            filters={"person_id": person_id},
        )

    def _active_release(self) -> str:
        release_id = read_active_release(self.release_root)
        if release_id is None:
            raise NoActiveRelease("no active canonical release")
        return release_id

    def _data_through(self, release_id: str) -> str:
        with self._connection(release_id) as connection:
            row = connection.execute(
                "SELECT data_through FROM releases WHERE release_id = ?", (release_id,)
            ).fetchone()
        if row is None:
            raise NoActiveRelease("release metadata is unavailable")
        return str(row[0])

    def _record(
        self, sql: str, params: tuple[object, ...], *, json_fields: tuple[str, ...] = ()
    ) -> CanonicalRecord | None:
        release_id = self._active_release()
        with self._connection(release_id) as connection:
            row = connection.execute(sql, params).fetchone()
            meta = connection.execute(
                "SELECT data_through FROM releases WHERE release_id = ?", (release_id,)
            ).fetchone()
        if row is None:
            return None
        item: dict[str, Any] = dict(row)
        for field in json_fields:
            item[field] = __import__("json").loads(item[field] or "[]")
        return CanonicalRecord(item, release_id, str(meta[0]))

    def _collection(
        self,
        name: str,
        key: str,
        select_sql: str,
        params: tuple[object, ...],
        *,
        limit: int,
        cursor: str | None,
        filters: dict[str, object] | None = None,
    ) -> CanonicalPage:
        if isinstance(limit, bool) or limit <= 0 or limit > self.max_page_size:
            raise ValueError(f"limit must be between 1 and {self.max_page_size}")
        digest = filter_digest({"collection": name, **(filters or {})})
        state = self.cursors.decode(cursor) if cursor else None
        if state is not None and (
            state.filter_digest != digest or state.roll_call_id != name
        ):
            raise InvalidCursor("cursor does not belong to this collection")
        release_id = state.release_id if state else self._active_release()
        outer = f"SELECT * FROM ({select_sql}) records"
        query_params = list(params)
        if state is not None:
            outer += " WHERE record_id > ?"
            query_params.append(state.vote_id)
        outer += " ORDER BY record_id LIMIT ?"
        query_params.append(limit + 1)
        with self._connection(release_id) as connection:
            rows = connection.execute(outer, query_params).fetchall()
            meta = connection.execute(
                "SELECT data_through FROM releases WHERE release_id = ?", (release_id,)
            ).fetchone()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = tuple(
            {k: row[k] for k in row.keys() if k != "record_id"} for row in rows
        )
        next_cursor = None
        if has_more:
            next_cursor = self.cursors.encode(
                CursorState(release_id, digest, "", name, str(rows[-1]["record_id"]))
            )
        return CanonicalPage(items, release_id, str(meta[0]), next_cursor)
