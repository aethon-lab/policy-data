from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from policy_data.app import create_app
from policy_data.domain.enums import ChamberCode, VotePosition
from policy_data.mcp.server import create_mcp_server
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
        )


class FakeAuthService:
    def authenticate_api_key(self, raw: str):
        return object() if raw == "pd_live_valid_key_material" else None


@pytest.mark.asyncio
async def test_mcp_tool_is_small_read_only_and_structured() -> None:
    server = create_mcp_server(FakeQueryService())
    tools = await server.list_tools()
    assert [tool.name for tool in tools] == ["find_voters"]
    tool = tools[0]
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert tool.output_schema["additionalProperties"] is False
    result = await server.call_tool(
        "find_voters", {"text": "Superbonus", "position": "yes", "limit": 20}
    )
    assert result.structured_content["items"][0]["person_id"] == "person:1"
    assert result.structured_content["items"][0]["measure_url"].endswith("/law/1")


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
