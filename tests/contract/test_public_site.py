from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from policy_data.app import create_app
from policy_data.auth.service import SESSION_COOKIE_MAX_AGE
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
                    "Conversione Superbonus",
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
        return None

    def validate_session(self, raw: str):
        return None


def test_public_home_search_docs_and_machine_discovery(tmp_path: Path) -> None:
    app = create_app(
        FakeQueryService(),
        FakeAuthService(),
        release_root=tmp_path,
        enable_mcp=False,
        public_site_url="https://policy.example",
    )
    client = TestClient(app)
    home = client.get("/")
    assert home.status_code == 200
    assert "Segui i voti" in home.text
    assert "Camera" in home.text and "Senato" in home.text
    search = client.get("/cerca?q=Superbonus&position=yes")
    assert "Ada Rossi" in search.text
    assert "Conversione Superbonus" in search.text
    assert "https://camera.it/law/1" in search.text
    assert client.get("/docs/api").status_code == 200
    llms = client.get("/llms.txt")
    assert "non fare scrape" in llms.text
    assert "https://policy.example/mcp" in llms.text
    robots = client.get("/robots.txt")
    assert "Disallow: /dashboard" in robots.text
    assert "https://policy.example/sitemap.xml" in robots.text
    assert (
        client.get("/sitemap.xml").headers["content-type"].startswith("application/xml")
    )


def test_anonymous_dashboard_is_no_store(tmp_path: Path) -> None:
    app = create_app(
        FakeQueryService(), FakeAuthService(), release_root=tmp_path, enable_mcp=False
    )
    response = TestClient(app).get("/dashboard")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "nessuna password" in response.text


def test_login_cookies_match_the_session_absolute_lifetime(tmp_path: Path) -> None:
    class LoginAuthService(FakeAuthService):
        def verify_code(self, challenge_id: str, code: str, *, source_ip: str):
            assert source_ip == "testclient"
            return SimpleNamespace(
                generated=SimpleNamespace(raw_token="session", csrf_token="csrf")
            )

    app = create_app(
        FakeQueryService(), LoginAuthService(), release_root=tmp_path, enable_mcp=False
    )
    response = TestClient(app).post(
        "/auth/verify-code", data={"challenge_id": "challenge", "code": "123456"}
    )

    assert response.history[0].status_code == 303
    cookies = response.history[0].headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert all(f"Max-Age={SESSION_COOKIE_MAX_AGE}" in cookie for cookie in cookies)
