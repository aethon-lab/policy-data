from __future__ import annotations

import json
import hashlib
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from policy_data.sources.artifacts import ArtifactStore
from policy_data.sources.http import FetchRejected, SafeFetcher
from policy_data.sources.registry import SourceDefinition


def _source() -> SourceDefinition:
    return SourceDefinition(
        source_id="fixture",
        publisher="Camera dei deputati",
        dataset="votes",
        legislature=19,
        chamber="camera",
        url="https://dati.camera.it/votes.zip",
        allowed_hosts=frozenset({"dati.camera.it"}),
        media_types=frozenset({"application/zip"}),
        max_bytes=64,
        license_id="CC-BY-SA-4.0",
        adapter_version="1",
    )


def test_valid_body_is_content_addressed_and_reused(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/zip", "etag": '"one"'},
            content=b"PK\x03\x04fixture",
        )
    )
    store = ArtifactStore(tmp_path)
    fetcher = SafeFetcher(
        store, transport=transport, resolver=lambda host: ["93.184.216.34"]
    )

    first = fetcher.fetch(_source())
    second = fetcher.fetch(_source())

    assert first.sha256 == second.sha256
    assert first.path == second.path
    metadata = json.loads(first.metadata_path.read_text(encoding="utf-8"))
    assert metadata["publisher"] == "Camera dei deputati"
    assert metadata["license"] == "CC-BY-SA-4.0"
    assert metadata["source_url"] == _source().url


def test_sparql_query_uses_allowlisted_get_request(tmp_path: Path) -> None:
    source = replace(
        _source(),
        source_id="senato-query",
        publisher="Senato della Repubblica",
        chamber="senato",
        url="https://dati.senato.it/sparql",
        allowed_hosts=frozenset({"dati.senato.it"}),
        media_types=frozenset({"application/sparql-results+json"}),
        query="SELECT * WHERE { ?s ?p ?o } LIMIT 1",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.params["query"] == source.query
        assert request.url.params["output"] == "application/sparql-results+json"
        return httpx.Response(
            200,
            headers={"content-type": "application/sparql-results+json"},
            content=b'{"results":{"bindings":[]}}',
        )

    artifact = SafeFetcher(
        ArtifactStore(tmp_path),
        transport=httpx.MockTransport(handler),
        resolver=lambda host: ["93.184.216.34"],
    ).fetch(source)
    assert artifact.path.read_bytes() == b'{"results":{"bindings":[]}}'


def test_html_challenge_and_cross_origin_redirect_are_rejected(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/redirect":
            return httpx.Response(
                302, headers={"location": "https://evil.example/data"}
            )
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="browser challenge"
        )

    fetcher = SafeFetcher(
        ArtifactStore(tmp_path),
        transport=httpx.MockTransport(handler),
        resolver=lambda host: ["93.184.216.34"],
    )
    with pytest.raises(FetchRejected, match="media type|HTML"):
        fetcher.fetch(_source())

    redirected = replace(_source(), url="https://dati.camera.it/redirect")
    with pytest.raises(FetchRejected, match="redirect"):
        fetcher.fetch(redirected)


def test_exact_camera_proof_challenge_is_solved_without_executing_javascript(
    tmp_path: Path,
) -> None:
    calls = 0
    hint = "a" * 32
    expected = hashlib.sha1(b"107").hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    f'<form id="js-challenge-form" action="/votes.zip" method="get">'
                    f'<input type="hidden" name="hint" value="{hint}"/>'
                    "</form><script>var x =100; var y = "
                    f'"{expected}";</script>'
                ),
            )
        assert request.url.params["hint"] == hint
        assert request.url.params["answer"] == "7"
        return httpx.Response(
            200,
            headers={"content-type": "application/zip"},
            content=b"PK\x03\x04fixture",
        )

    artifact = SafeFetcher(
        ArtifactStore(tmp_path),
        transport=httpx.MockTransport(handler),
        resolver=lambda host: ["93.184.216.34"],
    ).fetch(_source())
    assert artifact.path.read_bytes() == b"PK\x03\x04fixture"
    assert calls == 2


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.2", "169.254.169.254", "::1", "fe80::1"]
)
def test_private_or_local_resolution_is_rejected(tmp_path: Path, address: str) -> None:
    fetcher = SafeFetcher(ArtifactStore(tmp_path), resolver=lambda host: [address])
    with pytest.raises(FetchRejected, match="address"):
        fetcher.fetch(_source())


def test_304_reuses_only_a_verified_prior_artifact(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                headers={"content-type": "application/zip", "etag": '"one"'},
                content=b"PK\x03\x04fixture",
            )
        assert request.headers["if-none-match"] == '"one"'
        return httpx.Response(304)

    fetcher = SafeFetcher(
        ArtifactStore(tmp_path),
        transport=httpx.MockTransport(handler),
        resolver=lambda host: ["93.184.216.34"],
    )
    first = fetcher.fetch(_source())
    assert fetcher.fetch(_source()).sha256 == first.sha256


def test_streaming_fetch_enforces_bound_and_cleans_private_temp(tmp_path: Path) -> None:
    source = replace(_source(), max_bytes=8)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/zip"},
            content=b"0123456789",
        )
    )
    fetcher = SafeFetcher(
        ArtifactStore(tmp_path),
        transport=transport,
        resolver=lambda host: ["93.184.216.34"],
    )

    with pytest.raises(FetchRejected, match="maximum byte count"):
        fetcher.fetch(source)
    temporary_root = tmp_path / ".tmp"
    assert list(temporary_root.iterdir()) == []
    assert temporary_root.stat().st_mode & 0o777 == 0o700


def test_html_challenge_split_across_stream_chunks_is_rejected(tmp_path: Path) -> None:
    source = replace(_source(), media_types=frozenset({"text/html"}))
    request = httpx.Request("GET", source.url)
    response = httpx.Response(
        200,
        request=request,
        headers={"content-type": "text/html"},
        stream=httpx.ByteStream(b"browser challenge"),
    )
    fetcher = SafeFetcher(
        ArtifactStore(tmp_path),
        transport=httpx.MockTransport(lambda request: response),
        resolver=lambda host: ["93.184.216.34"],
    )

    with pytest.raises(FetchRejected, match="challenge body"):
        fetcher.fetch(source)
    assert list((tmp_path / ".tmp").iterdir()) == []


def test_artifact_store_rejects_symlinked_private_temp(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    (artifact_root / ".tmp").symlink_to(outside, target_is_directory=True)
    fetcher = SafeFetcher(
        ArtifactStore(artifact_root),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "application/zip"},
                content=b"PK\x03\x04fixture",
            )
        ),
        resolver=lambda host: ["93.184.216.34"],
    )

    with pytest.raises(ValueError, match="private directory"):
        fetcher.fetch(_source())
    assert list(outside.iterdir()) == []
