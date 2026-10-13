"""SKYGYM FastAPI application.

The server is authoritative for every live session: it steps the deterministic
engine at a fixed 10 Hz, broadcasts ``state_frame`` messages over
``/ws/session/{id}`` and applies ``TraineeAction`` messages at tick boundaries.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings

logger = logging.getLogger("skygym")

_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise storage and optional subsystems on startup."""
    from app.db.session import init_db

    init_db()
    logger.info("SKYGYM started; database at %s", _settings.db_url)

    from app.core.redbrain import describe_policy_availability

    state = describe_policy_availability()
    logger.info("Red Brain Layer B learned policy: %s", state)
    app.state.redbrain_policy = state

    yield


def create_app() -> FastAPI:
    """Build and configure the ASGI application."""
    app = FastAPI(
        title="SKYGYM",
        version=_settings.version,
        description=(
            "AI-enabled drone & counter-drone threat simulation trainer. "
            "A decision-quality gym, not a shooting game."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers are imported lazily inside create_app so that a bare `app.main`
    # import never touches the database (useful for pure-core unit tests).
    from app.api.health import router as health_router

    app.include_router(health_router)

    try:  # pragma: no cover - import guard for partial builds
        from app.api.analytics import router as analytics_router
        from app.api.aar import router as aar_router
        from app.api.builder import router as builder_router
        from app.api.instructor import router as instructor_router
        from app.api.scenarios import router as scenarios_router
        from app.api.sessions import router as sessions_router
        from app.api.users import router as users_router

        for r in (
            scenarios_router,
            sessions_router,
            aar_router,
            analytics_router,
            users_router,
            builder_router,
            instructor_router,
        ):
            app.include_router(r)
    except ImportError as exc:  # pragma: no cover
        logger.warning("API routers unavailable at startup: %s", exc)

    try:  # pragma: no cover - optional routers
        from app.api.teams import router as teams_router
        from app.api.realtime import router as realtime_router

        app.include_router(realtime_router)
        app.include_router(teams_router)
    except ImportError as exc:
        logger.info("Optional realtime/team routers not loaded: %s", exc)

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built frontend when `frontend/dist` exists (production)."""
    dist: Path = _settings.frontend_dist
    if not (_settings.serve_frontend and dist.is_dir()):
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> JSONResponse | FileResponse:
        """Single-page-application fallback."""
        if full_path.startswith(("api/", "ws/")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()