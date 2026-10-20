"""Session drivers: play a scenario with a policy, replay it, and fork it.

This is the deterministic heart behind the live WebSocket session, the
after-action review and the counterfactual fork. A live session and a replay
run *exactly the same code*; the only difference is who supplies the actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from app.core.engine import NUM_SECTORS, EngineState, initial_state, step
from app.core.oracle import OraclePolicy, oracle_policy
from app.core.roe import get_rule_set, roe_spec_for
from app.core.schemas import (
    ActionType,
    EntityClass,
    ForkRequest,
    ForkResult,
    FrameSample,
    GhostAction,
    LabelType,
    ReplayEntity,
    ReplaySession,
    Scenario,
    ScoreBreakdown,
    ScoreWeights,
    TraineeAction,
)
from app.core.scoring import score_session
from app.core.view import build_state_frame

__all__ = [
    "RECORD_DT",
    "REPLAY_SAMPLE_S",
    "Policy",
    "PlayResult",
    "OraclePolicyAdapter",
    "ScriptedBadTrainee",
    "fork_session",
    "play",
    "replay",
]

#: How often the replay records a frame (seconds). 1 Hz is enough for the AAR
#: scrubber and keeps a 10-minute replay under ~600 frames.
REPLAY_SAMPLE_S = 1.0

#: The tick rate every recorded session runs at (10 Hz). A session's
#: `final_state_hash` is only meaningful at this rate, so the result check and
#: the after-action review both replay at it. Sessions produced at another `dt`
#: cannot be reproduced by the server and must not be stored.
RECORD_DT = 0.1


class Policy(Protocol):
    """Anything that can turn the current engine state into actions."""

    def act(self, state: EngineState, world: Any, t: float) -> list[TraineeAction]:
        """Return the actions to apply at the next tick boundary."""


class OraclePolicyAdapter:
    """Wraps `core.oracle.OraclePolicy` so it matches the `Policy` protocol."""

    def __init__(self, policy: OraclePolicy) -> None:
        self.policy = policy
        self.decisions: list[tuple[float, GhostAction]] = []
        self.recorded: list[TraineeAction] = []

    def act(self, state: EngineState, world: Any, t: float) -> list[TraineeAction]:
        """Return the oracle's actions and record them for the ghost overlay."""
        out = self.policy.act(state, world, t)
        for d in out:
            self.decisions.append(
                (
                    t,
                    GhostAction(
                        t=round(t, 2),
                        type=d.action.type,
                        track_id=d.action.track_id,
                        entity_id=d.entity_id,
                        params=dict(d.action.params),
                        rationale=d.rationale,
                    ),
                )
            )
            self.recorded.append(d.action)
        return [d.action for d in out]

    def ghost_actions(self) -> list[GhostAction]:
        """The recorded ghost actions, in order."""
        return [g for _, g in self.decisions]


@dataclass(slots=True)
class ScriptedBadTrainee:
    """A deliberately poor trainee, used to prove the scorer can fail.

    Behaviour: ignores most tracks, never classifies, and engages the first
    thing it sees regardless of range, label or sector.
    """

    scenario: Scenario
    reaction_s: float = 6.0
    seen: dict[str, float] = field(default_factory=dict)
    fired: set[str] = field(default_factory=set)
    shoot_everything: bool = True

    def act(self, state: EngineState, world: Any, t: float) -> list[TraineeAction]:
        """Act badly but deterministically."""
        out: list[TraineeAction] = []
        tracks = state.tracker.tracks
        rng = state.scenario.seed % 7
        for i, (tid, trk) in enumerate(sorted(tracks.items())):
            if not trk.confirmed:
                continue
            if tid not in self.seen:
                self.seen[tid] = t
                # Aims at everything with a laughable delay.
                self.seen[tid] = t + self.reaction_s + i * 2.0 + rng
                continue
            if t < self.seen[tid]:
                continue
            if self.shoot_everything and tid not in self.fired:
                self.fired.add(tid)
                spec = self.scenario.effectors[0]
                out.append(
                    TraineeAction(
                        t=round(t, 2),
                        type=ActionType.ENGAGE_S if spec.kind == "soft" else ActionType.ENGAGE_H,
                        track_id=tid,
                        params={"effector": spec.id},
                        source="scripted",
                    )
                )
        return out


