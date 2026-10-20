"""Session lifecycle: create, list, fetch and submit results.

The simulation runs in the browser (see `frontend/src/engine`), so the server
never ticks a session and never holds a live runtime. What it does own is the
**record**: the action log the browser produced, the authoritative score it
recomputes from that log, and the skill and habit state derived from it.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import not_found, unprocessable
from app.core.schemas import (
    OkResponse,
    Scenario,
    ScoreBreakdown,
    SessionRecord,
    TraineeAction,
)
from app.db.models import ScenarioRow, SessionRow, User
from app.db.repositories import actions_for, save_habits, session_to_record, store_session
from app.db.session import session_scope

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

#: A session row with no end time is considered live for this long. The
#: browser registers a session when it starts and closes it when it finishes,
#: so the instructor panel is a view over the database rather than over an
#: in-process registry.
LIVE_WINDOW = timedelta(minutes=30)


class CreateSessionRequest(BaseModel):
    """Body for `POST /api/sessions`."""

    scenario_id: str
    trainee_id: str
    allow_pause: bool = False


class SessionListItem(BaseModel):
    """Row used by the Instructor "live sessions" panel."""

    id: str
    scenario_id: str
    scenario_name: str
    trainee_id: str
    trainee_name: str
    seed: int
    started_at: str
    total_score: float | None
    synthetic: bool


class SessionList(BaseModel):
    """Response of `GET /api/sessions`."""

    items: list[SessionListItem]
    total: int


class SubmitResultRequest(BaseModel):
    """Body for `POST /api/sessions/{id}/result`.

    The browser sends the action log it produced. The server never trusts a
    score it did not compute: it re-runs the deterministic replay from the
    seed and the action log and returns the authoritative breakdown.
    """

    actions: list[TraineeAction] = Field(default_factory=list)
    score: ScoreBreakdown | None = None
    final_state_hash: str = ""
    aborted: bool = False


@router.post("", response_model=SessionRecord, status_code=201)
def create_session(body: CreateSessionRequest) -> SessionRecord:
    """Register a session row.

    The row exists before the browser starts playing, so the instructor can
    see the session open, and so a session that is abandoned is still recorded.
    """
    with session_scope() as db:
        scenario_row = db.get(ScenarioRow, body.scenario_id)
        if scenario_row is None:
            raise not_found("scenario", body.scenario_id)
        user = db.get(User, body.trainee_id)
        if user is None:
            raise not_found("trainee", body.trainee_id)
        scenario = Scenario.model_validate(scenario_row.body_json)
        session_id = f"ses_{uuid.uuid4().hex[:12]}"
        row = SessionRow(
            id=session_id,
            scenario_id=scenario.id,
            user_id=user.id,
            seed=scenario.seed,
            bandit_state_json=None,
        )
        db.add(row)
        db.flush()
        return SessionRecord(
            id=session_id,
            scenario_id=scenario.id,
            seed=scenario.seed,
            trainee_id=body.trainee_id,
            started_at=_now_iso(),
        )


@router.get("", response_model=SessionList)
def list_sessions(
    trainee_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> SessionList:
    """Recent sessions, newest first."""
    with session_scope() as db:
        stmt = select(SessionRow).order_by(SessionRow.started_at.desc(), SessionRow.id.desc())
        if trainee_id:
            stmt = stmt.where(SessionRow.user_id == trainee_id)
        rows = list(db.execute(stmt.limit(limit)).scalars())
        items = [_list_item(db, row) for row in rows]
    return SessionList(items=items, total=len(items))


@router.get("/live", response_model=list[SessionListItem])
def list_live() -> list[SessionListItem]:
    """Sessions that have started but not finished yet.

    The browser owns the simulation, so "live" means "a row with no end time
    inside the live window" rather than "a runtime in this process".
    """
    cutoff = datetime.now(UTC) - LIVE_WINDOW
    with session_scope() as db:
        stmt = (
            select(SessionRow)
            .where(SessionRow.ended_at.is_(None))
            .where(SessionRow.started_at >= cutoff)
            .order_by(SessionRow.started_at.desc())
        )
        rows = list(db.execute(stmt).scalars())
        return [_list_item(db, row) for row in rows]


@router.get("/{session_id}", response_model=SessionRecord)
def read_session(session_id: str) -> SessionRecord:
    """One stored session, including its action log and score."""
    with session_scope() as db:
        row = db.get(SessionRow, session_id)
        if row is None:
            raise not_found("session", session_id)
        return SessionRecord.model_validate(session_to_record(db, row))


@router.get("/{session_id}/actions", response_model=list[TraineeAction])
def read_actions(session_id: str) -> list[TraineeAction]:
    """The ordered action log (the determinism contract)."""
    with session_scope() as db:
        if db.get(SessionRow, session_id) is None:
            raise not_found("session", session_id)
        return actions_for(db, session_id)


@router.post("/{session_id}/result", response_model=SessionRecord)
def submit_result(session_id: str, body: SubmitResultRequest) -> SessionRecord:
    """Finish a session: replay it, score it, persist it, update the trainee.

    The replay is the server's own: it re-runs the deterministic engine from
    the scenario seed and the action log the browser sent. The browser's own
    hash is accepted as a cross-check and a mismatch is refused, so the score
    the trainee sees and the score the instructor reads cannot drift apart.
    """
    from app.core.engine import NUM_SECTORS, sector_of_bearing
    from app.core.session import RECORD_DT, replay_state
    from app.core.skills import habits_from_actions

    with session_scope() as db:
        row = db.get(SessionRow, session_id)
        if row is None:
            raise not_found("session", session_id)
        scenario_row = db.get(ScenarioRow, row.scenario_id)
        user = db.get(User, row.user_id)
        if scenario_row is None or user is None:
            raise unprocessable("session references a missing scenario or trainee")
        scenario = Scenario.model_validate(scenario_row.body_json)
        actions = sorted(body.actions, key=lambda a: a.t)

    # One simulation, not two: the replay carries the score, the final state
    # and the ghost, so nothing has to be recomputed.
    outcome = replay_state(scenario, actions, dt=RECORD_DT)
    replayed = outcome.session
    state = outcome.state
    if body.final_state_hash and body.final_state_hash != replayed.final_state_hash:
        raise unprocessable(
            "client state hash does not match the deterministic replay",
            client_hash=body.final_state_hash,
            replay_hash=replayed.final_state_hash,
        )

    score = replayed.score

    sector_of_track: dict[str, int] = {}
    for tid, eid in state.track_entities.items():
        ent = state.world.entities.get(eid)
        if ent is None:
            continue
        bearing = math.degrees(math.atan2(ent.position.y, ent.position.x)) % 360.0
        sector_of_track[tid] = sector_of_bearing(bearing)
    _ = NUM_SECTORS
    latency_by_track = {
        e.entity_id: (e.detection_latency_s or 0.0) for e in score.entities if e.detection_latency_s
    }
    habits = habits_from_actions(
        actions, sector_of_track, latency_by_track, float(scenario.duration_s)
    )

    with session_scope() as db:
        scenario_row = db.get(ScenarioRow, row.scenario_id)
        user = db.get(User, row.user_id)
        assert scenario_row is not None and user is not None
        store_session(
            db,
            session_id=session_id,
            scenario=scenario_row,
            user=user,
            seed=scenario.seed,
            actions=actions,
            events=[e.model_dump(mode="json") for e in replayed.events],
            final_state_hash=replayed.final_state_hash,
            score=score.model_dump(mode="json"),
            difficulty=scenario.difficulty.model_dump(mode="json"),
        )
        save_habits(db, user.id, session_id, habits)
        row.aborted = body.aborted

    with session_scope() as db:
        row = db.get(SessionRow, session_id)
        assert row is not None
        return SessionRecord.model_validate(session_to_record(db, row))


@router.delete("/{session_id}", response_model=OkResponse)
def abandon_session(session_id: str) -> OkResponse:
    """Mark the stored row aborted."""
    with session_scope() as db:
        row = db.get(SessionRow, session_id)
        if row is None:
            raise not_found("session", session_id)
        row.aborted = True
        row.ended_at = datetime.now(UTC)
    return OkResponse(ok=True, message=f"session {session_id} abandoned")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _list_item(db: Any, row: SessionRow) -> SessionListItem:
    """One instructor-panel row."""
    scenario_row = db.get(ScenarioRow, row.scenario_id)
    user = db.get(User, row.user_id)
    return SessionListItem(
        id=row.id,
        scenario_id=row.scenario_id,
        scenario_name=scenario_row.name if scenario_row else row.scenario_id,
        trainee_id=row.user_id,
        trainee_name=user.display_name if user else row.user_id,
        seed=row.seed,
        started_at=row.started_at.isoformat() if row.started_at else "",
        total_score=row.total_score,
        synthetic=row.synthetic,
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
