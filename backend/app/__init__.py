"""SKYGYM backend application package.

`app.core` is pure simulation + scoring logic with no I/O whatsoever; everything
in `app.api`, `app.db` and `app.main` is allowed to touch the outside world.
"""

from __future__ import annotations

__version__ = "0.1.0"