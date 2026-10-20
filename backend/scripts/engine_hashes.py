"""Print the state hash of every shipped scenario, as the server computes it.

The browser runs the same `app.core` under Pyodide, so the hash it produces
must be identical to this one. `frontend/scripts/verify-engine.mjs` checks
exactly that, which is what keeps the determinism contract true across the two
runtimes.

    python backend/scripts/engine_hashes.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.engine import SimEngine  # noqa: E402
from app.core.schemas import Scenario  # noqa: E402

SCENARIO_DIR = BACKEND_DIR.parent / "scenarios"


def hashes() -> dict[str, str]:
    """The state hash of every shipped scenario, played at the canonical 10 Hz."""
    out: dict[str, str] = {}
    for path in sorted(SCENARIO_DIR.glob("*.json")):
        scenario = Scenario.model_validate(json.loads(path.read_text(encoding="utf-8")))
        engine = SimEngine(scenario, dt=0.1)
        while not engine.finished:
            engine.tick()
        out[scenario.id] = engine.state_hash()
    return out


def main() -> int:
    """Print the hashes as JSON."""
    print(json.dumps(hashes(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
