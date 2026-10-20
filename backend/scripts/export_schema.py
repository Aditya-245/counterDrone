"""Export every wire model's JSON Schema into `docs/schema/`.

Thin wrapper around `app.core.schema_export` so `make schemas` works the same way
as every other script in this directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.schema_export import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