@dataclass(slots=True)
class PlayResult:
    """Everything one played session produced."""

    scenario: Scenario
    state: EngineState
    actions: list[TraineeAction]
    score: ScoreBreakdown
    final_state_hash: str
    frames: list[Any] = field(default_factory=list)
    ghost_actions: list[GhostAction] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Playing
# --------------------------------------------------------------------------- #


def play(
    scenario: Scenario,
    policy: Policy,
    dt: float = 0.1,
    weights: ScoreWeights | None = None,
    collect_frames: bool = False,
    max_steps: int | None = None,
) -> PlayResult:
    """Run a whole scenario under `policy` and score the result."""
    state = initial_state(scenario, dt=dt)
    rng = np.random.default_rng(int(scenario.seed))
    limit = max_steps if max_steps is not None else int(scenario.duration_s / dt) + 2
    actions: list[TraineeAction] = []
    frames: list[Any] = []
    next_frame = 0.0
    ghost: list[GhostAction] = getattr(policy, "decisions", [])

    while state.world.status == "running" and state.world.step < limit:
        pending = policy.act(state, state.world, state.world.t)
        actions.extend(pending)
        state = step(state, pending, rng)
        if collect_frames and state.world.t + 1e-9 >= next_frame:
            next_frame += REPLAY_SAMPLE_S
            frames.append(build_state_frame(state, "replay"))

    score = score_session(
        scenario,
        state.world,
        actions,
        get_rule_set(roe_spec_for(scenario).rule_set),
        state.track_entities,
        state.annotations,
        weights,
    )
    return PlayResult(
        scenario=scenario,
        state=state,
        actions=actions,
        score=score,
        final_state_hash=state.state_hash(),
        frames=frames,
        ghost_actions=list(ghost),
    )


def oracle_result(
    scenario: Scenario,
    dt: float = 0.2,
    weights: ScoreWeights | None = None,
    collect_frames: bool = True,
) -> PlayResult:
    """Play a scenario with the counterfactual oracle and return the result.

    `collect_frames` builds the trainee-visible view every half second, which the
    after-action replay needs but a caller that only wants the score does not -
    the seeder turns it off, which is the difference between minutes and hours.
    """
    adapter = OraclePolicyAdapter(oracle_policy(scenario))
    return play(scenario, adapter, dt=dt, weights=weights, collect_frames=collect_frames)


def bad_trainee_result(
    scenario: Scenario, dt: float = 0.2, weights: ScoreWeights | None = None
) -> PlayResult:
    """Play a scenario with the deliberately bad scripted trainee."""
    return play(scenario, ScriptedBadTrainee(scenario), dt=dt, weights=weights)


# --------------------------------------------------------------------------- #
# Replay
# --------------------------------------------------------------------------- #


def replay(
    scenario: Scenario,
    actions: list[TraineeAction],
    dt: float = 0.1,
    sample_s: float = REPLAY_SAMPLE_S,
    weights: ScoreWeights | None = None,
) -> ReplaySession:
    """Re-run a session from its seed and action log, producing the AAR replay.

    Because the engine is deterministic this reproduces the original
    `final_state_hash` exactly when the same action log is supplied.
    """
    return replay_state(scenario, actions, dt=dt, sample_s=sample_s, weights=weights).session


@dataclass(slots=True)
class ReplayOutcome:
    """A replay and the engine state it ended in.

    The state is kept so a caller that needs the final positions - the
    per-track sector the habit model wants, for instance - does not have to
    simulate the scenario a second time.
    """

    session: ReplaySession
    state: EngineState


