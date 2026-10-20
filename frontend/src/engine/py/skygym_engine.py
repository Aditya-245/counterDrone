"""Client-side SKYGYM engine - runs inside Pyodide in the browser.

The whole simulation lives here. The browser loads ``app.core`` into Pyodide and
drives the *same* deterministic engine the server used to run, so the state hash
a session produces in the browser is bit-identical to the one the server
reproduces from the action log.

Nothing in this module touches the network or the database. It is a pure
function of ``(scenario, action log)`` - the same contract ``app.core.session``
honours on the server.

The JavaScript side calls the module-level ``skygym_*`` functions; each returns
a JSON string so the boundary is one serialisation, not a field-by-field
mapping.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from app.core.engine import EngineState, initial_state, step
from app.core.roe import get_rule_set, roe_spec_for
from app.core.schemas import (
    EntityClass,
    ForkResult,
    FrameSample,
    GhostAction,
    LabelType,
    ReplayEntity,
    ReplaySession,
    Scenario,
    TraineeAction,
)
from app.core.scoring import score_session
from app.core.session import RECORD_DT, REPLAY_SAMPLE_S, OraclePolicyAdapter, oracle_policy
from app.core.view import build_state_frame

__all__ = [
    "skygym_action",
    "skygym_finish",
    "skygym_fork",
    "skygym_landscape",
    "skygym_start",
    "skygym_status",
    "skygym_tick",
]

#: The running session. There is exactly one per browser tab: the simulation is
#: a single timeline and the worker drives it alone.
_SESSION: "_ClientSession | None" = None


class _ClientSession:
    """One live session, plus the capture buffers the AAR needs.

    The engine is stepped at the canonical 10 Hz (``RECORD_DT``) so the state
    hash matches a server-side replay of the same action log.
    """

    def __init__(self, scenario: Scenario, session_id: str, dt: float = RECORD_DT) -> None:
        self.scenario = scenario
        self.session_id = session_id
        self.dt = float(dt)
        self.state: EngineState = initial_state(scenario, dt=self.dt, allow_pause=False)
        self.rng = np.random.default_rng(int(scenario.seed))
        self.pending: list[TraineeAction] = []
        self.actions: list[TraineeAction] = []
        self.oracle = OraclePolicyAdapter(oracle_policy(scenario))
        self.frames: list[Any] = []
        self.paths: dict[str, list[FrameSample]] = {}
        self.entities_meta: dict[str, tuple[EntityClass, LabelType]] = {}
        self.neutralised_at: dict[str, float] = {}
        self.perimeter_at: dict[str, float] = {}
        self.next_sample = 0.0
        self.step_events: list[Any] = []
        self.finished = False

    # ------------------------------------------------------------------ #
    def tick(self) -> Any:
        """Advance one timestep and return the trainee-visible frame."""
        if self.finished or self.state.world.status != "running":
            self.finished = True
            return build_state_frame(self.state, self.session_id, list(self.step_events))

        before = len(self.state.world.events)
        self.state = step(self.state, self.pending, self.rng)
        self.pending = []
        self.step_events = list(self.state.world.events[before:])
        self._capture()
        # The ghost observes the same timeline the trainee does. The oracle
        # never writes to the engine state, so running it here cannot perturb
        # the simulation - it only records what an expert would have done.
        self.oracle.act(self.state, self.state.world, self.state.world.t)
        if self.state.world.status == "finished":
            self.finished = True
        return build_state_frame(self.state, self.session_id, list(self.step_events))

    # ------------------------------------------------------------------ #
    def _capture(self) -> None:
        """Sample the replay buffers at 1 Hz, exactly like `core.session.replay`."""
        state = self.state
        if state.world.t + 1e-9 < self.next_sample:
            return
        self.next_sample += REPLAY_SAMPLE_S
        self.frames.append(build_state_frame(state, "replay"))
        for eid, ent in state.world.entities.items():
            self.paths.setdefault(eid, []).append(
                FrameSample(
                    t=round(state.world.t, 2),
                    x=ent.position.x,
                    y=ent.position.y,
                    z=ent.position.z,
                )
            )
            self.entities_meta[eid] = (ent.entity_class, ent.label)
            if ent.neutralised and ent.neutralised_t is not None:
                self.neutralised_at.setdefault(eid, ent.neutralised_t)
            if ent.reached_perimeter_t is not None:
                self.perimeter_at.setdefault(eid, ent.reached_perimeter_t)

    # ------------------------------------------------------------------ #
    def queue(self, action: TraineeAction) -> None:
        """Accept one trainee action; it lands on the next tick boundary."""
        self.pending.append(action)
        self.actions.append(action)

    # ------------------------------------------------------------------ #
    def score(self) -> Any:
        """Score the session exactly as `core.session.play` would."""
        return score_session(
            self.scenario,
            self.state.world,
            self.actions,
            get_rule_set(roe_spec_for(self.scenario).rule_set),
            self.state.track_entities,
            self.state.annotations,
            None,
        )

    # ------------------------------------------------------------------ #
    def replay_session(self) -> ReplaySession:
        """Assemble the after-action-review payload from what was captured live.

        No re-simulation is needed: the frames, the entity paths, the ghost
        actions and the score were all produced while the session ran. That is
        why the browser AAR is instant where the server's replay was not.
        """
        state = self.state
        entities = [
            ReplayEntity(
                id=eid,
                entity_class=cls,
                label=lbl,
                path=self.paths.get(eid, []),
                t_first_detectable=next(
                    (
                        e.t_first_detectable
                        for e in state.world.entities.values()
                        if e.id == eid
                    ),
                    None,
                ),
                ring_crossings={
                    str(k): round(float(v), 2)
                    for k, v in sorted(state.world.entities[eid].ring_crossings.items())
                }
                if eid in state.world.entities
                else {},
                t_closest_approach=next(
                    (
                        e.t_closest_approach
                        for e in state.world.entities.values()
                        if e.id == eid
                    ),
                    None,
                ),
                neutralised_t=self.neutralised_at.get(eid),
                reached_perimeter_t=self.perimeter_at.get(eid),
            )
            for eid, cls, lbl in sorted(
                ((eid, *meta) for eid, meta in self.entities_meta.items()),
                key=lambda r: r[0],
            )
        ]
        attention = list(state.sector_attention_s)
        from app.core.engine import NUM_SECTORS

        if len(attention) != NUM_SECTORS:
            attention = [0.0] * NUM_SECTORS
        return ReplaySession(
            session_id=self.session_id,
            scenario_id=self.scenario.id,
            duration_s=float(state.world.t),
            final_state_hash=state.state_hash(),
            ticks_per_second=int(round(1.0 / self.dt)),
            trainee_actions=list(self.actions),
            ghost_actions=list(self.oracle.ghost_actions()),
            entities=entities,
            frames=list(self.frames),
            events=list(state.world.events),
            sector_attention=[round(float(a), 2) for a in attention],
            score=self.score(),
        )


# --------------------------------------------------------------------------- #
# The JavaScript-facing API
# --------------------------------------------------------------------------- #


def skygym_start(scenario_json: str, session_id: str) -> str:
    """Begin a session. Returns the first frame as JSON."""
    global _SESSION
    scenario = Scenario.model_validate_json(scenario_json)
    _SESSION = _ClientSession(scenario, session_id)
    frame = _SESSION.tick()
    return json.dumps(frame.model_dump(mode="json"))


def skygym_tick() -> str:
    """Advance one tick. Returns the frame as JSON, or ``""`` once finished."""
    if _SESSION is None:
        return ""
    frame = _SESSION.tick()
    return json.dumps(frame.model_dump(mode="json"))


def skygym_action(action_json: str) -> str:
    """Queue one trainee action."""
    if _SESSION is None:
        return '{"error":"no session"}'
    action = TraineeAction.model_validate_json(action_json)
    _SESSION.queue(action)
    return "{}"


def skygym_finish() -> str:
    """End the session and return the after-action-review payload.

    The payload carries the replay *and* the outcome summary the counterfactual
    fork diffs against, so the browser never has to re-simulate the original
    run to fork it.
    """
    if _SESSION is None:
        return "{}"
    from app.core.session import outcome_summary_from

    payload = _SESSION.replay_session()
    outcome = outcome_summary_from(_SESSION.state, payload.score, _SESSION.scenario)
    return json.dumps(
        {
            "replay": payload.model_dump(mode="json"),
            "outcome": outcome,
        }
    )


def skygym_status() -> str:
    """A small status blob: the engine's idea of where the session is."""
    if _SESSION is None:
        return json.dumps({"running": False})
    state = _SESSION.state
    return json.dumps(
        {
            "running": not _SESSION.finished and state.world.status == "running",
            "t": state.world.t,
            "step": state.world.step,
            "status": state.world.status,
            "actions": len(_SESSION.actions),
            "state_hash": state.state_hash(),
        }
    )


