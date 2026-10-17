"""Query helpers shared by the routers. Keeps SQL out of the API modules."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.redbrain import RedBrain, TacticSpace, bandit_state_digest
from app.core.schemas import DifficultyVector, HabitFeatures, SimEvent, SkillVector, TraineeAction
from app.core.skills import SkillModel
from app.db.models import (
    ActionRow,
    BanditStateRow,
    EventRow,
    HabitFeatureRow,
    ScenarioRow,
    ScoreRow,
    SessionRow,
    SkillHistoryRow,
    User,
)

__all__ = [
    "actions_for",
    "bandit_for",
    "habits_for",
    "load_skill_model",
    "session_to_record",
    "store_session",
    "unit_overview",
]


def actions_for(db: Session, session_id: str) -> list[TraineeAction]:
    """Ordered action log of a session."""
    rows = db.execute(
        select(ActionRow).where(ActionRow.session_id == session_id).order_by(ActionRow.seq)
    ).scalars()
    return [
        TraineeAction.model_validate(
            {
                "t": r.t,
                "type": r.type,
                "track_id": r.track_id,
                "params": r.params_json or {},
                "source": r.source,
            }
        )
        for r in rows
    ]


def events_for(db: Session, session_id: str) -> list[dict[str, Any]]:
    """Event log of a session."""
    rows = db.execute(
        select(EventRow).where(EventRow.session_id == session_id).order_by(EventRow.id)
    ).scalars()
    return [
        {
            "t": r.t,
            "kind": r.kind,
            "entity_id": r.entity_id,
            "track_id": r.track_id,
            "data": r.data_json,
        }
        for r in rows
    ]


def load_skill_model(db: Session, user_id: str) -> SkillModel | None:
    """Rebuild a trainee's skill model from the stored history."""
    user = db.get(User, user_id)
    if user is None:
        return None
    rows = list(
        db.execute(
            select(SkillHistoryRow)
            .where(SkillHistoryRow.user_id == user_id)
            .order_by(SkillHistoryRow.recorded_at, SkillHistoryRow.id)
        ).scalars()
    )
    if not rows:
        return None
    latest = rows[-1]
    skills = SkillVector(
        detection=latest.detection,
        classification=latest.classification,
        prioritisation=latest.prioritisation,
        engagement=latest.engagement,
        roe=latest.roe,
    )
    history = [
        {
            "detection": r.detection,
            "classification": r.classification,
            "prioritisation": r.prioritisation,
            "engagement": r.engagement,
            "roe": r.roe,
            "total": (r.detection + r.classification + r.prioritisation + r.engagement + r.roe)
            / 5.0,
            "difficulty": r.difficulty_norm,
            "session": float(idx + 1),
        }
        for idx, r in enumerate(rows)
    ]
    habit_row = db.get(HabitFeatureRow, user_id)
    habits = HabitFeatures.model_validate(habit_row.features_json) if habit_row else None
    return SkillModel(
        trainee_id=user_id,
        skills=skills,
        history=history,
        habits=habits,
        sessions=len(rows),
    )


def load_skill_model_by_id(user_id: str) -> SkillModel | None:
    """Convenience wrapper that opens its own session."""
    from app.db.session import session_scope

    with session_scope() as db:
        return load_skill_model(db, user_id)


def habits_for(db: Session, user_id: str) -> HabitFeatures | None:
    """Latest stored habit feature vector for a trainee."""
    row = db.get(HabitFeatureRow, user_id)
    return HabitFeatures.model_validate(row.features_json) if row else None


def bandit_for(db: Session, user_id: str) -> RedBrain:
    """Rebuild the Red Brain bandit state for a trainee (fresh state if absent)."""
    from app.core.redbrain import describe_policy_availability

    brain = RedBrain(space=TacticSpace())
    brain.learned_policy_available = bool(describe_policy_availability()["available"])
    row = db.get(BanditStateRow, user_id)
    if row is not None and row.state_json:
        brain.load_dict(row.state_json)
    return brain


def save_bandit(db: Session, user_id: str, brain: RedBrain) -> None:
    """Persist the Red Brain state."""
    row = db.get(BanditStateRow, user_id)
    payload = brain.to_dict()
    if row is None:
        row = BanditStateRow(user_id=user_id)
        db.add(row)
    row.arms_json = [t.id for t in brain.space.tactics]
    row.state_json = payload
    row.pulls = brain.bandit.total_pulls if brain.bandit else 0
    row.updated_at = datetime.now(UTC)


def bandit_digest_for(db: Session, user_id: str) -> str:
    """Digest of the stored bandit state; mixed into scenario seeds."""
    row = db.get(BanditStateRow, user_id)
    return bandit_state_digest(row.state_json if row else None)


