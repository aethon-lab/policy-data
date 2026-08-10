import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from policy_data.app import create_app
from policy_data.domain.enums import ChamberCode, VotePosition
from policy_data.query.results import VoterPage, VoterResult


class FakeQueryService:
    def find_voters(self, query, *, limit=50, cursor=None):
        return VoterPage(
            (
                VoterResult(
                    "vote:1",
                    "roll:1",
                    "person:1",
                    "Ada Rossi",
                    VotePosition.YES,
                    "Favorevole",
                    "group:1",
                    "Gruppo Alfa",
                    19,
                    ChamberCode.CAMERA,
                    "2026-01-22T12:00:00Z",
                    "final",
                    "approved",
                    "item:1",
                    "Superbonus",
                    "https://camera.it/law/1",
                    "https://camera.it/vote/1",
                    "Camera dei deputati",
                    "CC-BY-SA-4.0",
                ),
            ),
            "release-test",
            None,
        )


class FakeAuthService:
    def authenticate_api_key(self, raw: str):
        return (
            type("Principal", (), {"account_id": "account:1", "key_id": "key:1"})()
            if raw == "pd_live_valid_key_material"
            else None
        )


def _release(root: Path) -> bytes:
    release = root / "releases" / "release-test"
    release.mkdir(parents=True)
    body = b"download"
    (release / "camera-votes.csv.gz").write_bytes(body)
    manifest = {
        "release_id": "release-test",
        "files": [
            {
                "filename": "camera-votes.csv.gz",
                "sha256": hashlib.sha256(body).hexdigest(),
                "byte_count": len(body),
                "row_count": 1,
                "media_type": "text/csv",
                "content_encoding": "gzip",
                "source_id": "camera_votes_xix",
                "publisher": "Camera dei deputati",
                "license_id": "CC-BY-SA-4.0",
            }
        ],
    }
    (release / "manifest.json").write_text(json.dumps(manifest))
    (root / "active.json").write_text(json.dumps({"release_id": "release-test"}))
    return body


def test_bearer_api_and_problem_contract(tmp_path: Path) -> None:
    _release(tmp_path)
    app = create_app(FakeQueryService(), FakeAuthService(), release_root=tmp_path)
    client = TestClient(app)
    missing = client.get("/api/v1/voters?text=Superbonus")
    assert missing.status_code == 401
    assert missing.headers["content-type"].startswith("application/problem+json")
    assert missing.json()["type"].endswith("/missing-api-key")
    invalid = client.get(
        "/api/v1/voters", headers={"Authorization": "Bearer pd_live_wrong_key_material"}
    )
    assert invalid.status_code == 401
    valid = client.get(
        "/api/v1/voters?text=Superbonus&position=yes&chamber=camera&legislature=19",
        headers={"Authorization": "Bearer pd_live_valid_key_material"},
    )
    assert valid.status_code == 200
    assert valid.json()["items"][0]["person_id"] == "person:1"
    assert valid.json()["items"][0]["measure_url"] == "https://camera.it/law/1"
    assert valid.headers["cache-control"] == "no-store"


def test_openapi_health_manifest_and_download_are_public(tmp_path: Path) -> None:
    body = _release(tmp_path)
    app = create_app(FakeQueryService(), FakeAuthService(), release_root=tmp_path)
    client = TestClient(app)
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    assert openapi.json()["openapi"].startswith("3.1")
    assert (
        openapi.json()["paths"]["/api/v1/voters"]["get"]["operationId"] == "findVoters"
    )
    health = client.get("/health")
    assert health.json()["release_id"] == "release-test"
    manifest = client.get("/releases/current/manifest.json")
    assert manifest.status_code == 200
    download = client.get("/releases/release-test/camera-votes.csv.gz")
    assert download.content == body
    assert download.headers["etag"] == f'"{hashlib.sha256(body).hexdigest()}"'
    assert download.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert download.headers["x-content-type-options"] == "nosniff"


def test_unmanifested_and_traversal_downloads_are_not_served(tmp_path: Path) -> None:
    _release(tmp_path)
    app = create_app(FakeQueryService(), FakeAuthService(), release_root=tmp_path)
    client = TestClient(app)
    assert client.get("/releases/release-test/secret.txt").status_code == 404
    assert client.get("/releases/release-test/../active.json").status_code == 404
