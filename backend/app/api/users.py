"""Users, units and the Red Brain panel."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import not_found
from app.core.redbrain import describe_policy_availability
from app.core.schemas import DifficultyVector, HabitFeatures, RedBrainReport, SkillVector
from app.db.models import SessionRow, UnitRow, User
from app.db.repositories import bandit_for, habits_for, load_skill_model
from app.db.session import session_scope

router = APIRouter(prefix="/api/users", tags=["users"])


class UserIn(BaseModel):
    """Create or update a user."""

    id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    role: str = "trainee"
    unit_id: str | None = None
    synthetic: bool = False


class UserOut(BaseModel):
    """User summary."""

    id: str
    display_name: str
    role: str
    unit_id: str | None
    synthetic: bool
    session_count: int = 0


class UnitIn(BaseModel):
    """Create a unit."""

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)


@router.get("", response_model=list[UserOut])
def list_users(unit_id: str | None = None) -> list[UserOut]:
    """All users, optionally filtered to one unit."""
    with session_scope() as db:
        stmt = select(User).order_by(User.display_name)
        if unit_id:
            stmt = stmt.where(User.unit_id == unit_id)
        users = list(db.execute(stmt).scalars())
        out: list[UserOut] = []
        for user in users:
            count = len(
                list(db.execute(select(SessionRow).where(SessionRow.user_id == user.id)).scalars())
            )
            out.append(
                UserOut(
                    id=user.id,
                    display_name=user.display_name,
                    role=user.role,
                    unit_id=user.unit_id,
                    synthetic=user.synthetic,
                    session_count=count,
                )
            )
    return out


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserIn) -> UserOut:
    """Create a user (idempotent on `id`)."""
    with session_scope() as db:
        user = db.get(User, body.id)
        if user is None:
            user = User(
                id=body.id,
                display_name=body.display_name,
                role=body.role,
                unit_id=body.unit_id,
                synthetic=body.synthetic,
            )
            db.add(user)
        else:
            user.display_name = body.display_name
            user.role = body.role
            user.unit_id = body.unit_id
        db.flush()
        return UserOut(
            id=user.id,
            display_name=user.display_name,
            role=user.role,
            unit_id=user.unit_id,
            synthetic=user.synthetic,
            session_count=0,
        )


@router.get("/units", response_model=list[dict[str, Any]])
def list_units() -> list[dict[str, Any]]:
    """All units."""
    with session_scope() as db:
        return [{"id": u.id, "name": u.name} for u in db.execute(select(UnitRow)).scalars()]


@router.post("/units", response_model=dict[str, Any], status_code=201)
def create_unit(body: UnitIn) -> dict[str, Any]:
    """Create a unit."""
    with session_scope() as db:
        unit = db.get(UnitRow, body.id)
        if unit is None:
            unit = UnitRow(id=body.id, name=body.name)
            db.add(unit)
        db.flush()
        return {"id": unit.id, "name": unit.name}


@router.get("/{trainee_id}/skills")
def read_skills(trainee_id: str) -> dict[str, Any]:
    """Skill ratings, history and habits for one trainee."""
    with session_scope() as db:
        if db.get(User, trainee_id) is None:
            raise not_found("trainee", trainee_id)
        model = load_skill_model(db, trainee_id)
        habits = habits_for(db, trainee_id)
    return {
        "trainee_id": trainee_id,
        "skills": (model.skills if model else SkillVector()).model_dump(mode="json"),
        "history": model.history if model else [],
        "weakest_skill": model.skills.weakest if model else None,
        "sessions": model.sessions if model else 0,
        "trend": model.trend() if model else {},
        "declining_skill": model.decline() if model else "",
        "habits": habits.model_dump(mode="json") if habits else None,
    }


@router.get("/{trainee_id}/redbrain", response_model=RedBrainReport)
def read_redbrain(trainee_id: str) -> RedBrainReport:
    """The 'adversary adapted to you' panel.

    Every claim in the response is derived from the stored habit statistics and
    the stored bandit state; nothing here is asserted without evidence.
    """
    with session_scope() as db:
        if db.get(User, trainee_id) is None:
            raise not_found("trainee", trainee_id)
        brain = bandit_for(db, trainee_id)
        habits = habits_for(db, trainee_id) or HabitFeatures()
        difficulty = _last_difficulty(db, trainee_id)
        return brain.report(trainee_id, habits, difficulty)


@router.post("/{trainee_id}/redbrain/reset")
def reset_redbrain(trainee_id: str) -> dict[str, Any]:
    """Forget everything the adversary has learned about a trainee."""
    with session_scope() as db:
        from app.db.models import BanditStateRow

        row = db.get(BanditStateRow, trainee_id)
        if row is not None:
            db.delete(row)
    return {"ok": True, "message": f"red brain state cleared for {trainee_id}"}


@router.get("/redbrain/policy")
def read_policy_status() -> dict[str, Any]:
    """Whether the optional Layer-B learned policy is available."""
    return describe_policy_availability()


def _last_difficulty(db: Any, trainee_id: str) -> DifficultyVector:
    """Difficulty vector of the trainee's most recent session."""
    from app.db.models import ScenarioRow

    row = db.execute(
        select(SessionRow)
        .where(SessionRow.user_id == trainee_id)
        .order_by(SessionRow.started_at.desc(), SessionRow.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return DifficultyVector()
    scenario_row = db.get(ScenarioRow, row.scenario_id)
    if scenario_row is None:
        return DifficultyVector()
    return DifficultyVector.model_validate(scenario_row.difficulty_json)
