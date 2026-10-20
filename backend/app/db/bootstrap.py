"""Start-up bootstrap for an instance that starts with an empty database.

The serverless deployment has no writable filesystem except ``/tmp``, and
``/tmp`` belongs to a single instance, so every instance starts from nothing.
Two things fix that:

* the build ships a pre-seeded snapshot (``backend/scripts/build_seed_snapshot.py``)
  that the application copies into place on start-up, and
* if that snapshot is missing, the instance seeds the minimum it needs to be
  usable - the shipped scenarios and the demo roster - so a judge who opens the
  app never meets an empty library.

The oracle score of a scenario is computed by playing the whole scenario, which
takes a few seconds, so it is filled in by a background task instead of
blocking the start-up.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("skygym.bootstrap")

__all__ = ["ensure_library", "refresh_oracle_scores"]


def ensure_library() -> bool:
    """Make sure the scenario library and the demo roster exist.

    Returns True when the library had to be seeded.
    """
    from app.db.models import ScenarioRow
    from app.db.session import session_scope

    with session_scope() as db:
        if db.query(ScenarioRow).count() > 0:
            return False

    # Imported lazily: the seeding helpers live next to the other scripts and
    # pull in the generator, which is a second of import time nobody needs
    # when the snapshot was already in place.
    from scripts.seed import load_shipped, seed_demo_unit, seed_scenarios

    scenarios = load_shipped(with_oracle=False)
    seed_scenarios(scenarios)
    seed_demo_unit(scenarios, simulate=False)
    logger.info("seeded %d shipped scenario(s) into an empty database", len(scenarios))
    return True


async def refresh_oracle_scores() -> None:
    """Fill in the oracle score of every scenario that does not have one yet.

    The oracle plays the whole scenario, so this runs in a worker thread while
    the application is already serving requests. The library shows a dash until
    the score lands.
    """
    from sqlalchemy import select

    from app.core.schemas import Scenario
    from app.core.session import oracle_result
    from app.db.models import ScenarioRow
    from app.db.session import session_scope

    with session_scope() as db:
        rows = list(
            db.execute(select(ScenarioRow).where(ScenarioRow.oracle_score.is_(None))).scalars()
        )
    if not rows:
        return

    for row in rows:
        try:
            scenario = Scenario.model_validate(row.body_json)
            result = await asyncio.to_thread(oracle_result, scenario, 0.2, None, False)
        except Exception:  # pragma: no cover - a bad scenario must not kill start-up
            logger.exception("oracle score failed for %s", row.id)
            continue
        with session_scope() as db:
            fresh = db.get(ScenarioRow, row.id)
            if fresh is not None:
                fresh.oracle_score = round(result.score.total, 2)
        logger.info("oracle score for %s: %.1f", row.id, result.score.total)
