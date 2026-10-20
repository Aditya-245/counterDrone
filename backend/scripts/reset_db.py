"""Delete the SQLite database and rebuild an empty schema.

Run with ``make reset-db``. The file is removed (with its WAL sidecars) and then
recreated empty, so the next ``make seed`` starts from a known state.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.db.session import init_db  # noqa: E402


def main() -> int:
    """Remove the database file and recreate the schema."""
    db_path = Path(get_settings().db_path)
    for suffix in ("", "-wal", "-shm"):
        target = Path(f"{db_path}{suffix}")
        if target.exists():
            target.unlink()
            print(f"  removed {target}")
    init_db()
    print(f"  recreated {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
