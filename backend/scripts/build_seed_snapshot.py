"""Build the pre-seeded database snapshot the serverless deployment starts from.

A serverless instance has no writable filesystem except ``/tmp``, and every
instance gets its own copy of it, so the deployed application cannot keep a
database file between requests. Instead the build seeds a complete database -
shipped scenarios, demo roster and the synthetic session history - and the
application copies that snapshot into place on start-up.

The snapshot is a plain SQLite file with the write-ahead log folded into it,
so copying it is a single file operation and the copy is consistent.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

SNAPSHOT = BACKEND_DIR / "data" / "seed_snapshot.db"


def main() -> int:
    """Seed the snapshot database and fold its write-ahead log into it."""
    os.environ["SKYGYM_DB_PATH"] = str(SNAPSHOT)

    from app.config import get_settings

    get_settings.cache_clear()

    from app.db.session import get_engine, init_db, reset_engine
    from scripts.seed import load_shipped, prune_scenarios, seed_demo_unit, seed_scenarios

    for suffix in ("", "-wal", "-shm"):
        stale = Path(f"{SNAPSHOT}{suffix}")
        if stale.exists():
            stale.unlink()

    init_db()
    scenarios = load_shipped()
    seed_scenarios(scenarios)
    retired = prune_scenarios({scenario.id for scenario, _ in scenarios})
    created, expected = seed_demo_unit(scenarios)
    print(f"  snapshot  : {len(scenarios)} scenarios, {created} synthetic sessions simulated")
    if retired:
        print(f"  retired   : {len(retired)} scenario(s)")
    print(f"  history   : {expected - created} session(s) already present")

    # Fold the WAL into the main file so the snapshot is one self-contained
    # file, then drop the journal companions.
    with get_engine().connect() as connection:
        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    reset_engine()
    for suffix in ("-wal", "-shm"):
        journal = Path(f"{SNAPSHOT}{suffix}")
        if journal.exists():
            journal.unlink()

    size_mb = SNAPSHOT.stat().st_size / (1024 * 1024)
    print(f"  written   : {SNAPSHOT} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
