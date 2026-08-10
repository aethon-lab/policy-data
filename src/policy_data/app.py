import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Protocol

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from policy_data.api.errors import problem
from policy_data.api.schemas import HealthResponse, VoterPageResponse, VoterResponse
from policy_data.domain.enums import ChamberCode, VotePosition
from policy_data.ingest.publish import read_active_release
from policy_data.mcp.server import authenticated_mcp_app, create_mcp_server
from policy_data.query.filters import VoteQuery
from policy_data.query.results import VoterPage
from policy_data.web.routes import create_web_router


class QueryServiceContract(Protocol):
    def find_voters(
        self, query: VoteQuery, *, limit: int = 50, cursor: str | None = None
    ) -> VoterPage: ...


class AuthServiceContract(Protocol):
    def authenticate_api_key(self, raw: str) -> object | None: ...


def create_app(
    query_service: QueryServiceContract,
    auth_service: AuthServiceContract,
    *,
    release_root: Path,
    enable_mcp: bool = True,
    public_site_url: str = "http://localhost:8000",
) -> FastAPI:
    mcp_server = create_mcp_server(query_service) if enable_mcp else None
    mcp_app = authenticated_mcp_app(mcp_server, auth_service) if mcp_server else None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if mcp_server is None:
            yield
            return
        async with mcp_server.session_manager.run():
            yield

    app = FastAPI(
        title="Policy Data Italia API",
        version="0.1.0",
        openapi_version="3.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    bearer = HTTPBearer(auto_error=False, scheme_name="ApiKeyBearer")
    static_root = Path(__file__).parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=static_root), name="static")
    app.include_router(
        create_web_router(
            query_service,
            auth_service,  # type: ignore[arg-type]
            public_site_url=public_site_url,
        )
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def require_api_key(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> object | JSONResponse:
        if credentials is None:
            return problem(
                401,
                "missing-api-key",
                "API key required",
                "Send the API key as a Bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        principal = auth_service.authenticate_api_key(credentials.credentials)
        if principal is None:
            return problem(
                401,
                "invalid-api-key",
                "Invalid API key",
                "The API key is invalid or revoked.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return principal

    @app.get(
        "/health",
        response_model=HealthResponse,
        operation_id="getHealth",
        tags=["public"],
    )
    def health() -> HealthResponse:
        return HealthResponse(status="ok", release_id=read_active_release(release_root))

    @app.get(
        "/api/v1/voters",
        response_model=VoterPageResponse,
        operation_id="findVoters",
        tags=["votes"],
        responses={401: {"content": {"application/problem+json": {}}}},
    )
    async def find_voters(
        principal: Annotated[object | JSONResponse, Depends(require_api_key)],
        text: Annotated[str | None, Query(max_length=200)] = None,
        position: VotePosition | None = None,
        chamber: ChamberCode | None = None,
        legislature: Annotated[int | None, Query(gt=0)] = None,
        group_id: str | None = None,
        person_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
    ) -> VoterPageResponse | JSONResponse:
        if isinstance(principal, JSONResponse):
            return principal
        page = await run_in_threadpool(
            query_service.find_voters,
            VoteQuery(text, position, chamber, legislature, group_id, person_id),
            limit=limit,
            cursor=cursor,
        )
        return VoterPageResponse(
            items=[VoterResponse.model_validate(item) for item in page.items],
            release_id=page.release_id,
            next_cursor=page.next_cursor,
        )

    @app.get(
        "/releases/current/manifest.json",
        operation_id="getCurrentManifest",
        tags=["public"],
    )
    def current_manifest() -> JSONResponse:
        release_id = read_active_release(release_root)
        if release_id is None:
            return problem(
                503, "no-release", "No data release", "No release is active."
            )
        body = _read_manifest(release_root, release_id)
        return JSONResponse(
            body,
            headers={"Cache-Control": "public, max-age=60, must-revalidate"},
        )

    @app.get(
        "/releases/{release_id}/{filename}",
        operation_id="downloadReleaseFile",
        tags=["public"],
        response_model=None,
    )
    def download(release_id: str, filename: str) -> FileResponse | JSONResponse:
        if (
            not release_id.startswith("release-")
            or not release_id.replace("-", "").isalnum()
            or "/" in filename
            or "\\" in filename
            or filename in {".", ".."}
        ):
            return problem(404, "not-found", "Not found", "Release file not found.")
        manifest = _read_manifest(release_root, release_id, missing_ok=True)
        if manifest is None:
            return problem(404, "not-found", "Not found", "Release file not found.")
        entry = next(
            (
                item
                for item in manifest.get("files", [])
                if item.get("filename") == filename
            ),
            None,
        )
        path = release_root / "releases" / release_id / filename
        if (
            entry is None
            or not path.is_file()
            or path.is_symlink()
            or path.stat().st_nlink != 1
        ):
            return problem(404, "not-found", "Not found", "Release file not found.")
        return FileResponse(
            path,
            media_type="application/gzip"
            if filename.endswith(".gz")
            else entry["media_type"],
            filename=filename,
            headers={
                "ETag": f'"{entry["sha256"]}"',
                "Cache-Control": "public, max-age=31536000, immutable",
                "X-Source-Publisher": entry.get("publisher") or "",
                "X-Source-License": entry.get("license_id") or "",
            },
        )

    if mcp_app is not None:
        app.mount("/", mcp_app, name="mcp")
    return app


def _read_manifest(
    release_root: Path, release_id: str, *, missing_ok: bool = False
) -> dict[str, Any] | None:
    path = release_root / "releases" / release_id / "manifest.json"
    if not path.is_file() or path.is_symlink():
        if missing_ok:
            return None
        raise RuntimeError("active release manifest is unavailable")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("release_id") != release_id:
        raise RuntimeError("release manifest is invalid")
    return value
