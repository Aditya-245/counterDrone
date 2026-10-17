"""SKYGYM FastAPI application.

The server is authoritative for every live session: it steps the deterministic
engine at a fixed 10 Hz, broadcasts ``state_frame`` messages over
``/ws/session/{id}`` and applies ``TraineeAction`` messages at tick boundaries.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings

logger = logging.getLogger("skygym")

_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise storage, start the simulation clock, and shut down cleanly."""
    from app.clock import run_session_clock
    from app.db.session import init_db

    init_db()
    logger.info("SKYGYM started; database at %s", get_settings().db_url)

    from app.core.redbrain import describe_policy_availability

    state = describe_policy_availability()
    logger.info("Red Brain Layer B learned policy: %s", state)
    app.state.redbrain_policy = state

    clock = asyncio.create_task(run_session_clock())
    app.state.clock_task = clock
    try:
        yield
    finally:
        clock.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await clock


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

    from app.middleware import install_schema_version_gate

    install_schema_version_gate(app)

    # Each router is imported independently: a missing optional module must not
    # take the required ones (notably the realtime socket) down with it.
    optional = {
        "app.api.health": "health",
        "app.api.analytics": "analytics",
        "app.api.aar": "aar",
        "app.api.builder": "builder",
        "app.api.instructor": "instructor",
        "app.api.scenarios": "scenarios",
        "app.api.sessions": "sessions",
        "app.api.users": "users",
        "app.api.realtime": "realtime",
        "app.api.teams": "teams",
    }
    import importlib

    for module_path in optional:
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:  # pragma: no cover - partial build
            logger.warning("router %s unavailable: %s", module_path, exc)
            continue
        router = getattr(module, "router", None)
        if router is not None:
            app.include_router(router)

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
