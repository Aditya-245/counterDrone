"""Shared pytest fixtures and helpers for the SKYGYM backend test suite."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("SKYGYM_SERVE_FRONTEND", "false")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Repository root directory."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def scenario_dir() -> Path:
    """Directory holding the shipped scenario JSON files."""
    return REPO_ROOT / "scenarios"


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the app settings at a throwaway SQLite file for this test."""
    db_path = tmp_path / "skygym-test.db"
    monkeypatch.setenv("SKYGYM_DB_PATH", str(db_path))
    from app.config import get_settings
    from app.db import session as db_session

    get_settings.cache_clear()
    db_session.reset_engine()
    yield db_path
    get_settings.cache_clear()
    db_session.reset_engine()