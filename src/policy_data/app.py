import json
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Awaitable, Callable, Protocol

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from policy_data.api.errors import problem
from policy_data.api.schemas import (
    CanonicalPageResponse,
    CanonicalRecordResponse,
    HealthResponse,
    VoterPageResponse,
    VoterResponse,
)
from policy_data.domain.enums import ChamberCode, VotePosition
from policy_data.ingest.publish import read_active_release
from policy_data.mcp.server import authenticated_mcp_app, create_mcp_server
from policy_data.query.filters import VoteQuery
from policy_data.query.pagination import InvalidCursor
from policy_data.query.results import CanonicalPage, CanonicalRecord, VoterPage
from policy_data.query.service import NoActiveRelease, QueryTimeout
from policy_data.web.routes import create_web_router


class QueryServiceContract(Protocol):
    def find_voters(
        self, query: VoteQuery, *, limit: int = 25, cursor: str | None = None
    ) -> VoterPage: ...

    def list_legislatures(self, *, limit: int, cursor: str | None) -> CanonicalPage: ...
    def list_people(
        self, *, text: str | None, limit: int, cursor: str | None
    ) -> CanonicalPage: ...
    def get_person(self, person_id: str) -> CanonicalRecord | None: ...
    def list_person_votes(
        self, person_id: str, *, limit: int, cursor: str | None
    ) -> VoterPage: ...
    def list_groups(
        self,
        *,
        legislature: int | None,
        chamber: ChamberCode | None,
        limit: int,
        cursor: str | None,
    ) -> CanonicalPage: ...
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
    def list_disclosures(
        self, *, person_id: str | None, limit: int, cursor: str | None
    ) -> CanonicalPage: ...
    def dataset_status(self) -> CanonicalRecord: ...


class AuthServiceContract(Protocol):
    def authenticate_api_key(self, raw: str) -> object | None: ...

    def authorize_data_request(self, principal: object, *, source_ip: str) -> bool: ...


