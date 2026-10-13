"""Database engine / session plumbing.

Single SQLite file, WAL journaling, one guarded session for writes (see
DECISIONS.md D017).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base

_settings = get_settings()

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
_write_lock = threading.Lock()


def _configure_sqlite(dbapi_connection: object, _record: object) -> None:
    """Enable foreign keys and WAL on every new SQLite connection."""
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            _settings.db_url,
            future=True,
            connect_args={"check_same_thread": False},
        )
        event.listen(_engine, "connect", _configure_sqlite)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), future=True, expire_on_commit=False)
    return _SessionLocal


def init_db() -> None:
    """Create the data directory and all tables (idempotent)."""
    Path(_settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope; commits on success, rolls back on error."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def write_scope() -> Iterator[Session]:
    """Serialised write scope used by the WebSocket handler (DECISIONS D017)."""
    with _write_lock, session_scope() as session:
        yield session


def drop_db() -> None:
    """Delete the SQLite file and every journal companion. Used by scripts."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
        _engine = None
    _SessionLocal = None
    base = Path(_settings.db_path)
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(f"{base}{suffix}")
        if candidate.exists():
            candidate.unlink()


def reset_engine() -> None:
    """Drop cached engine/session factory (used after a database file swap)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None