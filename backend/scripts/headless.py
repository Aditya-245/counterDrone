"""Run one scenario headlessly and print its entity paths.

Thin wrapper around `app.core.headless` so `make headless` works the same way as
every other script in this directory (the package lives under `backend/`, which is
not on the path when Python is started from the repository root).
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.headless import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
