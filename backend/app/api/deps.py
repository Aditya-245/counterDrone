"""Small helpers shared by the REST routers: consistent error responses."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from app.core.schemas import SCHEMA_VERSION

__all__ = ["not_found", "unprocessable"]


def not_found(kind: str, identifier: str) -> HTTPException:
    """A consistent 404 for every router."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"detail": f"{kind} '{identifier}' not found", "code": "not_found"},
    )


def unprocessable(reason: str, **extra: Any) -> HTTPException:
    """A consistent 422 for validation failures."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "detail": reason,
            "code": "unprocessable",
            "expected_schema_version": SCHEMA_VERSION,
            **extra,
        },
    )
