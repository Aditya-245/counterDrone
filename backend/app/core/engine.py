"""The deterministic fixed-timestep engine.

    state' = step(state, actions, rng)

`step` is a pure function: the same `(state, actions, rng)` always produces the
same `state'`, and the resulting `state_hash()` is byte-identical across runs
and across a save/load round trip. There is no wall-clock time and no use of the
global `random` module anywhere in `core`.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.core.entities import (
    BehaviourContext,
    apply_behaviour,
    formation_offsets,
    params_for,
    resolve_rf_active,
)
from app.core.schemas import (
    ActionType,
    DegradationState,
    EventKind,
    LabelType,
    Scenario,
    ThreatSpec,
    TraineeAction,
    Vec3,
)
from app.core.sensors import Sensor, build_sensors, first_detectable
from app.core.terrain import Terrain, build_terrain
from app.core.tracking import Detection, TrackerConfig, TrackerState, TrackTracker
from app.core.world import EntityState, WorldState, entity_rng, new_world

__all__ = [
    "EngineState",
    "NUM_SECTORS",
    "SimEngine",
    "initial_state",
    "sector_of_bearing",
    "step",
]

#: Number of 45-degree sectors used for the attention heatmap.
NUM_SECTORS = 8


def sector_of_bearing(bearing_deg: float) -> int:
    """Map a bearing in degrees onto one of `NUM_SECTORS` 45-degree sectors."""
    b = bearing_deg % 360.0
    idx = int(b // (360.0 / NUM_SECTORS))
    return min(NUM_SECTORS - 1, max(0, idx))


@dataclass(slots=True)
class TrackAnnotation:
    """Trainee-side notes attached to a track id (never sent as ground truth)."""

    acknowledged: bool = False
    t_ack: float | None = None
    trainee_class: LabelType | None = None
    t_classified: float | None = None
    classify_confidence: float | None = None
    priority: int | None = None
    t_priority: float | None = None
    warned: bool = False
    hold_fire: bool = False
    id_requested: bool = False
    neutralised: bool = False
    missed: bool = False
    first_action_t: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise."""
        return {
            "acknowledged": self.acknowledged,
            "t_ack": self.t_ack,
            "trainee_class": self.trainee_class.value if self.trainee_class else None,
            "t_classified": self.t_classified,
            "classify_confidence": self.classify_confidence,
            "priority": self.priority,
            "t_priority": self.t_priority,
            "warned": self.warned,
            "hold_fire": self.hold_fire,
            "id_requested": self.id_requested,
            "neutralised": self.neutralised,
            "missed": self.missed,
            "first_action_t": self.first_action_t,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrackAnnotation":
        """Inverse of `to_dict`."""
        label = data.get("trainee_class")
        return cls(
            acknowledged=bool(data.get("acknowledged", False)),
            t_ack=data.get("t_ack"),
            trainee_class=LabelType(label) if label else None,
            t_classified=data.get("t_classified"),
            classify_confidence=data.get("classify_confidence"),
            priority=data.get("priority"),
            t_priority=data.get("t_priority"),
            warned=bool(data.get("warned", False)),
            hold_fire=bool(data.get("hold_fire", False)),
            id_requested=bool(data.get("id_requested", False)),
            neutralised=bool(data.get("neutralised", False)),
            missed=bool(data.get("missed", False)),
            first_action_t=data.get("first_action_t"),
        )


@dataclass(slots=True)
class EngineState:
    """Everything the pure `step` function needs, in one serialisable object."""

    scenario: Scenario
    world: WorldState
    tracker: TrackerState
    annotations: dict[str, TrackAnnotation] = field(default_factory=dict)
    sector_attention_s: list[float] = field(default_factory=lambda: [0.0] * NUM_SECTORS)
    allow_pause: bool = False
    _sensors: list[Sensor] | None = field(default=None, repr=False, compare=False)
    _tracker: TrackTracker | None = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------------ #
    @property
    def terrain(self) -> Terrain:
        """Terrain implied by the scenario (cached by `app.core.terrain`)."""
        return build_terrain(self.scenario.terrain)

    @property
    def sensors(self) -> list[Sensor]:
        """Instantiated sensors; rebuilt lazily after a load."""
        if self._sensors is None:
            self._sensors = build_sensors(self.scenario, self.terrain)
        return self._sensors

    @property
    def tracker_engine(self) -> TrackTracker:
        """A thin wrapper around the tracker state (association configuration)."""
        if self._tracker is None:
            self._tracker = TrackTracker(TrackerConfig(), dt=self.world.dt)
            self._tracker.state = self.tracker
            self._tracker.site = (0.0, 0.0)
            self._tracker.protected_radius_m = float(self.scenario.site.protected_radius_m)
            self._tracker.ring_distances_m = tuple(self.scenario.site.ring_distances_m)
        self._tracker.state = self.tracker
        return self._tracker

    # ------------------------------------------------------------------ #
    def state_hash(self) -> str:
        """Deterministic digest over the whole engine state."""
        import hashlib
        import json

        h = hashlib.blake2b(digest_size=16)
        h.update(self.world.hash_bytes())
        h.update(json.dumps(self.tracker.to_dict(), sort_keys=True).encode("utf-8"))
        for tid in sorted(self.annotations):
            h.update(tid.encode("utf-8"))
            h.update(json.dumps(self.annotations[tid].to_dict(), sort_keys=True).encode("utf-8"))
        h.update(np.asarray(self.sector_attention_s, dtype=np.float64).tobytes())
        h.update(b"1" if self.allow_pause else b"0")
        return h.hexdigest()

    def clone(self) -> "EngineState":
        """Deep copy for counterfactual forks."""
        return EngineState(
            scenario=self.scenario,
            world=self.world.clone(),
            tracker=TrackerState.from_dict(self.tracker.to_dict(), TrackerConfig()),
            annotations={
                k: TrackAnnotation.from_dict(v.to_dict()) for k, v in self.annotations.items()
            },
            sector_attention_s=list(self.sector_attention_s),
            allow_pause=self.allow_pause,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise the complete engine state (scenario included) for round trips."""
        return {
            "scenario": self.scenario.model_dump(mode="json"),
            "world": self.world.to_dict(),
            "tracker": self.tracker.to_dict(),
            "annotations": {k: v.to_dict() for k, v in sorted(self.annotations.items())},
            "sector_attention_s": list(self.sector_attention_s),
            "allow_pause": self.allow_pause,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EngineState":
        """Inverse of `to_dict`.

        Deliberately goes through JSON so the round trip is the same code path a
        persisted session takes, rather than a friendlier in-memory shortcut.
        """
        payload = json.loads(json.dumps(data))
        return cls(
            scenario=Scenario.model_validate(payload["scenario"]),
            world=WorldState.from_dict(payload["world"]),
            tracker=TrackerState.from_dict(payload["tracker"], TrackerConfig()),
            annotations={
                k: TrackAnnotation.from_dict(v) for k, v in payload.get("annotations", {}).items()
            },
            sector_attention_s=[float(x) for x in payload.get("sector_attention_s", [0.0] * NUM_SECTORS)],
            allow_pause=bool(payload.get("allow_pause", False)),
        )


def initial_state(scenario: Scenario, dt: float = 0.1, allow_pause: bool = False) -> EngineState:
    """Create the initial engine state for `scenario`."""
    return EngineState(
        scenario=scenario,
        world=new_world(scenario, dt=dt),
        tracker=TrackerState(),
        allow_pause=allow_pause,
    )


# --------------------------------------------------------------------------- #
# The pure step function
# --------------------------------------------------------------------------- #


def step(
    state: EngineState,
    actions: list[TraineeAction],
    rng: np.random.Generator,
) -> EngineState:
    """Advance the simulation by exactly one timestep.

    Pure: `state'` depends only on `state`, `actions` and the *state* of `rng`.
    """
    if state.world.status != "running":
        return state

    counter = _CountingRng(rng)
    nxt = state.clone()
    world = nxt.world

    _apply_scenario_events(nxt, world)
    _advance_sensor_degradations(nxt, world)
    _advance_gps_drift(nxt, world)

    for action in actions:
        _apply_action(nxt, world, action, counter)

    _spawn_due_waves(nxt, world, counter)
    _integrate_entities(nxt, world, counter)
    _update_entity_bookkeeping(nxt, world)

    detections = _observe(nxt, world, counter)
    nxt.tracker_engine.update(
        detections,
        t=world.t,
        site=(0.0, 0.0),
        protected_radius_m=float(nxt.scenario.site.protected_radius_m),
        ring_distances_m=tuple(float(r) for r in nxt.scenario.site.ring_distances_m),
    )
    newly = nxt.tracker_engine.confirm_pending(world.t)
    for tid in newly:
        world.add_event(EventKind.TRACK_INITIATED, f"track {tid} confirmed", track_id=tid)

    _mark_first_detectable(nxt, world)
    _attribute_detections(nxt, world, detections)
    _accumulate_attention(nxt, world)

    world.rng_draws += counter.count
    world.t = round(world.t + world.dt, 9)
    world.step += 1
    if world.t >= world.duration_s - 1e-9:
        world.status = "finished"
        world.add_event(EventKind.END, "scenario complete")
    return nxt


class _CountingRng:
    """Thin pass-through that records how many variates the shared RNG served.

    Every stochastic call inside `step` goes through this object, so
    `WorldState.rng_draws` is a real accounting of RNG consumption and a drift
    in that number between two runs is a determinism bug.
    """

    __slots__ = ("_rng", "count")

    def __init__(self, rng: np.random.Generator) -> None:
        self._rng = rng
        self.count = 0

    def random(self) -> float:
        """Uniform [0, 1)."""
        self.count += 1
        return float(self._rng.random())

    def uniform(self, low: float = 0.0, high: float = 1.0, size: int | None = None) -> Any:
        """Uniform variates."""
        self.count += 1
        return self._rng.uniform(low, high, size)

    def normal(self, loc: float = 0.0, scale: float = 1.0, size: Any = None) -> Any:
        """Normal variates."""
        self.count += 1
        return self._rng.normal(loc, scale, size)

    def poisson(self, lam: float = 1.0, size: Any = None) -> Any:
        """Poisson variates (used by the radar clutter model)."""
        self.count += 1
        return self._rng.poisson(lam, size)

    def integers(self, low: int, high: int | None = None, size: Any = None) -> Any:
        """Discrete uniform integers."""
        self.count += 1
        return self._rng.integers(low, high, size)


# --------------------------------------------------------------------------- #
# Events / degradations
# --------------------------------------------------------------------------- #


def _apply_scenario_events(state: EngineState, world: WorldState) -> None:
    """Fire every scenario event whose time has arrived, exactly once."""
    for idx, ev in enumerate(state.scenario.events):
        if idx in world.fired_scenario_events or ev.t > world.t + 1e-9:
            continue
        world.fired_scenario_events.add(idx)
        if ev.kind in ("sensor_failure", "sensor_partial", "sensor_restore"):
            sid = ev.target or ""
            run = world.sensors.get(sid)
            if run is None:
                continue
            if ev.kind == "sensor_failure":
                run.state = DegradationState.FAILED
            elif ev.kind == "sensor_partial":
                run.state = DegradationState.PARTIAL
            else:
                run.state = DegradationState.NONE
            run.note = ev.description
            world.add_event(
                EventKind.SENSOR_DEGRADED,
                f"{sid} -> {run.state.value}",
                data={"sensor": sid, "state": run.state.value},
            )
        elif ev.kind == "gps_denied":
            for run in world.sensors.values():
                run.note = run.note or "GPS denied"
            world.add_event(EventKind.SENSOR_FAILURE, "GPS denied: positions will drift")
        elif ev.kind == "gps_restored":
            for run in world.sensors.values():
                run.note = ""
            world.add_event(EventKind.SENSOR_RECOVERED, "GPS restored")
        elif ev.kind == "comms_loss":
            world.add_event(EventKind.COMMS_LOSS, ev.description or "comms lost")
        elif ev.kind == "comms_restore":
            world.add_event(EventKind.COMMS_LOSS, ev.description or "comms restored")
        else:
            world.add_event(EventKind.SCENARIO_EVENT, ev.description or ev.kind)


def _advance_sensor_degradations(state: EngineState, world: WorldState) -> None:
    """Apply the per-sensor degradation schedule declared in the scenario."""
    for spec in state.scenario.sensors:
        run = world.sensors.get(spec.id)
        if run is None:
            continue
        for deg in spec.degradations:
            if world.t + 1e-9 >= deg.t:
                run.state = deg.state
                run.note = deg.note
                world.add_event(
                    EventKind.SENSOR_DEGRADED,
                    f"{spec.id} -> {deg.state.value}",
                    data={"sensor": spec.id, "state": deg.state.value},
                )


def _advance_gps_drift(state: EngineState, world: WorldState) -> None:
    """Accumulate a deterministic position drift for GPS-denied sensors."""
    for spec in state.scenario.sensors:
        if spec.drift_mps <= 0.0:
            continue
        run = world.sensors.get(spec.id)
        if run is None:
            continue
        heading = math.radians(_stable_heading(spec.id))
        step_m = float(spec.drift_mps) * world.dt
        run.drift_x_m += step_m * math.cos(heading)
        run.drift_y_m += step_m * math.sin(heading)


def _stable_heading(sensor_id: str) -> float:
    """Deterministic pseudo-heading in degrees derived from the sensor id."""
    acc = 0
    for ch in sensor_id:
        acc = (acc * 131 + ord(ch)) % 360
    return float(acc)


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #


def _apply_action(
    state: EngineState, world: WorldState, action: TraineeAction, rng: np.random.Generator
) -> None:
    """Apply one trainee (or oracle) action at the current tick boundary."""
    if action.t > world.t + world.dt + 1e-6:
        return
    track_id = action.track_id
    ann = nxt_annotation(state, track_id) if track_id else None
    if ann is not None and ann.first_action_t is None:
        ann.first_action_t = world.t

    atype = action.type
    if atype is ActionType.ACK_TRACK and ann is not None:
        ann.acknowledged = True
        if ann.t_ack is None:
            ann.t_ack = world.t
    elif atype is ActionType.CLASSIFY and ann is not None:
        raw = action.params.get("class") or action.params.get("label")
        if raw:
            try:
                ann.trainee_class = LabelType(str(raw))
                ann.t_classified = world.t
                conf = action.params.get("confidence")
                ann.classify_confidence = float(conf) if conf is not None else None
            except ValueError:
                world.add_event(EventKind.SCENARIO_EVENT, f"unknown class label {raw!r}")
    elif atype is ActionType.SET_PRIORITY and ann is not None:
        try:
            ann.priority = int(action.params.get("priority", 1))
            ann.t_priority = world.t
        except (TypeError, ValueError):
            pass
    elif atype is ActionType.STEER_SENSOR:
        sid = str(action.params.get("sensor", ""))
        run = world.sensors.get(sid)
        if run is not None:
            run.azimuth_deg = float(action.params.get("az", run.azimuth_deg)) % 360.0
            if run.spec.fov_deg < 359.9:
                run.azimuth_deg = float(
                    np.clip(run.azimuth_deg, 0.0, 360.0 - run.spec.fov_deg)
                )
        sector = sector_of_bearing(float(action.params.get("az", 0.0)))
        state.sector_attention_s[sector] += world.dt * 1.0
    elif atype is ActionType.WARN and ann is not None:
        ann.warned = True
    elif atype is ActionType.HOLD_FIRE and ann is not None:
        ann.hold_fire = True
    elif atype is ActionType.REQUEST_ID and ann is not None:
        ann.id_requested = True
    elif atype is ActionType.PAUSE_REQUEST:
        if state.allow_pause:
            world.paused = not world.paused
            world.status = "paused" if world.paused else "running"
    elif action.is_engagement():
        _resolve_engagement(state, world, action, track_id, rng)


def nxt_annotation(state: EngineState, track_id: str | None) -> TrackAnnotation | None:
    """Fetch or create the annotation for `track_id`."""
    if not track_id:
        return None
    ann = state.annotations.get(track_id)
    if ann is None:
        ann = TrackAnnotation()
        state.annotations[track_id] = ann
    return ann


# --------------------------------------------------------------------------- #
# Effectors
# --------------------------------------------------------------------------- #


def engagement_success_probability(
    scenario: Scenario,
    world: WorldState,
    effector_id: str,
    target: EntityState,
    range_m: float,
    track_quality: float,
) -> float:
    """Abstract engagement success probability in [0, 1].

    Depends on range, track quality, target speed and class susceptibility.
    All magnitudes are invented for simulation; nothing here is derived from
    any real system.
    """
    spec = next((e for e in scenario.effectors if e.id == effector_id), None)
    if spec is None:
        return 0.0
    span = max(1e-6, spec.max_range_m - spec.min_range_m)
    frac = float(np.clip((range_m - spec.min_range_m) / span, 0.0, 1.0))
    range_term = (1.0 - frac) ** 0.85
    quality_term = 0.25 + 0.75 * float(np.clip(track_quality, 0.0, 1.0))
    params = params_for(target.entity_class)
    if spec.kind == "soft":
        sus = params.susceptibility
        if not target.rf_active and spec.susceptible_to_rf_silence:
            sus *= 0.35
        speed_term = 1.0 / (1.0 + target.speed_mps / 30.0)
        base = spec.base_success
    else:
        sus = 1.0
        speed_term = float(np.clip(1.0 - (target.speed_mps - params.max_speed_mps * 0.5) / 25.0, 0.15, 1.0))
        base = spec.base_success * 0.85
    return float(np.clip(base * range_term * quality_term * speed_term * (0.5 + 0.5 * sus), 0.0, 0.99))


def _resolve_engagement(
    state: EngineState,
    world: WorldState,
    action: TraineeAction,
    track_id: str | None,
    rng: np.random.Generator,
) -> None:
    """Execute an engagement action: check readiness, roll success, update state."""
    eff_id = action.effector_id() or ""
    if not eff_id and state.scenario.effectors:
        eff_id = state.scenario.effectors[0].id
    spec = next((e for e in state.scenario.effectors if e.id == eff_id), None)
    runtime = world.effectors.get(eff_id)
    if spec is None or runtime is None:
        world.add_event(
            EventKind.ENGAGEMENT,
            f"unknown effector {eff_id!r}",
            track_id=track_id,
        )
        return

    trk = state.tracker_engine.state.tracks.get(track_id or "")
    target_id = trk.entity_hint() if trk is not None else None
    target = world.entities.get(target_id) if target_id else None
    if target is None or not target.active or target.neutralised:
        world.engagement_log.append(
            {
                "t": world.t,
                "action": action.type.value,
                "effector": eff_id,
                "track_id": track_id or "",
                "target_id": "",
                "range_m": 0.0,
                "success": 0.0,
                "reason": "no_valid_target",
            }
        )
        world.add_event(EventKind.ENGAGEMENT, "no valid target", track_id=track_id)
        return

    px, py, pz = target.position.x, target.position.y, target.position.z
    range_m = math.dist((px, py, pz), (spec.x, spec.y, spec.z))
    if range_m > spec.max_range_m or range_m < spec.min_range_m:
        world.engagement_log.append(
            {
                "t": world.t,
                "action": action.type.value,
                "effector": eff_id,
                "track_id": track_id or "",
                "target_id": target.id,
                "range_m": range_m,
                "success": 0.0,
                "reason": "out_of_range",
            }
        )
        world.add_event(
            EventKind.ENGAGEMENT,
            f"out of range ({range_m:.0f} m > {spec.max_range_m:.0f} m)",
            entity_id=target.id,
            track_id=track_id,
            data={"range_m": round(range_m, 2)},
        )
        return

    if runtime.cooldown_remaining > 0.0 or runtime.reload_remaining > 0.0:
        world.engagement_log.append(
            {
                "t": world.t,
                "action": action.type.value,
                "effector": eff_id,
                "track_id": track_id or "",
                "target_id": target.id,
                "range_m": range_m,
                "success": 0.0,
                "reason": "cooldown",
            }
        )
        world.add_event(EventKind.ENGAGEMENT, "effector cooling down", track_id=track_id)
        return

    if runtime.ammo <= 0:
        if spec.reload_s <= 0.0:
            world.engagement_log.append(
                {
                    "t": world.t,
                    "action": action.type.value,
                    "effector": eff_id,
                    "track_id": track_id or "",
                    "target_id": target.id,
                    "range_m": range_m,
                    "success": 0.0,
                    "reason": "no_ammunition",
                }
            )
            world.add_event(EventKind.ENGAGEMENT, "no ammunition", track_id=track_id)
            return
        runtime.reload_remaining = spec.reload_s

    quality = trk.quality if trk is not None else 0.2
    prob = engagement_success_probability(
        state.scenario, world, eff_id, target, range_m, quality
    )
    success = bool(float(rng.random()) < prob)
    runtime.ammo -= 1
    runtime.shots_fired += 1
    runtime.cooldown_remaining = spec.cooldown_s
    target.engaged_count += 1
    target.engaged_by = eff_id
    ann = nxt_annotation(state, track_id)
    if ann is not None:
        ann.neutralised = success

    if success:
        runtime.hits += 1
        target.neutralised = True
        target.neutralised_t = world.t
        target.neutralised_by = eff_id
        target.active = False
        world.add_event(
            EventKind.ENGAGEMENT_RESULT,
            f"{target.id} neutralised by {eff_id}",
            entity_id=target.id,
            track_id=track_id,
            data={"p_success": round(prob, 3), "range_m": round(range_m, 1)},
        )
    else:
        world.add_event(
            EventKind.ENGAGEMENT_RESULT,
            f"engagement on {target.id} missed",
            entity_id=target.id,
            track_id=track_id,
            data={"p_success": round(prob, 3), "range_m": round(range_m, 1)},
        )

    world.engagement_log.append(
        {
            "t": world.t,
            "action": action.type.value,
            "effector": eff_id,
            "track_id": track_id or "",
            "target_id": target.id,
            "range_m": range_m,
            "success": 1.0 if success else 0.0,
            "p_success": prob,
            "reason": "fired",
        }
    )


def _advance_cooldowns(world: WorldState) -> None:
    """Tick effector cooldowns and reload timers down."""
    for eff in world.effectors.values():
        if eff.cooldown_remaining > 0.0:
            eff.cooldown_remaining = max(0.0, eff.cooldown_remaining - world.dt)
        if eff.reload_remaining > 0.0:
            eff.reload_remaining = max(0.0, eff.reload_remaining - world.dt)


# --------------------------------------------------------------------------- #
# Spawning
# --------------------------------------------------------------------------- #


def _spawn_due_waves(state: EngineState, world: WorldState, rng: np.random.Generator) -> None:
    """Spawn any wave whose window has opened and that has not spawned yet."""
    for wave in state.scenario.all_waves:
        if wave.name in world.spawned_waves:
            continue
        if world.t + 1e-9 < wave.spawn_t:
            continue
        _spawn_wave(state, world, wave, rng)
        world.spawned_waves.add(wave.name)


def _spawn_wave(
    state: EngineState, world: WorldState, wave: ThreatSpec, rng: np.random.Generator
) -> None:
    """Instantiate every member of `wave` at its scheduled spawn point."""
    site = state.scenario.site
    count = max(1, wave.count)
    spacing = max(45.0, 18.0 * math.sqrt(count))
    offsets = formation_offsets(wave.formation, count, spacing)
    rf_default = resolve_rf_active(wave)
    for i in range(wave.count):
        jitter_t = 0.0
        if wave.spawn_window_s > 0.0:
            jitter_t = float(rng.uniform(0.0, wave.spawn_window_s))
        t_spawn = wave.spawn_t + jitter_t
        bearing = wave.spawn_bearing_deg
        if wave.spawn_jitter_deg > 0.0:
            bearing = (bearing + float(rng.uniform(-wave.spawn_jitter_deg, wave.spawn_jitter_deg))) % 360.0
        if wave.spawn_points and i < len(wave.spawn_points):
            bp = wave.spawn_points[i]
            bearing = bp.bearing_deg
            slant = bp.range_m
            alt = bp.altitude_m
        else:
            slant = float(rng.uniform(2200.0, 4600.0))
            alt = float(rng.uniform(90.0, 380.0))
        radius = slant
        off = offsets[i]
        # Rotate the formation offset into the approach direction.
        th = math.radians(bearing)
        ox = off.x * math.cos(th) - off.y * math.sin(th)
        oy = off.x * math.sin(th) + off.y * math.cos(th)
        ox += wave.spawn_offset_m * math.cos(th)
        oy += wave.spawn_offset_m * math.sin(th)
        x = ox + radius * math.cos(th)
        y = oy + radius * math.sin(th)
        ground = state.terrain.height_at(x, y)
        z = max(ground + 12.0, alt)

        ent_id = f"{_slug(wave.name)}-{i + 1:02d}"
        ent = EntityState(
            id=ent_id,
            entity_class=wave.entity_class,
            label=wave.label,
            wave_name=wave.name,
            member_index=i,
            position=Vec3(x=x, y=y, z=z),
            velocity=Vec3(
                x=-math.cos(th) * params_for(wave.entity_class).loiter_speed_mps,
                y=-math.sin(th) * params_for(wave.entity_class).loiter_speed_mps,
                z=0.0,
            ),
            spawn_t=t_spawn,
            rf_active=rf_default if wave.rf_active is None else bool(wave.rf_active),
            beacon_active=bool(wave.beacon_active) if wave.beacon_active is not None else (wave.label is LabelType.FRIENDLY),
            hint_bias=float(wave.hint_bias),
        )
        world.entities[ent_id] = ent
        world.add_event(
            EventKind.SPAWN,
            f"{ent_id} ({wave.entity_class.value}/{wave.label.value}) inbound",
            entity_id=ent_id,
            data={"wave": wave.name, "bearing_deg": round(bearing, 1)},
        )


def _slug(name: str) -> str:
    """Short, stable, id-safe prefix for a wave name."""
    out = "".join(ch if ch.isalnum() else "_" for ch in name)
    return out[:16] or "wave"


# --------------------------------------------------------------------------- #
# Integration
# --------------------------------------------------------------------------- #


def _integrate_entities(state: EngineState, world: WorldState, rng: np.random.Generator) -> None:
    """Advance every active entity one timestep using its behaviour.

    Entity-local randomness comes from `entity_rng(world.seed, entity_id)` so it
    does not depend on how many other entities stepped first; `rng` is only
    consulted for bookkeeping here.
    """
    terrain = state.terrain
    by_wave: dict[str, list[EntityState]] = {}
    for ent in world.active_entities():
        by_wave.setdefault(ent.wave_name, []).append(ent)

    waves = {w.name: w for w in state.scenario.all_waves}
    _advance_cooldowns(world)

    for wave_name, members in by_wave.items():
        spec = waves.get(wave_name)
        if spec is None:
            continue
        positions = [m.position for m in members]
        leader_vel = members[0].velocity if members else Vec3()
        for ent in members:
            erng = entity_rng(world.seed, ent.id)
            ctx = BehaviourContext(
                site_x=0.0,
                site_y=0.0,
                t=world.t - ent.spawn_t,
                ground_height=terrain.height_at(ent.position.x, ent.position.y),
                members=[p for p in positions if p is not ent.position],
                leader_vel=leader_vel,
            )
            new_pos, new_vel, _ = apply_behaviour(
                ent.position,
                ent.velocity,
                params_for(ent.entity_class),
                spec.behaviour,
                ctx,
                erng,
                world.dt,
            )
            half = terrain.half_extent_m
            new_pos = Vec3(
                x=float(np.clip(new_pos.x, -half, half)),
                y=float(np.clip(new_pos.y, -half, half)),
                z=new_pos.z,
            )
            ground = terrain.height_at(new_pos.x, new_pos.y)
            new_pos = Vec3(x=new_pos.x, y=new_pos.y, z=max(new_pos.z, ground + 3.0))
            ent.position = new_pos
            ent.velocity = new_vel


def _update_entity_bookkeeping(state: EngineState, world: WorldState) -> None:
    """Record ring crossings, closest approach and perimeter arrival."""
    rings = state.scenario.site.ring_distances_m
    protected = float(state.scenario.site.protected_radius_m)
    for ent in world.entities.values():
        d = ent.distance_to_site()
        if d < ent.min_range_m:
            ent.min_range_m = d
            ent.t_closest_approach = world.t
        alt_agl = ent.position.z - state.terrain.height_at(ent.position.x, ent.position.y)
        if alt_agl < ent.min_altitude_m:
            ent.min_altitude_m = alt_agl
        for idx, ring in enumerate(rings):
            if idx in ent.ring_crossings:
                continue
            if d <= float(ring):
                ent.ring_crossings[idx] = world.t
                world.add_event(
                    EventKind.RING_CROSSING,
                    f"{ent.id} crossed ring {ring:.0f} m",
                    entity_id=ent.id,
                    data={"ring_index": idx, "ring_m": float(ring)},
                )
        if d <= protected and ent.active and not ent.neutralised:
            if ent.reached_perimeter_t is None:
                ent.reached_perimeter_t = world.t
                world.add_event(
                    EventKind.RING_CROSSING,
                    f"{ent.id} reached the protected zone",
                    entity_id=ent.id,
                    data={"perimeter": True},
                )
            ent.active = False


# --------------------------------------------------------------------------- #
# Perception
# --------------------------------------------------------------------------- #


def _observe(state: EngineState, world: WorldState, rng: np.random.Generator) -> list[Detection]:
    """Run every operational sensor once and concatenate their detections."""
    out: list[Detection] = []
    for sensor in state.sensors:
        out.extend(sensor.observe(world, rng))
    return out


def _mark_first_detectable(state: EngineState, world: WorldState) -> None:
    """Record the noise-free first-detectable time for each entity."""
    for ent in world.active_entities():
        if ent.t_first_detectable is not None:
            continue
        if first_detectable(state.sensors, world, ent):
            ent.t_first_detectable = world.t


def _attribute_detections(
    state: EngineState, world: WorldState, detections: list[Detection]
) -> None:
    """Record the first real detection time per entity (drives detection latency)."""
    for det in detections:
        if not det.entity_id:
            continue
        ent = world.entities.get(det.entity_id)
        if ent is None:
            continue
        if ent.t_first_detection is None:
            ent.t_first_detection = world.t
            ent.first_detected_by = det.sensor_id


def _accumulate_attention(state: EngineState, world: WorldState) -> None:
    """Accumulate sector-attention seconds for the AAR heatmap."""
    for tid, ann in state.annotations.items():
        if ann.first_action_t is None:
            continue
        trk = state.tracker_engine.state.tracks.get(tid)
        if trk is None:
            continue
        p = trk.position
        bearing = math.degrees(math.atan2(float(p[1]), float(p[0]))) % 360.0
        state.sector_attention_s[sector_of_bearing(bearing)] += world.dt * 0.35


# --------------------------------------------------------------------------- #
# Convenience runner used by scripts, tests and the oracle
# --------------------------------------------------------------------------- #


class SimEngine:
    """Thin driver around the pure `step` function.

    It owns the scenario, the seeded generator and the tick counter, and knows
    nothing the pure step does not.
    """

    def __init__(self, scenario: Scenario, dt: float = 0.1, allow_pause: bool = False) -> None:
        self.scenario = scenario
        self.dt = float(dt)
        self.state = initial_state(scenario, dt=dt, allow_pause=allow_pause)
        self.rng = np.random.default_rng(int(scenario.seed))
        self.actions: list[TraineeAction] = []

    @property
    def step_index(self) -> int:
        """Number of completed steps."""
        return self.state.world.step

    @property
    def t(self) -> float:
        """Current simulation time."""
        return self.state.world.t

    @property
    def finished(self) -> bool:
        """True once the scenario has run to completion."""
        return self.state.world.status == "finished"

    def queue(self, action: TraineeAction) -> None:
        """Queue an action to be applied at the next tick boundary."""
        self.actions.append(action)

    def tick(self) -> EngineState:
        """Advance exactly one step."""
        due = [a for a in self.actions if a.t <= self.state.world.t + 1e-9]
        self.actions = [a for a in self.actions if a.t > self.state.world.t + 1e-9]
        self.state = step(self.state, due, self.rng)
        return self.state

    def run(self, max_steps: int | None = None) -> EngineState:
        """Run to completion (or `max_steps`)."""
        limit = max_steps if max_steps is not None else int(self.scenario.duration_s / self.dt) + 2
        while not self.finished and self.step_index < limit:
            self.tick()
        return self.state

    def state_hash(self) -> str:
        """Current deterministic state hash."""
        return self.state.state_hash()