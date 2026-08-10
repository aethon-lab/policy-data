import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from policy_data.app import create_app
from policy_data.domain.enums import ChamberCode, VotePosition
from policy_data.query.results import (
    CanonicalPage,
    CanonicalRecord,
    VoterPage,
    VoterResult,
)
from policy_data.query.pagination import InvalidCursor
from policy_data.query.service import NoActiveRelease, QueryTimeout


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
            "2026-01-22",
        )

    def list_legislatures(self, **kwargs):
        return CanonicalPage(
            ({"number": 19, "roman_numeral": "XIX"},),
            "release-test",
            "2026-01-22",
            None,
        )

    def list_people(self, **kwargs):
        return CanonicalPage(
            ({"person_id": "person:1", "display_name": "Ada Rossi"},),
            "release-test",
            "2026-01-22",
            None,
        )

    def get_person(self, person_id):
        return CanonicalRecord(
            {"person_id": person_id, "display_name": "Ada Rossi", "disclosures": []},
            "release-test",
            "2026-01-22",
        )

    def list_person_votes(self, person_id, **kwargs):
        return self.find_voters(None, **kwargs)

    def list_groups(self, **kwargs):
        return CanonicalPage(
            ({"group_id": "group:1", "name": "Gruppo Alfa"},),
            "release-test",
            "2026-01-22",
            None,
        )

    def list_roll_calls(self, **kwargs):
        return CanonicalPage(
            ({"roll_call_id": "roll:1", "official_title": "Votazione finale"},),
            "release-test",
            "2026-01-22",
            None,
        )

    def get_roll_call(self, roll_call_id):
        return CanonicalRecord(
            {"roll_call_id": roll_call_id, "official_result": "approved"},
            "release-test",
            "2026-01-22",
        )

    def list_roll_call_positions(self, roll_call_id=None, **kwargs):
        return CanonicalPage(
            ({"vote_id": "vote:1", "roll_call_id": roll_call_id},),
            "release-test",
            "2026-01-22",
            None,
        )

    def list_disclosures(self, **kwargs):
        return CanonicalPage((), "release-test", "2026-01-22", None)

    def dataset_status(self):
        return CanonicalRecord(
            {"release_id": "release-test", "counts": {"votes": 1}},
            "release-test",
            "2026-01-22",
        )


class FakeAuthService:
    def authenticate_api_key(self, raw: str):
        return (
            type("Principal", (), {"account_id": "account:1", "key_id": "key:1"})()
            if raw == "pd_live_valid_key_material"
            else None
        )

    def authorize_data_request(self, principal, *, source_ip: str):
        return True


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
    assert "data_through" in valid.json()
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


def test_versioned_canonical_resources_share_release_contract(tmp_path: Path) -> None:
    _release(tmp_path)
    client = TestClient(
        create_app(FakeQueryService(), FakeAuthService(), release_root=tmp_path)
    )
    headers = {"Authorization": "Bearer pd_live_valid_key_material"}
    paths = (
        "/api/v1/legislatures",
        "/api/v1/people",
        "/api/v1/people/person:1",
        "/api/v1/people/person:1/votes",
        "/api/v1/groups",
        "/api/v1/roll-calls",
        "/api/v1/roll-calls/roll:1",
        "/api/v1/roll-calls/roll:1/positions",
        "/api/v1/disclosures",
        "/api/v1/dataset-status",
    )
    for path in paths:
        response = client.get(path, headers=headers)
        assert response.status_code == 200, path
        assert response.json()["release_id"] == "release-test"
        assert response.json()["data_through"] == "2026-01-22"


def test_unmanifested_and_traversal_downloads_are_not_served(tmp_path: Path) -> None:
    _release(tmp_path)
    app = create_app(FakeQueryService(), FakeAuthService(), release_root=tmp_path)
    client = TestClient(app)
    assert client.get("/releases/release-test/secret.txt").status_code == 404
    assert client.get("/releases/release-test/../active.json").status_code == 404


def test_readiness_requires_an_active_complete_release(tmp_path: Path) -> None:
    app = create_app(FakeQueryService(), FakeAuthService(), release_root=tmp_path)
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "degraded"
    assert client.get("/ready").status_code == 503
    _release(tmp_path)
    assert client.get("/ready").status_code == 200


def test_query_failures_use_problem_contract(tmp_path: Path) -> None:
    _release(tmp_path)

    class FailingQueryService(FakeQueryService):
        error: Exception = InvalidCursor("bad cursor")

        def find_voters(self, query, *, limit=25, cursor=None):
            raise self.error

    service = FailingQueryService()
    client = TestClient(create_app(service, FakeAuthService(), release_root=tmp_path))
    headers = {"Authorization": "Bearer pd_live_valid_key_material"}
    for error, status in (
        (InvalidCursor("bad cursor"), 400),
        (QueryTimeout("slow query"), 504),
        (NoActiveRelease("no release"), 503),
    ):
        service.error = error
        response = client.get("/api/v1/voters", headers=headers)
        assert response.status_code == status
        assert response.headers["content-type"].startswith("application/problem+json")


def test_request_body_is_limited_before_mcp_parsing(tmp_path: Path) -> None:
    app = create_app(FakeQueryService(), FakeAuthService(), release_root=tmp_path)
    response = TestClient(app).post(
        "/mcp",
        content=b"x" * 65_537,
        headers={"Authorization": "Bearer pd_live_valid_key_material"},
    )
    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/problem+json")

    streamed = TestClient(app).post(
        "/mcp",
        content=(chunk for chunk in (b"x" * 40_000, b"y" * 30_000)),
        headers={"Authorization": "Bearer pd_live_valid_key_material"},
    )
    assert streamed.status_code == 413


def test_protected_rest_rate_limit_uses_problem_contract(tmp_path: Path) -> None:
    _release(tmp_path)

    class LimitedAuth(FakeAuthService):
        def authorize_data_request(self, principal, *, source_ip: str):
            return False

    client = TestClient(
        create_app(FakeQueryService(), LimitedAuth(), release_root=tmp_path)
    )
    response = client.get(
        "/api/v1/voters",
        headers={"Authorization": "Bearer pd_live_valid_key_material"},
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
    assert response.json()["type"].endswith("/rate-limited")