def store_session(
    db: Session,
    *,
    session_id: str,
    scenario: ScenarioRow,
    user: User,
    seed: int,
    actions: list[TraineeAction],
    events: list[dict[str, Any]],
    final_state_hash: str,
    score: dict[str, Any],
    difficulty: dict[str, Any],
    bandit_state: dict[str, Any] | None = None,
    synthetic: bool = False,
    started_at: datetime | None = None,
) -> SessionRow:
    """Write a completed session and everything derived from it."""
    from app.core.schemas import ScoreBreakdown, ScoreWeights
    from app.core.scoring import SCORING_SPEC, skill_subscores

    row = db.get(SessionRow, session_id)
    if row is None:
        row = SessionRow(id=session_id, scenario_id=scenario.id, user_id=user.id)
        db.add(row)
    row.seed = seed
    row.started_at = started_at or row.started_at or datetime.now(UTC)
    row.ended_at = datetime.now(UTC)
    row.final_state_hash = final_state_hash
    row.total_score = float(score["total"])
    row.synthetic = synthetic
    if bandit_state is not None:
        row.bandit_state_json = bandit_state

    db.query(ActionRow).filter(ActionRow.session_id == session_id).delete()
    db.query(EventRow).filter(EventRow.session_id == session_id).delete()
    db.query(ScoreRow).filter(ScoreRow.session_id == session_id).delete()
    db.flush()

    for i, action in enumerate(actions):
        db.add(
            ActionRow(
                session_id=session_id,
                seq=i,
                t=action.t,
                type=action.type.value,
                track_id=action.track_id,
                params_json=action.params,
                source=action.source,
            )
        )
    for event in events:
        db.add(
            EventRow(
                session_id=session_id,
                t=float(event.get("t", 0.0)),
                kind=str(event.get("kind", "scenario_event")),
                entity_id=event.get("entity_id"),
                track_id=event.get("track_id"),
                data_json=event.get("data", {}),
            )
        )

    breakdown = ScoreBreakdown.model_validate(score)
    db.add(
        ScoreRow(
            session_id=session_id,
            total=breakdown.total,
            detection=breakdown.detection,
            classification=breakdown.classification,
            prioritisation=breakdown.prioritisation,
            engagement=breakdown.engagement,
            workload=breakdown.workload,
            breakdown_json=score,
        )
    )

    sub = skill_subscores(breakdown, ScoreWeights())
    from app.core.schemas import DifficultyVector

    vec = DifficultyVector.model_validate(difficulty)
    db.add(
        SkillHistoryRow(
            user_id=user.id,
            session_id=session_id,
            detection=sub["detection"],
            classification=sub["classification"],
            prioritisation=sub["prioritisation"],
            engagement=sub["engagement"],
            roe=sub["roe"],
            difficulty_norm=vec.mean,
        )
    )
    _ = SCORING_SPEC
    return row


def save_habits(db: Session, user_id: str, session_id: str, habits: HabitFeatures) -> None:
    """Upsert the latest habit vector for a trainee (one row per trainee)."""
    row = db.get(HabitFeatureRow, user_id)
    if row is None:
        row = HabitFeatureRow(user_id=user_id)
        db.add(row)
    row.session_id = session_id
    row.features_json = habits.model_dump(mode="json")
    row.recorded_at = datetime.now(UTC)


def session_to_record(db: Session, row: SessionRow) -> dict[str, Any]:
    """Convert a session row into the API `SessionRecord` payload."""
    score_row = db.execute(
        select(ScoreRow).where(ScoreRow.session_id == row.id)
    ).scalar_one_or_none()
    scenario_row = db.get(ScenarioRow, row.scenario_id)
    difficulty = (
        DifficultyVector.model_validate(scenario_row.difficulty_json).model_dump(mode="json")
        if scenario_row is not None
        else DifficultyVector().model_dump(mode="json")
    )
    return {
        "id": row.id,
        "scenario_id": row.scenario_id,
        "seed": row.seed,
        "trainee_id": row.user_id,
        "started_at": row.started_at.isoformat() if row.started_at else "",
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "actions": [a.model_dump(mode="json") for a in actions_for(db, row.id)],
        "events_log": [
            SimEvent.model_validate(e).model_dump(mode="json") for e in events_for(db, row.id)
        ],
        "final_state_hash": row.final_state_hash or "",
        "score": score_row.breakdown_json if score_row else None,
        "difficulty": difficulty,
        "aborted": row.aborted,
        "synthetic": row.synthetic,
    }


def unit_overview(db: Session, unit_id: str) -> dict[str, Any]:
    """Cohort-level aggregates for the unit dashboard."""
    users = list(db.execute(select(User).where(User.unit_id == unit_id)).scalars())
    rows = []
    for user in users:
        sessions = list(
            db.execute(
                select(SessionRow).where(SessionRow.user_id == user.id).order_by(SessionRow.id)
            ).scalars()
        )
        scores = [s.total_score for s in sessions if s.total_score is not None]
        model = load_skill_model(db, user.id)
        rows.append(
            {
                "trainee_id": user.id,
                "display_name": user.display_name,
                "sessions": len(sessions),
                "mean_score": round(sum(scores) / len(scores), 2) if scores else None,
                "skills": model.skills.to_array() if model else None,
                "weakest": model.skills.weakest if model else None,
                "synthetic": user.synthetic,
            }
        )
    weakest_counts: dict[str, int] = {}
    for row in rows:
        if row["weakest"]:
            weakest_counts[row["weakest"]] = weakest_counts.get(row["weakest"], 0) + 1
    total = db.execute(select(func.count()).select_from(SessionRow)).scalar_one()
    return {
        "unit_id": unit_id,
        "trainees": rows,
        "sessions_total": int(total),
        "weakest_skill_counts": dict(sorted(weakest_counts.items())),
    }


def dump(value: Any) -> str:
    """JSON-encode a value for a TEXT column."""
    return json.dumps(value, sort_keys=True)
