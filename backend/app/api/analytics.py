"""Analytics: individual dashboard data and unit/cohort comparison."""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import not_found
from app.core.schemas import DifficultyVector, SkillVector
from app.db.models import ScenarioRow, ScoreRow, SessionRow, User
from app.db.repositories import (
    bandit_for,
    habits_for,
    load_skill_model,
    unit_overview,
)
from app.db.session import session_scope

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/trainee/{trainee_id}")
def trainee_dashboard(trainee_id: str) -> dict[str, Any]:
    """Everything the individual Dashboard page needs, from stored data only."""
    with session_scope() as db:
        user = db.get(User, trainee_id)
        if user is None:
            raise not_found("trainee", trainee_id)
        model = load_skill_model(db, trainee_id)
        session_rows = list(
            db.execute(
                select(SessionRow)
                .where(SessionRow.user_id == trainee_id)
                .order_by(SessionRow.started_at, SessionRow.id)
            ).scalars()
        )
        scores: list[dict[str, Any]] = []
        latencies: list[float] = []
        verdicts_over_time: list[dict[str, Any]] = []
        confusion: dict[str, dict[str, int]] = {}
        for row in session_rows:
            score_row = db.execute(
                select(ScoreRow).where(ScoreRow.session_id == row.id)
            ).scalar_one_or_none()
            if score_row is None:
                continue
            breakdown = score_row.breakdown_json or {}
            scores.append(
                {
                    "session_id": row.id,
                    "scenario_id": row.scenario_id,
                    "total": score_row.total,
                    "detection": score_row.detection,
                    "classification": score_row.classification,
                    "prioritisation": score_row.prioritisation,
                    "engagement": score_row.engagement,
                    "workload": score_row.workload,
                    "synthetic": row.synthetic,
                    "started_at": row.started_at.isoformat() if row.started_at else "",
                }
            )
            stats = breakdown.get("stats", {})
            latencies.extend(float(v) for v in stats.get("latency_samples", []))
            for true_label, row_counts in (stats.get("confusion_matrix") or {}).items():
                cell_row = confusion.setdefault(true_label, {})
                for chosen, count in row_counts.items():
                    cell_row[chosen] = cell_row.get(chosen, 0) + int(count)
            counts = breakdown.get("verdict_counts", {})
            verdicts_over_time.append(
                {
                    "session_id": row.id,
                    "t": row.started_at.isoformat() if row.started_at else "",
                    "counts": counts,
                }
            )
        brain = bandit_for(db, trainee_id)
        report = brain.report(
            trainee_id,
            model.habits if model else _empty_habits(),
            DifficultyVector(),
        )
        habits = habits_for(db, trainee_id)

    return {
        "trainee": {
            "id": user.id,
            "display_name": user.display_name,
            "unit_id": user.unit_id,
            "synthetic": user.synthetic,
        },
        "skills": model.skills.model_dump(mode="json")
        if model
        else SkillVector().model_dump(mode="json"),
        "skill_history": model.history if model else [],
        "weakest_skill": model.skills.weakest if model else None,
        "sessions": scores,
        "session_count": len(scores),
        "mean_score": round(sum(s["total"] for s in scores) / len(scores), 2) if scores else None,
        "detection_latencies": sorted(latencies),
        "classification_confusion": confusion,
        "verdicts_over_time": verdicts_over_time,
        "habits": habits.model_dump(mode="json") if habits else None,
        "redbrain": report.model_dump(mode="json"),
        "synthetic_note": (
            "Historical rows marked synthetic are generated demo data, not real trainees."
            if any(s["synthetic"] for s in scores)
            else None
        ),
    }


@router.get("/unit/{unit_id}")
def unit_dashboard(unit_id: str) -> dict[str, Any]:
    """Cohort comparison for the Instructor view."""
    with session_scope() as db:
        users = list(db.execute(select(User).where(User.unit_id == unit_id)).scalars())
        if not users:
            raise not_found("unit", unit_id)
        return unit_overview(db, unit_id)


@router.get("/export/{trainee_id}.csv")
def export_csv(trainee_id: str) -> Response:
    """CSV export of one trainee's session history."""
    data = trainee_dashboard(trainee_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "session_id",
            "scenario_id",
            "started_at",
            "total",
            "detection",
            "classification",
            "prioritisation",
            "engagement",
            "workload",
            "synthetic",
        ]
    )
    for row in data["sessions"]:
        writer.writerow(
            [
                row["session_id"],
                row["scenario_id"],
                row["started_at"],
                row["total"],
                row["detection"],
                row["classification"],
                row["prioritisation"],
                row["engagement"],
                row["workload"],
                row["synthetic"],
            ]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{trainee_id}-sessions.csv"'},
    )


@router.get("/units")
def list_units() -> dict[str, Any]:
    """Every unit that has trainees."""
    from app.db.models import UnitRow

    with session_scope() as db:
        units = list(db.execute(select(UnitRow).order_by(UnitRow.name)).scalars())
        return {"items": [{"id": u.id, "name": u.name, "synthetic": False} for u in units]}


@router.get("/scenarios/{scenario_id}/stats")
def scenario_stats(scenario_id: str) -> dict[str, Any]:
    """Cohort statistics for one scenario."""
    with session_scope() as db:
        if db.get(ScenarioRow, scenario_id) is None:
            raise not_found("scenario", scenario_id)
        rows = list(
            db.execute(select(ScoreRow).join(SessionRow, ScoreRow.session_id == SessionRow.id))
            .scalars()
            .where(SessionRow.scenario_id == scenario_id)
        )
        totals = [r.total for r in rows]
        return {
            "scenario_id": scenario_id,
            "attempts": len(rows),
            "mean_total": round(sum(totals) / len(totals), 2) if totals else None,
            "mean_detection": round(sum(r.detection for r in rows) / len(rows), 2)
            if rows
            else None,
            "mean_classification": (
                round(sum(r.classification for r in rows) / len(rows), 2) if rows else None
            ),
            "mean_engagement": round(sum(r.engagement for r in rows) / len(rows), 2)
            if rows
            else None,
        }


def _empty_habits() -> Any:
    from app.core.schemas import HabitFeatures

    return HabitFeatures()
