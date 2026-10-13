"""Pure simulation and scoring core.

Nothing in this package may import `app.db`, `app.api`, `fastapi`, or perform
I/O. Everything here is deterministic and side-effect free apart from the
`numpy.random.Generator` explicitly passed in.
"""

from __future__ import annotations