"""Verify every shipped scenario: schema, sanity, category tag and oracle score.

The shipped library is eight scenarios split four `rural` and four `city`. This
checks each one parses, passes the generator's sanity check, carries exactly one
category tag, and is still solvable by the oracle policy above the 85-point floor
the seeder enforces. A scenario that regresses here would otherwise only fail
later, inside `make seed`.

    .venv/Scripts/python backend/scripts/verify_scenarios.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.core.generator import sanity_check  # noqa: E402
from app.core.schemas import Scenario  # noqa: E402
from app.core.session import oracle_result  # noqa: E402

SCENARIO_DIR = REPO_ROOT / "scenarios"
MIN_ORACLE = 85.0
CATEGORIES = ("rural", "city")


def category_of(scenario: Scenario) -> str | None:
    """The single category facet a scenario carries, if any."""
    found = [c for c in CATEGORIES if c in scenario.tags]
    return found[0] if len(found) == 1 else None


def main() -> None:
    """Check every scenario file and report a table."""
    files = sorted(SCENARIO_DIR.glob("*.json"))
    if not files:
        raise SystemExit(f"no scenario files in {SCENARIO_DIR}")

    failures: list[str] = []
    counts: Counter[str] = Counter()
    print(f"{'scenario':<28} {'preset':<7} {'category':<9} {'oracle':>7}  verdict")
    print("-" * 72)

    for path in files:
        scenario = Scenario.model_validate(json.loads(path.read_text(encoding="utf-8")))
        sanity_ok, message = sanity_check(scenario)
        score = oracle_result(scenario, dt=0.2).score.total
        category = category_of(scenario)

        if not sanity_ok:
            verdict = f"FAILS SANITY: {message}"
        elif score < MIN_ORACLE:
            verdict = f"BELOW FLOOR ({MIN_ORACLE:.0f})"
        elif category is None:
            verdict = "NEEDS EXACTLY ONE CATEGORY TAG"
        else:
            verdict = "ok"

        if verdict != "ok":
            failures.append(path.name)
        if category:
            counts[category] += 1

        print(
            f"{scenario.id:<28} {scenario.terrain.preset.value:<7} "
            f"{category or '-':<9} {score:>7.1f}  {verdict}"
        )

    print("-" * 72)
    print(f"{len(files)} scenarios: " + ", ".join(f"{counts[c]} {c}" for c in CATEGORIES))

    if failures:
        raise SystemExit("FAILED: " + ", ".join(failures))
    if len(files) != 8:
        raise SystemExit(f"expected 8 shipped scenarios, found {len(files)}")
    print("all valid, sanity-clean, categorised and oracle-solvable")


if __name__ == "__main__":
    main()
