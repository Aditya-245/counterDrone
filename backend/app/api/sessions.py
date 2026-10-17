"""Session lifecycle: create, list, fetch, submit results, and the WebSocket."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC
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
    StateFrame,
    TraineeAction,
)
from app.db.models import ScenarioRow, SessionRow, User
from app.db.repositories import (
    actions_for,
    session_to_record,
)
from app.db.session import session_scope
from app.live import LiveSession, get_live_session, live_registry

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    """Body for `POST /api/sessions`."""

    scenario_id: str
    trainee_id: str
    allow_pause: bool = False
    dt: float = Field(default=0.1, gt=0.0, le=1.0)


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

    The server never trusts the client's score: it re-runs the deterministic
    replay from the seed and the stored action log and returns the authoritative
    breakdown. The client-supplied values are accepted only as a cross-check.
    """

    score: ScoreBreakdown | None = None
    final_state_hash: str = ""
    aborted: bool = False


@router.post("", response_model=SessionRecord, status_code=201)
def create_session(body: CreateSessionRequest) -> SessionRecord:
    """Register a new session row and register a live runtime for it."""
    from app.core.engine import initial_state

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

    live = get_live_session(session_id)
    live.start(
        scenario,
        dt=body.dt,
        allow_pause=body.allow_pause,
        trainee_id=body.trainee_id,
        session_id=session_id,
    )
    _ = initial_state
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
        items = []
        for row in rows:
            scenario_row = db.get(ScenarioRow, row.scenario_id)
            user = db.get(User, row.user_id)
            items.append(
                SessionListItem(
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
            )
    return SessionList(items=items, total=len(items))


@router.get("/live", response_model=list[SessionListItem])
def list_live() -> list[SessionListItem]:
    """Sessions currently being played over a WebSocket."""
    from app.api.scenarios import load_scenario_row

    out: list[SessionListItem] = []
    with session_scope() as db:
        for live in live_registry.all():
            scenario_row = db.get(ScenarioRow, live.scenario.id)
            user = db.get(User, live.trainee_id)
            out.append(
                SessionListItem(
                    id=live.session_id,
                    scenario_id=live.scenario.id,
                    scenario_name=scenario_row.name if scenario_row else live.scenario.id,
                    trainee_id=live.trainee_id,
                    trainee_name=user.display_name if user else live.trainee_id,
                    seed=live.scenario.seed,
                    started_at=_now_iso(),
                    total_score=None,
                    synthetic=False,
                )
            )
    _ = load_scenario_row
    return out


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


@router.get("/{session_id}/frame", response_model=StateFrame)
def read_frame(session_id: str) -> StateFrame:
    """The latest authoritative frame for a live session."""
    live = live_registry.get(session_id)
    if live is None:
        raise not_found("live session", session_id)
    return live.current_frame()


@router.post("/{session_id}/result", response_model=SessionRecord)
def submit_result(session_id: str, body: SubmitResultRequest) -> SessionRecord:
    """Finish a session: replay it, score it, persist it, update skills."""
    from app.core.session import RECORD_DT, play, replay
    from app.core.skills import habits_from_actions
    from app.db.repositories import save_habits, store_session

    live = live_registry.get(session_id)
    if live is not None:
        live.finish()

    with session_scope() as db:
        row = db.get(SessionRow, session_id)
        if row is None:
            raise not_found("session", session_id)
        scenario_row = db.get(ScenarioRow, row.scenario_id)
        user = db.get(User, row.user_id)
        if scenario_row is None or user is None:
            raise unprocessable("session references a missing scenario or trainee")
        scenario = Scenario.model_validate(scenario_row.body_json)
        actions = actions_for(db, session_id)

    if not actions and live is not None:
        actions = list(live.actions)
    replayed = replay(scenario, actions, dt=RECORD_DT)
    if body.final_state_hash and body.final_state_hash != replayed.final_state_hash:
        raise unprocessable(
            "client state hash does not match the deterministic replay",
            client_hash=body.final_state_hash,
            replay_hash=replayed.final_state_hash,
        )

    result = play(
        scenario,
        _ReplayActions(actions),
        dt=RECORD_DT,
    )
    score = result.score

    import math

    from app.core.engine import NUM_SECTORS
    from app.core.view import sector_of_bearing

    sector_of_track: dict[str, int] = {}
    for tid, eid in result.state.track_entities.items():
        ent = result.state.world.entities.get(eid)
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

    live_registry.drop(session_id)
    with session_scope() as db:
        row = db.get(SessionRow, session_id)
        assert row is not None
        return SessionRecord.model_validate(session_to_record(db, row))


@router.delete("/{session_id}", response_model=OkResponse)
def abandon_session(session_id: str) -> OkResponse:
    """Drop a live session and mark the stored row aborted."""
    live_registry.drop(session_id)
    with session_scope() as db:
        row = db.get(SessionRow, session_id)
        if row is None:
            raise not_found("session", session_id)
        row.aborted = True
    return OkResponse(ok=True, message=f"session {session_id} abandoned")


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class _ReplayActions:
    """Adapts a stored action list to the `Policy` protocol."""

    actions: list[TraineeAction] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.actions = sorted(self.actions, key=lambda a: a.t)

    def act(self, state: Any, world: Any, t: float) -> list[TraineeAction]:
        due = [a for a in self.actions if a.t <= t + 1e-9]
        self.actions = [a for a in self.actions if a.t > t + 1e-9]
        return due


_ASYNCIO = asyncio
_TIME = time
_LIVE = LiveSession
