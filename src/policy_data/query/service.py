from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Iterator

from policy_data.domain.enums import ChamberCode, VotePosition
from policy_data.ingest.publish import read_active_release
from policy_data.query.filters import VoteQuery
from policy_data.query.pagination import (
    CursorCodec,
    CursorState,
    InvalidCursor,
    filter_digest,
)
from policy_data.query.results import VoterPage, VoterResult


class QueryTimeout(TimeoutError):
    pass


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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
        limit: int = 50,
        cursor: str | None = None,
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
        release_id = (
            state.release_id if state else read_active_release(self.release_root)
        )
        if release_id is None:
            raise RuntimeError("no active canonical release")

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
        parameters.append(limit)
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
        if len(items) == limit:
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
        return VoterPage(items, release_id, next_cursor)
