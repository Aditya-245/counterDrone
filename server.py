"""Vercel entrypoint for the SKYGYM backend.

Vercel's Python runtime loads the ASGI application from a file called
``app.py``, ``index.py``, ``server.py``, ``main.py``, ``wsgi.py`` or
``asgi.py`` in the project root. The application itself lives in
``backend/app``, so this file only puts ``backend/`` on the import path and
re-exports the app under the name Vercel looks for.

Everything else - the API, the WebSocket, the simulation clock and the built
frontend - is served by that one function, so the browser only ever talks to
one origin.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402

__all__ = ["app"]
