from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from policy_data.app import create_app
from policy_data.domain.enums import ChamberCode, VotePosition
from policy_data.mcp.server import create_mcp_server
from policy_data.query.results import (
    CanonicalPage,
    CanonicalRecord,
    VoterPage,
    VoterResult,
)


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
                    None,
                    None,
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
            {"person_id": person_id, "disclosures": []}, "release-test", "2026-01-22"
        )

    def list_roll_calls(self, **kwargs):
        return CanonicalPage(
            ({"roll_call_id": "roll:1"},), "release-test", "2026-01-22", None
        )

    def get_roll_call(self, roll_call_id):
        return CanonicalRecord(
            {"roll_call_id": roll_call_id}, "release-test", "2026-01-22"
        )

    def list_person_votes(self, person_id, **kwargs):
        return self.find_voters(None, **kwargs)

    def list_roll_call_positions(self, roll_call_id, **kwargs):
        return CanonicalPage(
            ({"vote_id": "vote:1", "roll_call_id": roll_call_id},),
            "release-test",
            "2026-01-22",
            None,
        )

    def list_groups(self, **kwargs):
        return CanonicalPage(
            ({"group_id": "group:1"},), "release-test", "2026-01-22", None
        )

    def dataset_status(self):
        return CanonicalRecord(
            {"release_id": "release-test"}, "release-test", "2026-01-22"
        )


class FakeAuthService:
    def authenticate_api_key(self, raw: str):
        return object() if raw == "pd_live_valid_key_material" else None

    def authorize_data_request(self, principal, *, source_ip: str):
        return True


@pytest.mark.asyncio
async def test_mcp_tool_is_small_read_only_and_structured() -> None:
    server = create_mcp_server(FakeQueryService())
    tools = await server.list_tools()
    assert [tool.name for tool in tools] == [
        "find_voters",
        "list_legislatures",
        "search_people",
        "get_person",
        "search_roll_calls",
        "get_roll_call",
        "list_person_votes",
        "list_roll_call_positions",
        "list_groups",
        "get_dataset_status",
    ]
    tool = tools[0]
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert tool.output_schema["additionalProperties"] is False
    result = await server.call_tool(
        "find_voters", {"text": "Superbonus", "position": "yes", "limit": 20}
    )
    assert result.structured_content["items"][0]["person_id"] == "person:1"
    assert result.structured_content["items"][0]["measure_url"].endswith("/law/1")
    assert result.structured_content["data_through"] == "2026-01-22"
    properties = tool.input_schema["properties"]
    assert properties["text"]["anyOf"][0]["maxLength"] == 200
    assert properties["limit"]["minimum"] == 1
    assert properties["limit"]["maximum"] == 100
    assert properties["cursor"]["anyOf"][0]["maxLength"] == 2048
    legislature_result = await server.call_tool("list_legislatures", {})
    assert legislature_result.structured_content["items"][0]["number"] == 19
    assert legislature_result.structured_content["data_through"] == "2026-01-22"
    status_result = await server.call_tool("get_dataset_status", {})
    assert status_result.structured_content["release_id"] == "release-test"


def test_streamable_http_uses_the_same_bearer_key(tmp_path: Path) -> None:
    app = create_app(FakeQueryService(), FakeAuthService(), release_root=tmp_path)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2026-07-28",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    with TestClient(app) as client:
        missing = client.post(
            "/mcp",
            json=payload,
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert missing.status_code == 401
        valid = client.post(
            "/mcp",
            json=payload,
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer pd_live_valid_key_material",
            },
        )
        assert valid.status_code == 200
        assert valid.json()["result"]["serverInfo"]["name"] == "policy-data-italia"


def test_streamable_http_shares_the_protected_rate_limit(tmp_path: Path) -> None:
    class LimitedAuth(FakeAuthService):
        def authorize_data_request(self, principal, *, source_ip: str):
            return False

    app = create_app(FakeQueryService(), LimitedAuth(), release_root=tmp_path)
    response = TestClient(app).post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={"Authorization": "Bearer pd_live_valid_key_material"},
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
    assert response.json() == {"error": "rate_limited"}