class RequestBodyLimitMiddleware:
    """Reject oversized HTTP bodies before form, JSON, or MCP parsing."""

    def __init__(self, app: ASGIApp, max_bytes: int = 65_536) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        try:
            declared = int(raw_length) if raw_length is not None else None
        except ValueError:
            declared = self.max_bytes + 1
        if declared is not None and declared > self.max_bytes:
            await self._reject(send)
            return
        received = 0
        buffered: list[Message] = []
        more_body = True
        while more_body:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break
            received += len(message.get("body", b""))
            if received > self.max_bytes:
                await self._reject(send)
                return
            more_body = bool(message.get("more_body", False))
        messages = iter(buffered)

        async def replay_receive() -> Message:
            try:
                return next(messages)
            except StopIteration:
                return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = json.dumps(
            {
                "type": "https://policydata.it/problems/request-too-large",
                "title": "Request body too large",
                "status": 413,
                "detail": "Request bodies cannot exceed 65536 bytes.",
            },
            separators=(",", ":"),
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/problem+json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def create_app(
    query_service: QueryServiceContract,
    auth_service: AuthServiceContract,
    *,
    release_root: Path,
    enable_mcp: bool = True,
    public_site_url: str = "http://localhost:8000",
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver"),
    allowed_origins: tuple[str, ...] = ("http://localhost:8000",),
    shutdown: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    mcp_server = create_mcp_server(query_service) if enable_mcp else None
    mcp_app = authenticated_mcp_app(mcp_server, auth_service) if mcp_server else None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            if mcp_server is None:
                yield
            else:
                async with mcp_server.session_manager.run():
                    yield
        finally:
            if shutdown is not None:
                await shutdown()

    app = FastAPI(
        title="Policy Data Italia API",
        version="0.1.0",
        openapi_version="3.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Mcp-Session-Id"],
    )
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=65_536)
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

    @app.exception_handler(InvalidCursor)
    async def invalid_cursor_handler(_: Request, error: InvalidCursor) -> JSONResponse:
        return problem(400, "invalid-cursor", "Invalid cursor", str(error))

    @app.exception_handler(QueryTimeout)
    async def query_timeout_handler(_: Request, error: QueryTimeout) -> JSONResponse:
        return problem(504, "query-timeout", "Query timed out", str(error))

    @app.exception_handler(NoActiveRelease)
    async def no_release_handler(_: Request, error: NoActiveRelease) -> JSONResponse:
        return problem(503, "no-release", "No data release", str(error))

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
        request: Request,
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
        try:
            principal = auth_service.authenticate_api_key(credentials.credentials)
        except Exception:
            return problem(
                503,
                "auth-unavailable",
                "Authentication unavailable",
                "Authentication state is temporarily unavailable.",
            )
        if principal is None:
            return problem(
                401,
                "invalid-api-key",
                "Invalid API key",
                "The API key is invalid or revoked.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            allowed = auth_service.authorize_data_request(
                principal,
                source_ip=request.client.host if request.client else "unknown",
            )
        except Exception:
            return problem(
                503,
                "rate-limit-unavailable",
                "Rate limit unavailable",
                "Protected access is temporarily unavailable.",
            )
        if not allowed:
            return problem(
                429,
                "rate-limited",
                "Rate limit exceeded",
                "Wait before making another protected request.",
                headers={"Retry-After": "60"},
            )
        return principal

    @app.get(
        "/health",
        response_model=HealthResponse,
        operation_id="getHealth",
        tags=["public"],
    )
    def health() -> HealthResponse:
        release_id = read_active_release(release_root)
        return HealthResponse(
            status="ok" if release_id is not None else "degraded",
            release_id=release_id,
        )

    @app.get(
        "/ready", operation_id="getReadiness", tags=["public"], response_model=None
    )
    def readiness() -> HealthResponse | JSONResponse:
        release_id = read_active_release(release_root)
        if release_id is None:
            return problem(
                503, "no-release", "No data release", "No release is active."
            )
        try:
            manifest = _read_manifest(release_root, release_id, missing_ok=True)
        except (OSError, RuntimeError, ValueError):
            manifest = None
        if manifest is None:
            return problem(
                503,
                "release-unavailable",
                "Release unavailable",
                "The active release is incomplete.",
            )
        return HealthResponse(status="ready", release_id=release_id)

    @app.get(
        "/api/v1/voters",
        response_model=VoterPageResponse,
        operation_id="findVoters",
        tags=["votes"],
        responses={
            400: {"content": {"application/problem+json": {}}},
            401: {"content": {"application/problem+json": {}}},
            429: {"content": {"application/problem+json": {}}},
            503: {"content": {"application/problem+json": {}}},
            504: {"content": {"application/problem+json": {}}},
        },
    )
    async def find_voters(
        principal: Annotated[object | JSONResponse, Depends(require_api_key)],
        text: Annotated[str | None, Query(max_length=200)] = None,
        position: VotePosition | None = None,
        chamber: ChamberCode | None = None,
        legislature: Annotated[int | None, Query(gt=0)] = None,
        group_id: str | None = None,
        person_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
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
            data_through=page.data_through,
            next_cursor=page.next_cursor,
        )

    def page_response(page: Any) -> CanonicalPageResponse:
        return CanonicalPageResponse(
            items=list(page.items),
            release_id=page.release_id,
            data_through=page.data_through,
            next_cursor=page.next_cursor,
        )

    def record_response(record: Any) -> CanonicalRecordResponse:
        return CanonicalRecordResponse(
            item=record.item,
            release_id=record.release_id,
            data_through=record.data_through,
        )

    async def run_collection(
        method: Callable[..., Any], **kwargs: Any
    ) -> CanonicalPageResponse:
        return page_response(await run_in_threadpool(method, **kwargs))

    common_errors: dict[int | str, dict[str, Any]] = {
        400: {"content": {"application/problem+json": {}}},
        401: {"content": {"application/problem+json": {}}},
        429: {"content": {"application/problem+json": {}}},
        503: {"content": {"application/problem+json": {}}},
        504: {"content": {"application/problem+json": {}}},
    }

    @app.get(
        "/api/v1/legislatures",
        response_model=CanonicalPageResponse,
        tags=["canonical"],
        responses=common_errors,
    )
    async def list_legislatures(
        principal: Annotated[object | JSONResponse, Depends(require_api_key)],
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
    ) -> CanonicalPageResponse | JSONResponse:
        if isinstance(principal, JSONResponse):
            return principal
        return await run_collection(
            query_service.list_legislatures, limit=limit, cursor=cursor
        )

    @app.get(
        "/api/v1/people",
        response_model=CanonicalPageResponse,
        tags=["canonical"],
        responses=common_errors,
    )
    async def list_people(
        principal: Annotated[object | JSONResponse, Depends(require_api_key)],
        text: Annotated[str | None, Query(max_length=200)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
    ) -> CanonicalPageResponse | JSONResponse:
        if isinstance(principal, JSONResponse):
            return principal
        return await run_collection(
            query_service.list_people, text=text, limit=limit, cursor=cursor
        )

    @app.get(
        "/api/v1/people/{person_id}",
        response_model=CanonicalRecordResponse,
        tags=["canonical"],
        responses=common_errors,
    )
    async def get_person(
        person_id: str,
        principal: Annotated[object | JSONResponse, Depends(require_api_key)],
    ) -> CanonicalRecordResponse | JSONResponse:
        if isinstance(principal, JSONResponse):
            return principal
        record = await run_in_threadpool(query_service.get_person, person_id)
        return (
            record_response(record)
            if record
            else problem(404, "not-found", "Not found", "Person not found.")
        )

    @app.get(
        "/api/v1/people/{person_id}/votes",
        response_model=VoterPageResponse,
        tags=["canonical"],
        responses=common_errors,
    )
    async def list_person_votes(
        person_id: str,
        principal: Annotated[object | JSONResponse, Depends(require_api_key)],
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
    ) -> VoterPageResponse | JSONResponse:
        if isinstance(principal, JSONResponse):
            return principal
        page = await run_in_threadpool(
            query_service.list_person_votes, person_id, limit=limit, cursor=cursor
        )
        return VoterPageResponse(
            items=[VoterResponse.model_validate(item) for item in page.items],
            release_id=page.release_id,
            data_through=page.data_through,
            next_cursor=page.next_cursor,
        )

    @app.get(
        "/api/v1/groups",
        response_model=CanonicalPageResponse,
        tags=["canonical"],
        responses=common_errors,
    )
    async def list_groups(
        principal: Annotated[object | JSONResponse, Depends(require_api_key)],
        legislature: Annotated[int | None, Query(gt=0)] = None,
        chamber: ChamberCode | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
    ) -> CanonicalPageResponse | JSONResponse:
        if isinstance(principal, JSONResponse):
            return principal
        return await run_collection(
            query_service.list_groups,
            legislature=legislature,
            chamber=chamber,
            limit=limit,
            cursor=cursor,
        )

    @app.get(
        "/api/v1/roll-calls",
        response_model=CanonicalPageResponse,
        tags=["canonical"],
        responses=common_errors,
    )
    async def list_roll_calls(
        principal: Annotated[object | JSONResponse, Depends(require_api_key)],
        text: Annotated[str | None, Query(max_length=200)] = None,
        legislature: Annotated[int | None, Query(gt=0)] = None,
        chamber: ChamberCode | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
    ) -> CanonicalPageResponse | JSONResponse:
        if isinstance(principal, JSONResponse):
            return principal
        return await run_collection(
            query_service.list_roll_calls,
            text=text,
            legislature=legislature,
            chamber=chamber,
            limit=limit,
            cursor=cursor,
        )

    @app.get(
        "/api/v1/roll-calls/{roll_call_id}",
        response_model=CanonicalRecordResponse,
        tags=["canonical"],
        responses=common_errors,
    )
    async def get_roll_call(
        roll_call_id: str,
        principal: Annotated[object | JSONResponse, Depends(require_api_key)],
    ) -> CanonicalRecordResponse | JSONResponse:
        if isinstance(principal, JSONResponse):
            return principal
        record = await run_in_threadpool(query_service.get_roll_call, roll_call_id)
        return (
            record_response(record)
            if record
            else problem(404, "not-found", "Not found", "Roll call not found.")
        )

    @app.get(
        "/api/v1/roll-calls/{roll_call_id}/positions",
        response_model=CanonicalPageResponse,
        tags=["canonical"],
        responses=common_errors,
    )
    async def list_roll_call_positions(
        roll_call_id: str,
        principal: Annotated[object | JSONResponse, Depends(require_api_key)],
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
    ) -> CanonicalPageResponse | JSONResponse:
        if isinstance(principal, JSONResponse):
            return principal
        return await run_collection(
            query_service.list_roll_call_positions,
            roll_call_id=roll_call_id,
            limit=limit,
            cursor=cursor,
        )

    @app.get(
        "/api/v1/disclosures",
        response_model=CanonicalPageResponse,
        tags=["canonical"],
        responses=common_errors,
    )
    async def list_disclosures(
        principal: Annotated[object | JSONResponse, Depends(require_api_key)],
        person_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
    ) -> CanonicalPageResponse | JSONResponse:
        if isinstance(principal, JSONResponse):
            return principal
        return await run_collection(
            query_service.list_disclosures,
            person_id=person_id,
            limit=limit,
            cursor=cursor,
        )

    @app.get(
        "/api/v1/dataset-status",
        response_model=CanonicalRecordResponse,
        tags=["canonical"],
        responses=common_errors,
    )
    async def dataset_status(
        principal: Annotated[object | JSONResponse, Depends(require_api_key)],
    ) -> CanonicalRecordResponse | JSONResponse:
        if isinstance(principal, JSONResponse):
            return principal
        return record_response(await run_in_threadpool(query_service.dataset_status))

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
        try:
            metadata = path.lstat()
        except OSError:
            metadata = None
        if (
            entry is None
            or metadata is None
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
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
