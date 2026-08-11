from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter, Form, Request
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.concurrency import run_in_threadpool
from starlette.templating import Jinja2Templates

from policy_data.auth.service import SESSION_COOKIE_MAX_AGE
from policy_data.domain.enums import ChamberCode, VotePosition
from policy_data.query.filters import VoteQuery
from policy_data.query.results import CanonicalPage, CanonicalRecord, VoterPage

PACKAGE_ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=PACKAGE_ROOT / "templates")


class QueryServiceContract(Protocol):
    def find_voters(
        self, query: VoteQuery, *, limit: int = 50, cursor: str | None = None
    ) -> VoterPage: ...

    def list_people(
        self, *, text: str | None, limit: int, cursor: str | None
    ) -> CanonicalPage: ...

    def get_person(self, person_id: str) -> CanonicalRecord | None: ...

    def list_person_votes(
        self,
        person_id: str,
        *,
        limit: int,
        cursor: str | None,
        release_id: str | None = None,
    ) -> VoterPage: ...

    def list_roll_calls(
        self,
        *,
        text: str | None,
        legislature: int | None,
        chamber: ChamberCode | None,
        limit: int,
        cursor: str | None,
    ) -> CanonicalPage: ...

    def get_roll_call(self, roll_call_id: str) -> CanonicalRecord | None: ...

    def list_roll_call_positions(
        self, roll_call_id: str, *, limit: int, cursor: str | None
    ) -> CanonicalPage: ...

    def list_groups(
        self,
        *,
        legislature: int | None,
        chamber: ChamberCode | None,
        limit: int,
        cursor: str | None,
    ) -> CanonicalPage: ...

    def dataset_status(self) -> CanonicalRecord: ...


class AuthServiceContract(Protocol):
    async def request_code(self, email: str, *, source_ip: str) -> Any: ...

    def verify_code(
        self, challenge_id: str, code: str, *, source_ip: str
    ) -> Any | None: ...

    def validate_session(self, raw: str) -> Any | None: ...

    def verify_csrf(self, session_id: str, raw_csrf: str) -> bool: ...

    def list_api_keys(self, account_id: str) -> tuple[Any, ...]: ...

    def create_api_key(self, account_id: str, label: str) -> Any: ...

    def revoke_api_key(self, account_id: str, key_id: str) -> bool: ...


