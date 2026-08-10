from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from policy_data.ingest.orchestrate import OfficialRefresh, SourceAssemblyError
from policy_data.ingest.pipeline import ReleaseBuilder
from policy_data.sources.artifacts import ArtifactStore
from policy_data.sources.http import SafeFetcher
from policy_data.sources.registry import SourceDefinition, SourceRegistry

CAMERA = Path("tests/fixtures/camera")
SENATO = Path("tests/fixtures/senato")


def _source(source_id: str, role: str, url: str, media_type: str) -> SourceDefinition:
    chamber = "camera" if role.startswith("camera") else "senato"
    return SourceDefinition(
        source_id=source_id,
        publisher=(
            "Camera dei deputati" if chamber == "camera" else "Senato della Repubblica"
        ),
        dataset=source_id,
        legislature=19,
        chamber=chamber,
        url=url,
        allowed_hosts=frozenset({url.split("/", 3)[2]}),
        media_types=frozenset({media_type}),
        max_bytes=1024 * 1024,
        license_id="CC-BY-SA-4.0" if chamber == "camera" else "CC-BY-3.0",
        adapter_version=f"{chamber}-xix-v1",
        role=role,
    )


def test_offline_cross_chamber_refresh_acquires_builds_and_activates(
    tmp_path: Path,
) -> None:
    detail_url = (
        "https://documenti.camera.it/apps/votazioni/votazionitutte/"
        "schedaVotazione.asp?Legislatura=19&RifVotazione=599_44&tipo=dettaglio"
    )
    sources = [
        _source(
            "camera-votes",
            "camera_votes_rdf",
            "https://dati.camera.it/votes.rdf",
            "application/rdf+xml",
        ),
        _source(
            "camera-deputies",
            "camera_deputies_rdf",
            "https://dati.camera.it/deputies.rdf",
            "application/rdf+xml",
        ),
        _source(
            "camera-mandates",
            "camera_mandates_rdf",
            "https://dati.camera.it/mandates.rdf",
            "application/rdf+xml",
        ),
        _source(
            "camera-groups",
            "camera_groups_rdf",
            "https://dati.camera.it/groups.rdf",
            "application/rdf+xml",
        ),
        _source("camera-detail-599044", "camera_vote_detail", detail_url, "text/html"),
        _source(
            "senato-vote-metadata",
            "senato_vote_window",
            "https://dati.senato.it/vote-metadata.json",
            "application/json",
        ),
        _source(
            "senato-vote-yes",
            "senato_vote_window",
            "https://dati.senato.it/vote-yes.json",
            "application/json",
        ),
        _source(
            "senato-vote-no",
            "senato_vote_window",
            "https://dati.senato.it/vote-no.json",
            "application/json",
        ),
        _source(
            "senato-people",
            "senato_people_json",
            "https://dati.senato.it/people.json",
            "application/json",
        ),
        _source(
            "senato-groups",
            "senato_groups_json",
            "https://dati.senato.it/groups.json",
            "application/json",
        ),
    ]
    camera_votes = (CAMERA / "votes.rdf").read_bytes().split(
        b'  <rdf:Description rdf:about="http://dati.camera.it/ocd/votazione.rdf/vs19_100_001">',
        1,
    )[0] + b"</rdf:RDF>"
    senato_payload = json.loads((SENATO / "votes.json").read_bytes())
    senato_rows = senato_payload["results"]["bindings"]

    def senato_body(rows: list[dict[str, object]]) -> bytes:
        return json.dumps(
            {"head": senato_payload["head"], "results": {"bindings": rows}}
        ).encode()

    metadata_row = {
        key: value
        for key, value in senato_rows[0].items()
        if key not in {"senator", "position_predicate"}
    }
    position_rows = [
        {key: row[key] for key in ("vote", "date", "position_predicate", "senator")}
        for row in senato_rows
    ]
    bodies = {
        sources[0].url: camera_votes,
        sources[1].url: (CAMERA / "deputies.rdf").read_bytes(),
        sources[2].url: (CAMERA / "mandates.rdf").read_bytes(),
        sources[3].url: (CAMERA / "groups.rdf").read_bytes(),
        sources[4].url: (CAMERA / "vote_detail.html").read_bytes(),
        sources[5].url: senato_body([metadata_row]),
        sources[6].url: senato_body([position_rows[0]]),
        sources[7].url: senato_body([position_rows[1]]),
        sources[8].url: (SENATO / "people.json").read_bytes(),
        sources[9].url: (SENATO / "groups.json").read_bytes(),
    }
    media_types = {source.url: next(iter(source.media_types)) for source in sources}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        return httpx.Response(
            200,
            headers={"content-type": media_types[url]},
            content=bodies[url],
        )

    release_root = tmp_path / "published"
    refresh = OfficialRefresh(
        SourceRegistry(sources),
        SafeFetcher(
            ArtifactStore(tmp_path / "artifacts"),
            transport=httpx.MockTransport(handler),
            resolver=lambda host: ["93.184.216.34"],
        ),
        ReleaseBuilder(release_root),
        now=datetime(2026, 8, 10, tzinfo=UTC),
    )
    result = refresh.run()

    assert result.created is True
    assert refresh.builder.active_release_id() == result.release_id
    database = sqlite3.connect(result.path / "canonical.sqlite3")
    assert database.execute(
        "SELECT chamber_code, COUNT(*) FROM votes "
        "GROUP BY chamber_code ORDER BY chamber_code"
    ).fetchall() == [("camera", 2), ("senato", 2)]
    assert database.execute("SELECT COUNT(*) FROM fact_lineage").fetchone()[0] > 0
    assert (
        database.execute(
            "SELECT official_url FROM parliamentary_items WHERE chamber_code = 'camera'"
        ).fetchone()[0]
        == "http://dati.camera.it/ocd/attocamera.rdf/ac19_1311"
    )
    assert database.execute("SELECT COUNT(*) FROM roll_call_items").fetchone()[0] == 2
    assert (
        database.execute(
            "SELECT COUNT(DISTINCT sr.artifact_id) "
            "FROM fact_lineage fl JOIN source_records sr USING (source_record_id) "
            "JOIN votes v ON v.vote_id = fl.fact_id "
            "WHERE fl.fact_type = 'vote' AND v.chamber_code = 'senato'"
        ).fetchone()[0]
        == 2
    )
    assert len(list((tmp_path / "artifacts" / "sha256").glob("*/*/body"))) == 10


def test_refresh_preflight_fails_closed_with_missing_official_roles(
    tmp_path: Path,
) -> None:
    source = _source(
        "camera-votes",
        "camera_votes_rdf",
        "https://dati.camera.it/votes.rdf",
        "application/rdf+xml",
    )
    refresh = OfficialRefresh(
        SourceRegistry([source]),
        SafeFetcher(ArtifactStore(tmp_path / "artifacts")),
        ReleaseBuilder(tmp_path / "published"),
    )

    with pytest.raises(SourceAssemblyError, match="senato_people_json"):
        refresh.run()
