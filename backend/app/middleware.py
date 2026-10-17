"""Cross-cutting middleware: the schema-version gate.

This is middleware rather than a router dependency on purpose: FastAPI validates
request bodies *while* solving dependencies, so a dependency-based version gate
loses the race against its own model's validation and produces a generic 422
instead of the machine-readable error the API contract promises.

Everything here runs before routing, so the version check is genuinely the first
thing that sees a payload (DECISIONS D002).
"""

from __future__ import annotations

import json
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.schemas import SCHEMA_VERSION

__all__ = ["SchemaVersionMiddleware", "install_schema_version_gate"]


def _error(status: int, code: str, message: str, received: Any = None) -> JSONResponse:
    """A machine-readable API error.

    `detail` is the structured object (FastAPI's `HTTPException(detail={...})`
    convention) so clients can branch on `detail.code` without parsing prose.
    """
    detail: dict[str, Any] = {
        "code": code,
        "message": message,
        "expected_schema_version": SCHEMA_VERSION,
    }
    if received is not None:
        detail["received_schema_version"] = received
    return JSONResponse({"detail": detail}, status_code=status)


class SchemaVersionMiddleware(BaseHTTPMiddleware):
    """Reject any JSON body whose ``schema_version`` this build cannot read."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Check the body version, then replay the body for the handler."""
        if request.method in ("GET", "HEAD", "OPTIONS") or not request.url.path.startswith(
            ("/api/", "/ws/")
        ):
            return await call_next(request)
        if "application/json" not in request.headers.get("content-type", ""):
            return await call_next(request)

        raw = await request.body()
        if not raw:
            return await call_next(request)

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            return _error(422, "invalid_json", f"body is not valid JSON: {exc}")

        if not isinstance(payload, dict):
            return await call_next(request)

        version = payload.get("schema_version")
        if version is not None and version != SCHEMA_VERSION:
            return _error(
                422,
                "unsupported_schema_version",
                f"unsupported schema_version {version!r}; "
                f"this build understands {SCHEMA_VERSION}",
                received=version,
            )

        async def replay() -> dict[str, Any]:
            return {"type": "http.request", "body": raw, "more_body": False}

        request._receive = replay  # type: ignore[attr-defined]
        return await call_next(request)


def install_schema_version_gate(app: Any) -> None:
    """Attach the gate to a FastAPI application."""
    app.add_middleware(SchemaVersionMiddleware)
