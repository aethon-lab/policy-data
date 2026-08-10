from __future__ import annotations

from fastapi.responses import JSONResponse


def problem(
    status: int,
    problem_type: str,
    title: str,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        headers=headers,
        content={
            "type": f"https://policydata.it/problems/{problem_type}",
            "title": title,
            "status": status,
            "detail": detail,
        },
    )
