"""Instructor tools: rule-set selection, forced difficulty, seed locking."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import not_found, unprocessable
from app.core.generator import generate
from app.core.roe import available_rule_sets, describe_tree
from app.core.schemas import DifficultyVector, Scenario
from app.db.models import ScenarioRow, User
from app.db.session import session_scope

router = APIRouter(prefix="/api/instructor", tags=["instructor"])


class CohortExercise(BaseModel):
    """A locked-seed exercise: every trainee gets a byte-identical scenario."""

    name: str = Field(min_length=1, max_length=160)
    seed: int = Field(ge=0, le=2**31 - 1)
    difficulty: DifficultyVector = Field(default_factory=DifficultyVector)
    rule_set: str = "STANDARD"
    trainee_ids: list[str] = Field(default_factory=list)


class DifficultyOverride(BaseModel):
    """Force a difficulty vector for a trainee's next recommended scenario."""

    trainee_id: str
    difficulty: DifficultyVector
    reason: str = ""


@router.get("/rule-sets")
def list_rule_sets() -> dict[str, Any]:
    """Every shipped ROE rule set with its summary."""
    return {
        "items": [
            {
                "id": name,
                "title": describe_tree(name)["title"],
                "description": describe_tree(name)["description"],
            }
            for name in available_rule_sets()
        ]
    }


@router.post("/exercises", response_model=dict[str, Any], status_code=201)
def create_exercise(body: CohortExercise) -> dict[str, Any]:
    """Create a seed-locked exercise so a cohort faces an identical scenario."""
    from app.core.roe import get_rule_set

    rule_set = get_rule_set(body.rule_set)
    result = generate(
        seed=body.seed,
        difficulty=body.difficulty,
        tags=["exercise", "seed-locked"],
        name=body.name,
    )
    if not result.sanity_ok:
        raise unprocessable(
            "generated exercise failed the sanity simulation",
            sanity_message=result.sanity_message,
        )
    scenario = result.scenario
    scenario = scenario.model_copy(
        update={
            "roe": rule_set.spec({"rule_set": scenario.roe.rule_set}),
            "instructor_locked": True,
        }
    )
    scenario = Scenario.model_validate(scenario.model_dump(mode="python"))

    from app.api.scenarios import upsert_scenario

    upsert_scenario(scenario, source="generated")
    return {
        "scenario_id": scenario.id,
        "seed": scenario.seed,
        "rule_set": scenario.roe.rule_set.value,
        "trainees": body.trainee_ids,
        "sha": scenario.model_dump_json(),
        "message": (
            "Every trainee in this exercise receives the identical scenario JSON; "
            "the seed is locked so the simulation is byte-for-byte reproducible."
        ),
    }


@router.get("/exercises/{scenario_id}/lock")
def check_lock(scenario_id: str) -> dict[str, Any]:
    """Confirm a stored scenario is seed-locked for cohort comparison."""
    with session_scope() as db:
        row = db.get(ScenarioRow, scenario_id)
        if row is None:
            raise not_found("scenario", scenario_id)
        scenario = Scenario.model_validate(row.body_json)
        return {
            "scenario_id": scenario_id,
            "seed": scenario.seed,
            "instructor_locked": scenario.instructor_locked,
            "rule_set": scenario.roe.rule_set.value,
        }


@router.post("/difficulty")
def force_difficulty(body: DifficultyOverride) -> dict[str, Any]:
    """Force the difficulty used by the next recommendation for a trainee."""
    from app.db.repositories import habits_for, load_skill_model

    with session_scope() as db:
        user = db.get(User, body.trainee_id)
        if user is None:
            raise not_found("trainee", body.trainee_id)
        model = load_skill_model(db, body.trainee_id)
        weakest = model.skills.weakest if model else "detection"
        habits = habits_for(db, body.trainee_id)
    result = generate(
        seed=hash((body.trainee_id, body.difficulty.mean)) % (2**31 - 1),
        difficulty=body.difficulty,
        tags=["instructor-override"],
        name=f"Forced difficulty ({weakest})",
    )
    if not result.sanity_ok:
        raise unprocessable(
            "generated scenario failed the sanity simulation",
            sanity_message=result.sanity_message,
        )
    from app.api.scenarios import upsert_scenario

    upsert_scenario(result.scenario, source="generated")
    return {
        "scenario_id": result.scenario.id,
        "trainee_id": body.trainee_id,
        "difficulty": body.difficulty.model_dump(mode="json"),
        "weakest_skill": weakest,
        "reason": body.reason
        or f"Instructor forced difficulty; the skill model says {weakest} is weakest.",
        "habits_known": habits is not None,
    }


@router.get("/roster")
def roster(unit_id: str | None = None) -> dict[str, Any]:
    """Trainees available to an instructor."""
    with session_scope() as db:
        stmt = select(User).order_by(User.display_name)
        if unit_id:
            stmt = stmt.where(User.unit_id == unit_id)
        users = list(db.execute(stmt).scalars())
        return {
            "items": [
                {
                    "id": u.id,
                    "display_name": u.display_name,
                    "role": u.role,
                    "unit_id": u.unit_id,
                    "synthetic": u.synthetic,
                }
                for u in users
            ]
        }
