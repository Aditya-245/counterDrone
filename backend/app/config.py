"""SKYGYM application settings.

Everything is overridable with environment variables prefixed ``SKYGYM_`` so the
app can run fully offline with zero configuration.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    """Runtime configuration."""

    model_config = SettingsConfigDict(env_prefix="SKYGYM_", extra="ignore")

    app_name: str = "SKYGYM"
    version: str = "0.1.0"

    # --- storage ---------------------------------------------------------
    db_path: Path = BACKEND_DIR / "data" / "skygym.db"
    db_url_override: str | None = None

    # --- simulation ------------------------------------------------------
    tick_hz: int = 10
    tick_seconds: float = 0.1
    max_session_seconds: int = 3600

    # --- realtime --------------------------------------------------------
    ws_heartbeat_seconds: float = 15.0
    ws_send_interval_ticks: int = 1

    # --- red brain layer B (optional) ------------------------------------
    swarm_policy_path: Path = BACKEND_DIR / "data" / "swarm_ppo_policy.zip"
    swarm_policy_enabled: bool = True

    # --- CORS (dev only; Vite proxies /api and /ws in development) -------
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ]
    )

    # --- frontend production mount ---------------------------------------
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"
    serve_frontend: bool = True

    @property
    def db_url(self) -> str:
        """SQLAlchemy URL for the SQLite database."""
        if self.db_url_override:
            return self.db_url_override
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.db_path.as_posix()}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached process-wide settings object."""
    return Settings()