def skygym_landscape(scenario_json: str) -> str:
    """Generate the world for a scenario.

    The browser renders exactly the terrain the simulation used, because it
    builds it from the same seed with the same generator.
    """
    from app.core.terrain import build_terrain
    from app.core.worldgen import generate_landscape

    scenario = Scenario.model_validate_json(scenario_json)
    terrain = build_terrain(scenario.terrain, seed=scenario.seed)
    spec = generate_landscape(
        terrain,
        seed=scenario.seed,
        preset=scenario.terrain.preset,
        buildings=tuple(scenario.terrain.buildings),
        scenario_id=scenario.id,
    )
    return json.dumps(spec.model_dump(mode="json"))


def skygym_fork(
    scenario_json: str,
    actions_json: str,
    ghost_json: str,
    request_json: str,
    original_json: str,
) -> str:
    """Re-simulate from a decision point with a modified action log.

    The browser already has the original run (it played it) and the ghost's
    decisions (it recorded them while the session ran), so a fork costs **one**
    simulation instead of the three a from-scratch fork needs.
    """
    from app.core.schemas import ForkRequest
    from app.core.session import outcome_summary, play

    scenario = Scenario.model_validate_json(scenario_json)
    actions = [TraineeAction.model_validate(a) for a in json.loads(actions_json)]
    ghosts = [GhostAction.model_validate(g) for g in json.loads(ghost_json)]
    request = ForkRequest.model_validate(json.loads(request_json))
    original = json.loads(original_json)

    # Build the fork log exactly the way `core.session.fork_session` does.
    fork_actions: list[TraineeAction] = []
    for i, a in enumerate(actions):
        if request.replace_index is not None and i == request.replace_index:
            continue
        if request.replace_all_from and a.t >= request.fork_t - 1e-9:
            continue
        fork_actions.append(a)
    if request.inject_action is not None:
        fork_actions.append(request.inject_action)

    appended_ghosts: list[GhostAction] = []
    appended_actions: list[TraineeAction] = []
    if request.follow_with_ghost:
        for ghost in ghosts:
            if ghost.t <= request.fork_t + 1e-9:
                continue
            appended_ghosts.append(ghost)
            action = TraineeAction(
                t=ghost.t,
                type=ghost.type,
                track_id=ghost.track_id,
                params=dict(ghost.params),
                source="oracle",
            )
            appended_actions.append(action)
            fork_actions.append(action)
    fork_actions.sort(key=lambda a: a.t)

    forked = play(scenario, _StaticActions(fork_actions), dt=RECORD_DT)

    diverged = forked.final_state_hash != original["hash"]
    delta = forked.score.total - original["score"]["total"]
    if diverged:
        explanation = (
            f"Forking at t={request.fork_t:.1f} s changed the outcome: score "
            f"{original['score']['total']:.1f} -> {forked.score.total:.1f} ({delta:+.1f}). "
            f"{_describe_change(original['outcome'], outcome_summary(forked))}"
        )
    else:
        explanation = (
            f"The fork reproduced the original run exactly (hash {forked.final_state_hash}); "
            "the injected change had no effect on the outcome."
        )

    result = ForkResult(
        fork_t=request.fork_t,
        original_hash=original["hash"],
        forked_hash=forked.final_state_hash,
        diverged=diverged,
        original_score=original["score"]["total"],
        forked_score=forked.score.total,
        score_delta=round(delta, 2),
        original_outcome=original["outcome"],
        forked_outcome=outcome_summary(forked),
        explanation=explanation,
        ghost_actions=appended_ghosts,
        newly_appended_actions=appended_actions,
    )
    return json.dumps(result.model_dump(mode="json"))


def _describe_change(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Human-readable list of what the fork changed."""
    bits: list[str] = []
    for key, label in (
        ("neutralised", "neutralised"),
        ("breached", "hostiles that breached"),
        ("ammunition_used", "engagements fired"),
    ):
        if before.get(key) != after.get(key):
            bits.append(f"{label} {before.get(key)} -> {after.get(key)}")
    return "; ".join(bits) if bits else "no change in the outcome summary"


class _StaticActions:
    """Replays a fixed action log; matches the window `step` itself applies."""

    def __init__(self, actions: list[TraineeAction]) -> None:
        self.actions = sorted(actions, key=lambda a: a.t)

    def act(self, state: Any, world: Any, t: float) -> list[TraineeAction]:
        due = [a for a in self.actions if a.t <= t + 1e-9]
        self.actions = [a for a in self.actions if a.t > t + 1e-9]
        return due