def replay_state(
    scenario: Scenario,
    actions: list[TraineeAction],
    dt: float = 0.1,
    sample_s: float = REPLAY_SAMPLE_S,
    weights: ScoreWeights | None = None,
) -> ReplayOutcome:
    """Re-run a session and return both the AAR payload and the final state.

    Because the engine is deterministic this reproduces the original
    `final_state_hash` exactly when the same action log is supplied.
    """
    state = initial_state(scenario, dt=dt)
    rng = np.random.default_rng(int(scenario.seed))
    oracle = OraclePolicyAdapter(oracle_policy(scenario))

    frames: list[Any] = []
    paths: dict[str, list[FrameSample]] = {}
    entities_meta: dict[str, tuple[EntityClass, LabelType]] = {}
    neutralised_at: dict[str, float] = {}
    perimeter_at: dict[str, float] = {}
    next_sample = 0.0
    limit = int(scenario.duration_s / dt) + 2
    pending = sorted(actions, key=lambda a: a.t)

    while state.world.status == "running" and state.world.step < limit:
        t = state.world.t
        # An action recorded at time `t` during the live session was applied on
        # the tick that *started* at `t`, so the same filter is used here.
        due = [a for a in pending if a.t <= t + 1e-9]
        pending = [a for a in pending if a.t > t + 1e-9]
        state = step(state, due, rng)

        if state.world.t + 1e-9 >= next_sample:
            next_sample += sample_s
            frame = build_state_frame(state, "replay")
            frames.append(frame)
            for eid, ent in state.world.entities.items():
                paths.setdefault(eid, []).append(
                    FrameSample(
                        t=round(state.world.t, 2),
                        x=ent.position.x,
                        y=ent.position.y,
                        z=ent.position.z,
                    )
                )
                entities_meta[eid] = (ent.entity_class, ent.label)
                if ent.neutralised and ent.neutralised_t is not None:
                    neutralised_at.setdefault(eid, ent.neutralised_t)
                if ent.reached_perimeter_t is not None:
                    perimeter_at.setdefault(eid, ent.reached_perimeter_t)

        # Keep the ghost overlay synchronised with the replay.
        oracle.act(state, state.world, state.world.t)

    entities = [
        ReplayEntity(
            id=eid,
            entity_class=cls,
            label=lbl,
            path=paths.get(eid, []),
            t_first_detectable=next(
                (e.t_first_detectable for e in state.world.entities.values() if e.id == eid), None
            ),
            ring_crossings={
                str(k): round(float(v), 2)
                for k, v in sorted(state.world.entities[eid].ring_crossings.items())
            }
            if eid in state.world.entities
            else {},
            t_closest_approach=next(
                (e.t_closest_approach for e in state.world.entities.values() if e.id == eid), None
            ),
            neutralised_t=neutralised_at.get(eid),
            reached_perimeter_t=perimeter_at.get(eid),
        )
        for eid, cls, lbl in sorted(
            ((eid, *meta) for eid, meta in entities_meta.items()), key=lambda r: r[0]
        )
    ]

    score = score_session(
        scenario,
        state.world,
        actions,
        get_rule_set(roe_spec_for(scenario).rule_set),
        state.track_entities,
        state.annotations,
        weights,
    )
    ghost_actions = oracle.ghost_actions()
    attention = list(state.sector_attention_s)
    if len(attention) != NUM_SECTORS:
        attention = [0.0] * NUM_SECTORS
    return ReplayOutcome(
        session=ReplaySession(
            session_id="replay",
            scenario_id=scenario.id,
            duration_s=float(state.world.t),
            final_state_hash=state.state_hash(),
            ticks_per_second=int(round(1.0 / dt)),
            trainee_actions=list(actions),
            ghost_actions=ghost_actions,
            entities=entities,
            frames=frames,
            events=list(state.world.events),
            sector_attention=[round(float(a), 2) for a in attention],
            score=score,
        ),
        state=state,
    )


# --------------------------------------------------------------------------- #
# Counterfactual fork
# --------------------------------------------------------------------------- #


def outcome_summary(result: PlayResult) -> dict[str, Any]:
    """Compact outcome used to diff an original run against a fork.

    Public because the browser builds the same diff when it forks a session
    it played itself.
    """
    return outcome_summary_from(result.state, result.score, result.scenario)


