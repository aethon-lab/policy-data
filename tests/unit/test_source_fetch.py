from __future__ import annotations

import json
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
