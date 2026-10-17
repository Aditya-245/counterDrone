"""Load shipped scenarios and create the demo unit with synthetic history.

Run with ``make seed``. Everything it writes is reproducible: same files and same
trainee ids always produce the same database.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.generator import sanity_check  # noqa: E402
from app.core.schemas import DifficultyVector, Scenario  # noqa: E402
from app.core.session import oracle_result  # noqa: E402
from app.core.skills import SkillModel  # noqa: E402
from app.db.models import ScenarioRow, UnitRow, User  # noqa: E402
from app.db.repositories import save_bandit  # noqa: E402
from app.db.session import init_db, session_scope  # noqa: E402

SCENARIO_DIR = REPO_ROOT / "scenarios"

DEMO_UNIT_ID = "unit_demo"
DEMO_TRAINEES = [
    ("usr_alvarez", "Sgt Alvarez"),
    ("usr_brennan", "Cpl Brennan"),
    ("usr_chandra", "Lt Chandra"),
    ("usr_dubois", "WO Dubois"),
    ("usr_eriksen", "Cpl Eriksen"),
    ("usr_farrow", "Lt Farrow"),
]
#: Synthetic sessions per trainee (spec 9: a populated dashboard, "clearly
#: flagged as synthetic"). Lower it for a quick smoke database.
DEMO_SESSIONS_PER_TRAINEE = int(os.environ.get("SKYGYM_DEMO_SESSIONS", "20"))


def load_shipped() -> list[tuple[Scenario, float]]:
    """Read every `scenarios/*.json`, validate and sanity-check it.

    Returns `(scenario, oracle_score)` pairs. Raises on the first invalid file
    rather than inserting it (DECISIONS D011).
    """
    out: list[tuple[Scenario, float]] = []
    files = sorted(SCENARIO_DIR.glob("*.json"))
    if not files:
        raise SystemExit(f"no scenario files found in {SCENARIO_DIR}; run `make check` to see why")
    for path in files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        try:
            scenario = Scenario.model_validate(raw)
        except Exception as exc:
            raise SystemExit(f"{path.name}: invalid scenario document: {exc}") from exc
        ok, message = sanity_check(scenario)
        if not ok:
            raise SystemExit(f"{path.name}: failed sanity check: {message}")
        result = oracle_result(scenario, dt=0.2)
        if result.score.total < 85.0:
            raise SystemExit(
                f"{path.name}: oracle only scores {result.score.total:.1f} (minimum 85)"
            )
        out.append((scenario, result.score.total))
    return out


def seed_scenarios(scenarios: list[tuple[Scenario, float]]) -> None:
    """Upsert every shipped scenario."""
    with session_scope() as db:
        for scenario, score in scenarios:
            row = db.get(ScenarioRow, scenario.id)
            if row is None:
                row = ScenarioRow(id=scenario.id, created_at=None)  # type: ignore[arg-type]
                db.add(row)
            row.name = scenario.name
            row.schema_version = scenario.schema_version
            row.seed = scenario.seed
            row.difficulty_json = scenario.difficulty.model_dump(mode="json")
            row.tags_json = scenario.tags
            row.body_json = scenario.model_dump(mode="json")
            row.source = "shipped"
            row.oracle_score = round(score, 2)


def prune_scenarios(keep_ids: set[str]) -> list[tuple[str, int]]:
    """Retire shipped scenarios that are no longer on disk.

    `seed_scenarios` only upserts, so a scenario file that was deleted would live
    on in the database and keep appearing on the Library page. A retired scenario
    takes its sessions with it (they can no longer be scored against a scenario
    the library no longer ships), and the count is reported rather than dropped
    silently.
    """
    from sqlalchemy import select

    from app.db.models import (
        ActionRow,
        EventRow,
        ScoreRow,
        SessionRow,
        SkillHistoryRow,
    )

    retired: list[tuple[str, int]] = []
    with session_scope() as db:
        stale = [
            row
            for row in db.execute(select(ScenarioRow)).scalars()
            if row.source == "shipped" and row.id not in keep_ids
        ]
        for row in stale:
            session_ids = list(
                db.execute(select(SessionRow.id).where(SessionRow.scenario_id == row.id)).scalars()
            )
            for session_id in session_ids:
                db.query(ActionRow).filter(ActionRow.session_id == session_id).delete()
                db.query(EventRow).filter(EventRow.session_id == session_id).delete()
                db.query(ScoreRow).filter(ScoreRow.session_id == session_id).delete()
                db.query(SkillHistoryRow).filter(SkillHistoryRow.session_id == session_id).delete()
                db.query(SessionRow).filter(SessionRow.id == session_id).delete()
            db.query(ScenarioRow).filter(ScenarioRow.id == row.id).delete()
            retired.append((row.id, len(session_ids)))
    return retired


def seed_demo_unit(scenarios: list[tuple[Scenario, float]]) -> tuple[int, int]:
    """Create the demo unit, six trainees and their synthetic session history.

    Synthetic sessions are only simulated when they are missing, so a second
    `make seed` (and every `make dev`, which seeds) returns in seconds. Returns
    `(created, expected)` session counts. Filling a gap left by an interrupted
    first run can assign that one session a different difficulty, because the
    skill model is rebuilt from scratch each time.
    """
    from app.core.redbrain import RedBrain, TacticSpace
    from app.core.skills import habits_from_actions
    from app.db.models import SessionRow
    from app.db.repositories import save_habits, store_session

    with session_scope() as db:
        unit = db.get(UnitRow, DEMO_UNIT_ID)
        if unit is None:
            db.add(UnitRow(id=DEMO_UNIT_ID, name="Demo unit (synthetic)"))
        existing = {u.id for u in db.execute(select_users()).scalars()}
        for uid, name in DEMO_TRAINEES:
            if uid not in existing:
                db.add(
                    User(
                        id=uid,
                        display_name=name,
                        role="trainee",
                        unit_id=DEMO_UNIT_ID,
                        synthetic=True,
                    )
                )
        db.flush()

    playable = [s for s, _ in scenarios]
    created_total = 0
    for idx, (uid, _name) in enumerate(DEMO_TRAINEES):
        model = SkillModel(trainee_id=uid)
        created = 0
        for n in range(DEMO_SESSIONS_PER_TRAINEE):
            session_id = f"seed_{uid}_{n + 1}"
            with session_scope() as db:
                if db.get(SessionRow, session_id) is not None:
                    continue
            difficulty, _weakest = next_difficulty_for_offset(model, n)
            base = playable[(idx + n) % len(playable)]
            scenario = base.model_copy(
                deep=True,
                update={"difficulty": DifficultyVector.model_validate(difficulty.model_dump())},
            )
            result = oracle_result(scenario, dt=0.2)
            # Synthetic noise so the demo cohort has a realistic spread.
            with session_scope() as db:
                scenario_row = db.get(ScenarioRow, scenario.id)
                user = db.get(User, uid)
                if scenario_row is None or user is None:  # pragma: no cover
                    continue
                model.apply(result.score, scenario, result.actions)
                store_session(
                    db,
                    session_id=session_id,
                    scenario=scenario_row,
                    user=user,
                    seed=scenario.seed,
                    actions=result.actions[: max(1, len(result.actions) - 6)],
                    events=[e.model_dump(mode="json") for e in result.state.world.events],
                    final_state_hash=result.final_state_hash,
                    score=result.score.model_dump(mode="json"),
                    difficulty=scenario.difficulty.model_dump(mode="json"),
                    synthetic=True,
                )
                habits = habits_from_actions(
                    result.actions,
                    {},
                    {},
                    float(scenario.duration_s),
                    model.habits,
                )
                save_habits(db, uid, session_id, habits)
            created += 1
        if created == 0:
            continue  # nothing was simulated, so leave the stored skills and bandit alone
        created_total += created
        print(f"    {uid:<16} {created} simulated, {DEMO_SESSIONS_PER_TRAINEE - created} kept")
        brain = RedBrain(space=TacticSpace())
        habits = model.habits
        if habits is not None:
            brain.choose_tactic(habits, DifficultyVector(), seed=1000 + idx, forced_tactic=None)
        with session_scope() as db:
            save_bandit(db, uid, brain)
    return created_total, len(DEMO_TRAINEES) * DEMO_SESSIONS_PER_TRAINEE


def next_difficulty_for_offset(model: SkillModel, n: int) -> tuple[DifficultyVector, str]:
    """Difficulty for the n-th synthetic session, drifting upwards over time."""
    from app.core.generator import next_difficulty_for

    base, weakest = next_difficulty_for(model.skills)
    drift = min(0.3, 0.08 * n)
    adjusted = DifficultyVector.model_validate(
        {
            **base.model_dump(),
            "swarm_size": min(1.0, base.swarm_size + drift),
            "time_pressure": min(1.0, base.time_pressure + drift),
        }
    )
    return adjusted, weakest


def select_users():
    """`select(User)` without importing at module scope (keeps the script thin)."""
    from sqlalchemy import select

    from app.db.models import User as UserModel

    return select(UserModel)


def main() -> int:
    """CLI entry point."""
    print("SKYGYM seed")
    init_db()
    scenarios = load_shipped()
    seed_scenarios(scenarios)
    print(f"  scenarios : {len(scenarios)} validated and stored")
    for scenario, score in scenarios:
        print(f"    {scenario.id:<28} oracle {score:5.1f}")
    retired = prune_scenarios({scenario.id for scenario, _ in scenarios})
    for scenario_id, sessions in retired:
        print(f"  retired   : {scenario_id} (and {sessions} session(s) on it)")
    created, expected = seed_demo_unit(scenarios)
    print(
        f"  demo unit : {DEMO_UNIT_ID} with {len(DEMO_TRAINEES)} trainees x "
        f"{DEMO_SESSIONS_PER_TRAINEE} synthetic sessions "
        f"({created} simulated now, {expected - created} already present)"
    )
    print("  done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
