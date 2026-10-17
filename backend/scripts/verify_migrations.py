"""Ad-hoc check: the Alembic migration must build the same schema as create_all."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

MIGRATED = REPO / "backend" / "data" / "migrations_tmp" / "empty.db"
CREATED = REPO / "backend" / "data" / "migrations_tmp" / "created.db"


def tables(db: Path) -> set[str]:
    """Every table in `db`, excluding SQLite internals."""
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        con.close()
    return {r[0] for r in rows if not r[0].startswith("sqlite_") and r[0] != "alembic_version"}


def columns(db: Path, table: str) -> set[tuple[str, str, int, int]]:
    """`(name, type, notnull, default)` for every column of `table`."""
    con = sqlite3.connect(db)
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        con.close()
    return {(r[1], r[2], r[3], -1 if r[4] is None else 1) for r in rows}


def indexes(db: Path, table: str) -> set[str]:
    """Index names on `table`."""
    con = sqlite3.connect(db)
    try:
        rows = con.execute(f"PRAGMA index_list({table})").fetchall()
    finally:
        con.close()
    return {r[1] for r in rows if not r[1].startswith("sqlite_autoindex")}


def main() -> int:
    """Compare the migrated schema against `Base.metadata.create_all`."""
    from app.config import get_settings
    from app.db.models import Base
    from app.db.session import get_engine, reset_engine

    settings = get_settings()
    settings.db_path = CREATED
    settings.db_url_override = f"sqlite:///{CREATED.as_posix()}"
    CREATED.parent.mkdir(parents=True, exist_ok=True)
    if CREATED.exists():
        CREATED.unlink()
    reset_engine()
    Base.metadata.create_all(get_engine())

    mig = tables(MIGRATED)
    cre = tables(CREATED)
    problems: list[str] = []
    if mig != cre:
        problems.append(
            f"table mismatch: only-migrated={sorted(mig - cre)} only-created={sorted(cre - mig)}"
        )

    for t in sorted(mig & cre):
        cm, cc = columns(MIGRATED, t), columns(CREATED, t)
        if cm != cc:
            problems.append(
                f"{t}: column mismatch\n  migrated: {sorted(cm)}\n  created : {sorted(cc)}"
            )
        im, ic = indexes(MIGRATED, t), indexes(CREATED, t)
        if im != ic:
            problems.append(f"{t}: index mismatch migrated={sorted(im)} created={sorted(ic)}")

    if problems:
        print("FAIL")
        for p in problems:
            print(" -", p)
        return 1
    print(f"OK: migration reproduces create_all exactly ({len(mig)} tables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
