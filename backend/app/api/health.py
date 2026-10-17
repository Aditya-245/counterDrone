"""Liveness and capability probe endpoint."""

from __future__ import annotations

import platform
import sys
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import get_settings

router = APIRouter(prefix="/api", tags=["health"])


class HealthResponse(BaseModel):
    """Payload returned by `GET /api/health`."""

    status: str = Field(description="'ok' when the backend is serving")
    app: str
    version: str
    schema_version: int
    python: str
    platform: str
    tick_hz: int
    database: str
    db_reachable: bool
    llm_available: bool
    learned_policy: dict[str, Any]


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report backend health and optional-subsystem availability."""
    from app.core.redbrain import describe_policy_availability
    from app.core.schemas import SCHEMA_VERSION
    from app.db.session import get_engine

    settings = get_settings()
    reachable = True
    try:
        from sqlalchemy import text

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover - only on a broken database
        reachable = False

    llm_available = False
    try:  # pragma: no cover - network probing is environment dependent
        from app.core.nl_builder import ollama_available

        llm_available = ollama_available()
    except Exception:
        llm_available = False

    return HealthResponse(
        status="ok" if reachable else "degraded",
        app=settings.app_name,
        version=settings.version,
        schema_version=SCHEMA_VERSION,
        python=sys.version.split()[0],
        platform=f"{platform.system()} {platform.release()}",
        tick_hz=settings.tick_hz,
        database=str(settings.db_path),
        db_reachable=reachable,
        llm_available=llm_available,
        learned_policy=describe_policy_availability(),
    )
