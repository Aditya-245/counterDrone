"""Database engine / session plumbing.

Single SQLite file, WAL journaling, one guarded session for writes (see
DECISIONS.md D017).
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base

# NOTE: settings are resolved lazily inside `get_engine()` rather than cached at
# import time, so tests can point the app at a throwaway database by clearing the
# settings cache and calling `reset_engine()`.

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
            get_settings().db_url,
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


def prepare_runtime_db() -> bool:
    """Put the pre-built seed snapshot in place when the database is missing.

    A serverless instance has no writable filesystem except ``/tmp``, and every
    instance gets its own copy of it, so the application cannot keep a
    database file between requests. The build ships a seeded snapshot which is
    copied into place here. Returns True when a snapshot was used.

    Nothing happens when the database already exists (the normal case for a
    local run) or when no snapshot was shipped: the caller then seeds the
    library itself.
    """
    settings = get_settings()
    target = Path(settings.db_path)
    if target.exists():
        return False
    snapshot = Path(settings.seed_snapshot_path)
    if not snapshot.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(snapshot, target)
    return True


def init_db() -> None:
    """Create the data directory and all tables (idempotent).

    Two schema paths are supported and they must never fight:

    * **Alembic** (`make migrate`) is the way an existing database evolves.
    * **`create_all`** is the zero-setup path used by the tests, by `make seed`
      on a fresh checkout, and by the container-less demo.

    Whichever one builds the schema first stamps the Alembic revision table, so
    a later `make migrate` sees the database as already-current instead of
    trying to re-create tables that are already there (DECISIONS D020).
    """
    Path(get_settings().db_path).parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(get_engine())
    _stamp_alembic_head()


def _stamp_alembic_head() -> None:
    """Record the current Alembic revision if the DB has no version marker.

    Uses Alembic's own `stamp`, which creates the `alembic_version` table when
    it is missing. Any failure is swallowed: the database is perfectly usable
    without a revision marker, and a broken ini file must not stop the app.
    """
    from alembic import command
    from alembic.config import Config

    cfg_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    if not cfg_path.exists():
        return
    cfg = Config(str(cfg_path))
    cfg.set_main_option("sqlalchemy.url", get_settings().db_url)
    with suppress(Exception):  # pragma: no cover - alembic is optional at runtime
        command.stamp(cfg, "head")


def alembic_heads() -> list[str]:
    """Revision ids currently at the head of the migration chain on disk."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    if not cfg_path.exists():
        return []
    cfg = Config(str(cfg_path))
    cfg.set_main_option("sqlalchemy.url", get_settings().db_url)
    return list(ScriptDirectory.from_config(cfg).get_heads())


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
    base = Path(get_settings().db_path)
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