def create_web_router(
    query_service: QueryServiceContract,
    auth_service: AuthServiceContract,
    *,
    public_site_url: str,
) -> APIRouter:
    router = APIRouter()
    templates.env.globals["public_site_url"] = public_site_url.rstrip("/")

    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request,
            name="home.html",
            context={"public_site_url": public_site_url},
        )

    @router.get("/cerca", response_class=HTMLResponse, include_in_schema=False)
    async def search(
        request: Request,
        q: str = "",
        position: VotePosition | None = None,
        chamber: ChamberCode | None = None,
    ) -> Response:
        clean_query = q.strip()
        page = None
        error = None
        if clean_query:
            try:
                page = await run_in_threadpool(
                    query_service.find_voters,
                    VoteQuery(clean_query, position, chamber, 19),
                    limit=50,
                )
            except (RuntimeError, ValueError):
                error = "I dati non sono ancora disponibili. Riprova tra poco."
        return templates.TemplateResponse(
            request=request,
            name="search.html",
            context={
                "q": clean_query,
                "selected_position": position.value if position else "",
                "selected_chamber": chamber.value if chamber else "",
                "page": page,
                "error": error,
            },
        )

    @router.get("/docs", response_class=HTMLResponse, include_in_schema=False)
    def docs(request: Request) -> Response:
        return templates.TemplateResponse(request=request, name="docs.html", context={})

    @router.get("/politici", response_class=HTMLResponse, include_in_schema=False)
    async def people(request: Request, q: str = "") -> Response:
        page, error = await _web_query(
            query_service.list_people, text=q.strip() or None, limit=50, cursor=None
        )
        return templates.TemplateResponse(
            request=request,
            name="people.html",
            context={"page": page, "q": q.strip(), "error": error},
        )

    @router.get(
        "/politici/{person_id}", response_class=HTMLResponse, include_in_schema=False
    )
    async def person(request: Request, person_id: str) -> Response:
        record, error = await _web_query(query_service.get_person, person_id)
        votes = None
        if record is not None:
            votes, votes_error = await _web_query(
                query_service.list_person_votes,
                person_id,
                limit=50,
                cursor=None,
                release_id=record.release_id,
            )
            error = error or votes_error
        return templates.TemplateResponse(
            request=request,
            name="person.html",
            context={"record": record, "votes": votes, "error": error},
            status_code=404 if record is None and error is None else 200,
        )

    @router.get("/votazioni", response_class=HTMLResponse, include_in_schema=False)
    async def roll_calls(
        request: Request,
        q: str = "",
        chamber: ChamberCode | None = None,
    ) -> Response:
        page, error = await _web_query(
            query_service.list_roll_calls,
            text=q.strip() or None,
            legislature=19,
            chamber=chamber,
            limit=50,
            cursor=None,
        )
        return templates.TemplateResponse(
            request=request,
            name="roll_calls.html",
            context={"page": page, "q": q.strip(), "chamber": chamber, "error": error},
        )

    @router.get(
        "/votazioni/{roll_call_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    async def roll_call(request: Request, roll_call_id: str) -> Response:
        record, error = await _web_query(query_service.get_roll_call, roll_call_id)
        positions = None
        if record is not None:
            positions, positions_error = await _web_query(
                query_service.list_roll_call_positions,
                roll_call_id,
                limit=50,
                cursor=None,
            )
            error = error or positions_error
        return templates.TemplateResponse(
            request=request,
            name="roll_call.html",
            context={"record": record, "positions": positions, "error": error},
            status_code=404 if record is None and error is None else 200,
        )

    @router.get("/gruppi", response_class=HTMLResponse, include_in_schema=False)
    async def groups(request: Request) -> Response:
        page, error = await _web_query(
            query_service.list_groups,
            legislature=19,
            chamber=None,
            limit=100,
            cursor=None,
        )
        return templates.TemplateResponse(
            request=request,
            name="groups.html",
            context={"page": page, "error": error},
        )

    @router.get("/dati", response_class=HTMLResponse, include_in_schema=False)
    async def data_status(request: Request) -> Response:
        record, error = await _web_query(query_service.dataset_status)
        return templates.TemplateResponse(
            request=request,
            name="data_status.html",
            context={"record": record, "error": error},
        )

    @router.get("/docs/api", response_class=HTMLResponse, include_in_schema=False)
    def api_docs(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request, name="api_docs.html", context={}
        )

    @router.get("/docs/mcp", response_class=HTMLResponse, include_in_schema=False)
    def mcp_docs(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request, name="mcp_docs.html", context={}
        )

    @router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
    def privacy(request: Request) -> Response:
        return templates.TemplateResponse(
            request=request, name="privacy.html", context={}
        )

    @router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def dashboard(request: Request) -> Response:
        session, raw_csrf = _browser_session(request, auth_service)
        if session is None:
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={},
                headers={"Cache-Control": "no-store"},
            )
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "keys": auth_service.list_api_keys(session.account_id),
                "csrf": raw_csrf,
                "raw_key": None,
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.post(
        "/auth/request-code", response_class=HTMLResponse, include_in_schema=False
    )
    async def request_code(request: Request, email: str = Form(...)) -> Response:
        try:
            result = await auth_service.request_code(
                email, source_ip=_source_ip(request)
            )
            challenge_id = result.challenge_id
        except (ValueError, RuntimeError):
            challenge_id = ""
        return templates.TemplateResponse(
            request=request,
            name="verify.html",
            context={"challenge_id": challenge_id},
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/auth/verify-code", include_in_schema=False)
    def verify_code(
        request: Request,
        challenge_id: str = Form(...),
        code: str = Form(...),
    ) -> Response:
        verified = auth_service.verify_code(
            challenge_id, code, source_ip=_source_ip(request)
        )
        if verified is None:
            return RedirectResponse("/dashboard?errore=codice", status_code=303)
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie(
            "policy_session",
            verified.generated.raw_token,
            secure=True,
            httponly=True,
            samesite="lax",
            max_age=SESSION_COOKIE_MAX_AGE,
        )
        response.set_cookie(
            "policy_csrf",
            verified.generated.csrf_token,
            secure=True,
            httponly=True,
            samesite="lax",
            max_age=SESSION_COOKIE_MAX_AGE,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post(
        "/dashboard/keys", response_class=HTMLResponse, include_in_schema=False
    )
    def create_key(
        request: Request, label: str = Form(...), csrf: str = Form(...)
    ) -> Response:
        session, raw_csrf = _browser_session(request, auth_service)
        if session is None or not raw_csrf or csrf != raw_csrf:
            return RedirectResponse("/dashboard", status_code=303)
        issued = auth_service.create_api_key(session.account_id, label)
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "keys": auth_service.list_api_keys(session.account_id),
                "csrf": raw_csrf,
                "raw_key": issued.generated.raw,
            },
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/dashboard/keys/{key_id}/revoke", include_in_schema=False)
    def revoke_key(request: Request, key_id: str, csrf: str = Form(...)) -> Response:
        session, raw_csrf = _browser_session(request, auth_service)
        if session is not None and raw_csrf and csrf == raw_csrf:
            auth_service.revoke_api_key(session.account_id, key_id)
        return RedirectResponse("/dashboard", status_code=303)

    @router.get("/llms.txt", response_class=PlainTextResponse, include_in_schema=False)
    def llms() -> str:
        return _llms_text(public_site_url)

    @router.get(
        "/robots.txt", response_class=PlainTextResponse, include_in_schema=False
    )
    def robots() -> str:
        return (
            "User-agent: *\nAllow: /\nDisallow: /dashboard\nSitemap: "
            + public_site_url.rstrip("/")
            + "/sitemap.xml\n"
        )

    @router.get("/sitemap.xml", include_in_schema=False)
    def sitemap() -> Response:
        base = public_site_url.rstrip("/")
        paths = (
            "",
            "/cerca",
            "/politici",
            "/votazioni",
            "/gruppi",
            "/dati",
            "/docs",
            "/docs/api",
            "/docs/mcp",
            "/privacy",
        )
        urls = "".join(f"<url><loc>{base}{path}</loc></url>" for path in paths)
        return Response(
            f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
            media_type="application/xml",
        )

    return router


async def _web_query(method: Any, *args: Any, **kwargs: Any) -> tuple[Any, str | None]:
    try:
        return await run_in_threadpool(method, *args, **kwargs), None
    except (RuntimeError, ValueError):
        return None, "I dati non sono ancora disponibili. Riprova tra poco."


def _browser_session(
    request: Request, auth_service: AuthServiceContract
) -> tuple[Any | None, str | None]:
    raw_session = request.cookies.get("policy_session")
    raw_csrf = request.cookies.get("policy_csrf")
    if not raw_session or not raw_csrf:
        return None, None
    session = auth_service.validate_session(raw_session)
    if session is None or not auth_service.verify_csrf(session.session_id, raw_csrf):
        return None, None
    return session, raw_csrf


def _source_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _llms_text(public_site_url: str) -> str:
    base = public_site_url.rstrip("/")
    return f"""# Policy Data Italia
> Dati pubblici sui voti parlamentari italiani — Camera e Senato, XIX legislatura.

Auth: Bearer API key ottenuta nel dashboard. La stessa chiave vale per REST e MCP. Per i dati, usa API, MCP o download; non fare scrape dell'HTML.

## Docs
- [Documentazione]({base}/docs): panoramica e accesso
- [API]({base}/docs/api): REST e OpenAPI
- [MCP]({base}/docs/mcp): server MCP remoto per agenti

## Machine-readable
- [OpenAPI JSON]({base}/openapi.json): contratto REST
- [MCP endpoint]({base}/mcp): Streamable HTTP, Bearer richiesto
- [Health]({base}/health): stato e release attiva
- [Manifest]({base}/releases/current/manifest.json): file, checksum e licenze
- [Sitemap]({base}/sitemap.xml): pagine pubbliche
- [robots.txt]({base}/robots.txt): regole di crawl

## Copertura
- Tutte le votazioni nominali/elettroniche d'Aula pubblicate dalle fonti ufficiali incluse nella release.
- Voti segreti e posizioni non pubblicate restano esplicitamente indisponibili.
- Le candidature e le circoscrizioni non sono ancora incluse; il modello persona è pronto per collegarle.
"""
