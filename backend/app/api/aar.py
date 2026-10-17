"""After-action review: replay, ghost overlay, sector heatmap and forks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.deps import not_found, unprocessable
from app.core.schemas import ForkRequest, ForkResult, ReplaySession, Scenario
from app.core.session import RECORD_DT, fork_session, replay
from app.db.models import ScenarioRow, SessionRow
from app.db.repositories import actions_for
from app.db.session import session_scope

router = APIRouter(prefix="/api/aar", tags=["aar"])


@router.get("/{session_id}", response_model=ReplaySession)
def read_aar(session_id: str, ghost: bool = True) -> ReplaySession:
    """Deterministic replay of a finished session, with the counterfactual ghost."""
    with session_scope() as db:
        row = db.get(SessionRow, session_id)
        if row is None:
            raise not_found("session", session_id)
        scenario_row = db.get(ScenarioRow, row.scenario_id)
        if scenario_row is None:
            raise not_found("scenario", row.scenario_id)
        scenario = ScenarioRow_to_scenario(scenario_row)
        actions = actions_for(db, session_id)
    session = replay(scenario, actions, dt=RECORD_DT)
    if not ghost:
        session = session.model_copy(update={"ghost_actions": []})
    return session.model_copy(update={"session_id": session_id})


@router.get("/{session_id}/ghost", response_model=dict[str, Any])
def read_ghost(session_id: str) -> dict[str, Any]:
    """Just the ghost's actions and their rationale."""
    aar = read_aar(session_id)
    return {
        "session_id": session_id,
        "ghost_actions": [g.model_dump(mode="json") for g in aar.ghost_actions],
        "score": aar.score.model_dump(mode="json"),
        "final_state_hash": aar.final_state_hash,
    }


@router.get("/{session_id}/heatmap", response_model=dict[str, Any])
def read_heatmap(session_id: str) -> dict[str, Any]:
    """Sector-attention heatmap for the AAR."""
    aar = read_aar(session_id)
    return {
        "session_id": session_id,
        "sector_attention": aar.sector_attention,
        "labels": ["E", "NE", "N", "NW", "W", "SW", "S", "SE"],
        "duration_s": aar.duration_s,
    }


@router.post("/fork", response_model=ForkResult)
def post_fork(body: ForkRequest) -> ForkResult:
    """Re-simulate a session from a decision point with a modified action log."""
    with session_scope() as db:
        row = db.get(SessionRow, body.session_id)
        if row is None:
            raise not_found("session", body.session_id)
        scenario_row = db.get(ScenarioRow, row.scenario_id)
        if scenario_row is None:
            raise not_found("scenario", row.scenario_id)
        scenario = ScenarioRow_to_scenario(scenario_row)
        actions = actions_for(db, body.session_id)
    if body.fork_t > scenario.duration_s:
        raise unprocessable("fork time is beyond the end of the scenario")
    if body.replace_index is not None and body.replace_index >= max(1, len(actions)):
        raise unprocessable("replace_index is outside the action log")
    _, _, result = fork_session(
        scenario, actions, body, dt=RECORD_DT, follow_with_ghost=body.follow_with_ghost
    )
    return result


def ScenarioRow_to_scenario(row: ScenarioRow) -> Scenario:
    """Parse a stored scenario row into the `Scenario` model."""
    return Scenario.model_validate(row.body_json)