def outcome_summary_from(
    state: EngineState, score: ScoreBreakdown, scenario: Scenario
) -> dict[str, Any]:
    """The same summary, from the pieces a caller already holds."""
    world = state.world
    return {
        "hash": state.state_hash(),
        "total": score.total,
        "engagement": score.engagement,
        "detection": score.detection,
        "classification": score.classification,
        "prioritisation": score.prioritisation,
        "workload": score.workload,
        "neutralised": sum(1 for e in world.entities.values() if e.neutralised),
        "breached": sum(
            1
            for e in world.entities.values()
            if e.label is LabelType.HOSTILE and e.reached_perimeter_t is not None
        ),
        "ammunition_used": sum(e.ammunition for e in scenario.effectors)
        - sum(s.ammo for s in world.effectors.values()),
        "verdict_counts": dict(score.verdict_counts),
    }


#: Kept for callers that used the private name.
_outcome_summary = outcome_summary


def fork_session(
    scenario: Scenario,
    actions: list[TraineeAction],
    request: ForkRequest,
    dt: float = 0.1,
    weights: ScoreWeights | None = None,
    follow_with_ghost: bool = True,
    original: PlayResult | None = None,
) -> tuple[PlayResult, PlayResult, ForkResult]:
    """Re-simulate from a decision point with a modified action log.

    Returns ``(original, forked, result)``. When the original log is replayed
    unchanged the fork is a no-op and the hashes must match exactly, which is
    asserted by the AAR tests.

    ``original`` lets a caller that already has the run - the browser, which
    played it live - skip the replay, so a fork costs one simulation instead of
    three.
    """
    if original is None:
        original = play(scenario, _StaticPolicy(actions), dt=dt, weights=weights)

    fork_actions: list[TraineeAction] = []
    for i, a in enumerate(actions):
        if request.replace_index is not None and i == request.replace_index:
            continue
        if request.replace_all_from and a.t >= request.fork_t - 1e-9:
            continue
        fork_actions.append(a)
    if request.inject_action is not None:
        fork_actions.append(request.inject_action)

    appended: list[TraineeAction] = []
    ghost_actions: list[GhostAction] = []
    if follow_with_ghost:
        adapter = OraclePolicyAdapter(oracle_policy(scenario))
        play(scenario, adapter, dt=dt, weights=weights)
        ghost_actions = adapter.ghost_actions()
        appended = [a for a in adapter.recorded if a.t > request.fork_t + 1e-9]
        fork_actions.extend(appended)

    fork_actions.sort(key=lambda a: a.t)
    forked = play(scenario, _StaticPolicy(fork_actions), dt=dt, weights=weights)

    diverged = forked.final_state_hash != original.final_state_hash
    delta = forked.score.total - original.score.total
    if diverged:
        explanation = (
            f"Forking at t={request.fork_t:.1f} s changed the outcome: score "
            f"{original.score.total:.1f} -> {forked.score.total:.1f} ({delta:+.1f}). "
            f"{_describe_change(_outcome_summary(original), _outcome_summary(forked))}"
        )
    else:
        explanation = (
            f"The fork reproduced the original run exactly (hash {forked.final_state_hash}); "
            "the injected change had no effect on the outcome."
        )

    result = ForkResult(
        fork_t=request.fork_t,
        original_hash=original.final_state_hash,
        forked_hash=forked.final_state_hash,
        diverged=diverged,
        original_score=original.score.total,
        forked_score=forked.score.total,
        score_delta=round(delta, 2),
        original_outcome=_outcome_summary(original),
        forked_outcome=_outcome_summary(forked),
        explanation=explanation,
        ghost_actions=list(ghost_actions),
        newly_appended_actions=list(appended),
    )
    return original, forked, result


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


class _StaticPolicy:
    """Replays a fixed action log; used for replays and forks.

    The due-time window matches the one `step` itself applies, so a replayed
    action lands on exactly the same tick it did during the live session.
    """

    def __init__(self, actions: list[TraineeAction]) -> None:
        self.actions = sorted(actions, key=lambda a: a.t)

    def act(self, state: EngineState, world: Any, t: float) -> list[TraineeAction]:
        """Return every not-yet-applied action due at this tick.

        The window matches the one used by `core.session.replay`: an action
        stamped at time `t` is applied on the tick that *starts* at `t`.
        """
        due = [a for a in self.actions if a.t <= t + 1e-9]
        self.actions = [a for a in self.actions if a.t > t + 1e-9]
        return due
