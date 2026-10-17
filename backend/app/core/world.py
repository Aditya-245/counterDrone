"""The world state: entities, effectors, sensor runtime and the state hash.

`WorldState` is a *pure data* container. It contains ground truth because the
simulation needs it; nothing here decides what the trainee is allowed to see.
The only path from a `WorldState` to a client payload goes through
`core.tracking`, which is enforced structurally (DECISIONS D003).
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, field
from typing import Any, Literal, cast

import numpy as np

from app.core.schemas import (
    DegradationState,
    EntityClass,
    EventKind,
    LabelType,
    Scenario,
    SensorSpec,
    SimEvent,
    Vec3,
)

__all__ = [
    "EffectorState",
    "EntityState",
    "SensorRuntime",
    "WorldState",
    "new_world",
]


@dataclass(slots=True)
class EntityState:
    """Ground-truth state of one airborne object."""

    id: str
    entity_class: EntityClass
    label: LabelType
    wave_name: str
    member_index: int
    position: Vec3
    velocity: Vec3
    spawn_t: float
    rf_active: bool
    beacon_active: bool
    hint_bias: float = 0.0
    neutralised: bool = False
    neutralised_t: float | None = None
    neutralised_by: str | None = None
    active: bool = True
    reached_perimeter_t: float | None = None
    ring_crossings: dict[int, float] = field(default_factory=dict)
    t_first_detectable: float | None = None
    first_detected_by: str | None = None
    t_first_detection: float | None = None
    t_closest_approach: float | None = None
    min_range_m: float = float("inf")
    min_altitude_m: float = float("inf")
    engaged_count: int = 0
    engaged_by: str | None = None

    @property
    def speed_mps(self) -> float:
        """Ground speed."""
        return self.velocity.norm()

    def distance_to_site(self, site_x: float = 0.0, site_y: float = 0.0) -> float:
        """Plan-view distance to the defended site."""
        return math.hypot(self.position.x - site_x, self.position.y - site_y)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for save/load round trips."""
        return {
            "id": self.id,
            "entity_class": self.entity_class.value,
            "label": self.label.value,
            "wave_name": self.wave_name,
            "member_index": self.member_index,
            "position": self.position.as_list(),
            "velocity": self.velocity.as_list(),
            "spawn_t": self.spawn_t,
            "rf_active": self.rf_active,
            "beacon_active": self.beacon_active,
            "hint_bias": self.hint_bias,
            "neutralised": self.neutralised,
            "neutralised_t": self.neutralised_t,
            "neutralised_by": self.neutralised_by,
            "active": self.active,
            "reached_perimeter_t": self.reached_perimeter_t,
            "ring_crossings": {str(k): v for k, v in sorted(self.ring_crossings.items())},
            "t_first_detectable": self.t_first_detectable,
            "first_detected_by": self.first_detected_by,
            "t_first_detection": self.t_first_detection,
            "t_closest_approach": self.t_closest_approach,
            "min_range_m": None if math.isinf(self.min_range_m) else self.min_range_m,
            "min_altitude_m": (None if math.isinf(self.min_altitude_m) else self.min_altitude_m),
            "engaged_count": self.engaged_count,
            "engaged_by": self.engaged_by,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntityState:
        """Inverse of `to_dict`."""
        pos = data["position"]
        vel = data["velocity"]
        min_range = data.get("min_range_m")
        min_alt = data.get("min_altitude_m")
        return cls(
            id=str(data["id"]),
            entity_class=EntityClass(data["entity_class"]),
            label=LabelType(data["label"]),
            wave_name=str(data["wave_name"]),
            member_index=int(data["member_index"]),
            position=Vec3(x=float(pos[0]), y=float(pos[1]), z=float(pos[2])),
            velocity=Vec3(x=float(vel[0]), y=float(vel[1]), z=float(vel[2])),
            spawn_t=float(data["spawn_t"]),
            rf_active=bool(data["rf_active"]),
            beacon_active=bool(data["beacon_active"]),
            hint_bias=float(data.get("hint_bias", 0.0)),
            neutralised=bool(data.get("neutralised", False)),
            neutralised_t=data.get("neutralised_t"),
            neutralised_by=data.get("neutralised_by"),
            active=bool(data.get("active", True)),
            reached_perimeter_t=data.get("reached_perimeter_t"),
            ring_crossings={
                int(k): float(v) for k, v in dict(data.get("ring_crossings", {})).items()
            },
            t_first_detectable=data.get("t_first_detectable"),
            first_detected_by=data.get("first_detected_by"),
            t_first_detection=data.get("t_first_detection"),
            t_closest_approach=data.get("t_closest_approach"),
            min_range_m=float("inf") if min_range is None else float(min_range),
            min_altitude_m=float("inf") if min_alt is None else float(min_alt),
            engaged_count=int(data.get("engaged_count", 0)),
            engaged_by=data.get("engaged_by"),
        )


@dataclass(slots=True)
class EffectorState:
    """Mutable per-scenario effector bookkeeping."""

    id: str
    kind: Literal["soft", "hard"]
    ammo: int
    cooldown_s: float
    max_range_m: float
    cooldown_remaining: float = 0.0
    reload_remaining: float = 0.0
    shots_fired: int = 0
    hits: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise."""
        return {
            "id": self.id,
            "kind": self.kind,
            "ammo": self.ammo,
            "cooldown_s": self.cooldown_s,
            "max_range_m": self.max_range_m,
            "cooldown_remaining": self.cooldown_remaining,
            "reload_remaining": self.reload_remaining,
            "shots_fired": self.shots_fired,
            "hits": self.hits,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EffectorState:
        """Inverse of `to_dict`."""
        return cls(
            id=str(data["id"]),
            kind=cast("Literal['soft', 'hard']", str(data["kind"])),
            ammo=int(data["ammo"]),
            cooldown_s=float(data["cooldown_s"]),
            max_range_m=float(data["max_range_m"]),
            cooldown_remaining=float(data["cooldown_remaining"]),
            reload_remaining=float(data["reload_remaining"]),
            shots_fired=int(data.get("shots_fired", 0)),
            hits=int(data.get("hits", 0)),
        )


@dataclass(slots=True)
class SensorRuntime:
    """Per-step sensor state (degradation is a function of time and events)."""

    spec: SensorSpec
    state: DegradationState = DegradationState.NONE
    azimuth_deg: float = 0.0
    drift_x_m: float = 0.0
    drift_y_m: float = 0.0
    note: str = ""

    @property
    def id(self) -> str:
        """Sensor identifier."""
        return self.spec.id

    @property
    def operational(self) -> bool:
        """True when the sensor produces detections at all."""
        return self.state is not DegradationState.FAILED

    def to_dict(self) -> dict[str, Any]:
        """Serialise."""
        return {
            "spec": self.spec.model_dump(mode="json"),
            "state": self.state.value,
            "azimuth_deg": self.azimuth_deg,
            "drift_x_m": self.drift_x_m,
            "drift_y_m": self.drift_y_m,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SensorRuntime:
        """Inverse of `to_dict`."""
        return cls(
            spec=SensorSpec.model_validate(data["spec"]),
            state=DegradationState(data["state"]),
            azimuth_deg=float(data["azimuth_deg"]),
            drift_x_m=float(data.get("drift_x_m", 0.0)),
            drift_y_m=float(data.get("drift_y_m", 0.0)),
            note=str(data.get("note", "")),
        )


@dataclass(slots=True)
class WorldState:
    """Complete simulation state at one instant. Fully serialisable."""

    scenario_id: str
    seed: int
    t: float
    step: int
    dt: float
    duration_s: float
    entities: dict[str, EntityState] = field(default_factory=dict)
    effectors: dict[str, EffectorState] = field(default_factory=dict)
    sensors: dict[str, SensorRuntime] = field(default_factory=dict)
    events: list[SimEvent] = field(default_factory=list)
    spawned_waves: set[str] = field(default_factory=set)
    fired_scenario_events: set[int] = field(default_factory=set)
    status: Literal["running", "paused", "finished"] = "running"
    paused: bool = False
    rng_draws: int = 0
    """Number of random variates consumed; part of the determinism contract."""
    engagement_log: list[dict[str, Any]] = field(default_factory=list)
    counters: dict[str, float] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def active_entities(self) -> list[EntityState]:
        """Entities that are alive and in play."""
        return [e for e in self.entities.values() if e.active and not e.neutralised]

    def entity(self, entity_id: str) -> EntityState | None:
        """Look up an entity by id."""
        return self.entities.get(entity_id)

    def add_event(
        self, kind: EventKind, message: str = "", entity_id: str | None = None, **data: Any
    ) -> SimEvent:
        """Append an event to the world event log and return it."""
        ev = SimEvent(
            t=round(self.t, 4), kind=kind, entity_id=entity_id, message=message, data=data
        )
        self.events.append(ev)
        return ev

    def ring_index_for(self, distance_m: float, rings: list[float]) -> int | None:
        """Index of the innermost ring that `distance_m` has crossed, if any."""
        for idx in range(len(rings) - 1, -1, -1):
            if distance_m <= rings[idx]:
                return idx
        return None

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        """Serialise the whole world for save/load round trips."""
        return {
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "t": self.t,
            "step": self.step,
            "dt": self.dt,
            "duration_s": self.duration_s,
            "entities": [e.to_dict() for _, e in sorted(self.entities.items())],
            "effectors": [x.to_dict() for _, x in sorted(self.effectors.items())],
            "sensors": [s.to_dict() for _, s in sorted(self.sensors.items())],
            "events": [e.model_dump(mode="json") for e in self.events],
            "spawned_waves": sorted(self.spawned_waves),
            "fired_scenario_events": sorted(self.fired_scenario_events),
            "status": self.status,
            "paused": self.paused,
            "rng_draws": self.rng_draws,
            "engagement_log": self.engagement_log,
            "counters": self.counters,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorldState:
        """Inverse of `to_dict`."""
        world = cls(
            scenario_id=str(data["scenario_id"]),
            seed=int(data["seed"]),
            t=float(data["t"]),
            step=int(data["step"]),
            dt=float(data["dt"]),
            duration_s=float(data["duration_s"]),
            spawned_waves=set(data.get("spawned_waves", [])),
            fired_scenario_events={int(i) for i in data.get("fired_scenario_events", [])},
            status=cast("Literal['running', 'paused', 'finished']", data.get("status", "running")),
            paused=bool(data.get("paused", False)),
            rng_draws=int(data.get("rng_draws", 0)),
            engagement_log=list(data.get("engagement_log", [])),
            counters={str(k): float(v) for k, v in dict(data.get("counters", {})).items()},
        )
        for item in data.get("entities", []):
            ent = EntityState.from_dict(item)
            world.entities[ent.id] = ent
        for item in data.get("effectors", []):
            eff = EffectorState.from_dict(item)
            world.effectors[eff.id] = eff
        for item in data.get("sensors", []):
            run = SensorRuntime.from_dict(item)
            world.sensors[run.id] = run
        world.events = [SimEvent.model_validate(e) for e in data.get("events", [])]
        return world

    def clone(self) -> WorldState:
        """Deep copy, used by the counterfactual fork so replays stay independent."""
        return WorldState.from_dict(self.to_dict())

    # ------------------------------------------------------------------ #
    # Hashing
    # ------------------------------------------------------------------ #
    def hash_bytes(self) -> bytes:
        """Canonical little-endian byte encoding of everything that affects outcome."""
        buf = bytearray()
        buf += _pack_str(self.scenario_id)
        buf += struct.pack("<q", self.seed)
        buf += struct.pack("<d", round(self.t, 6))
        buf += struct.pack("<ddq", self.dt, self.duration_s, self.step)
        buf += struct.pack("<q", len(self.entities))
        for eid, ent in sorted(self.entities.items()):
            buf += _pack_str(eid)
            buf += _pack_str(ent.entity_class.value)
            buf += _pack_str(ent.label.value)
            buf += _pack_str(ent.wave_name)
            buf += struct.pack("<i", ent.member_index)
            buf += _pack_vec(ent.position)
            buf += _pack_vec(ent.velocity)
            buf += struct.pack("d?", ent.spawn_t, ent.rf_active)
            buf += struct.pack("?d", ent.beacon_active, ent.hint_bias)
            buf += struct.pack(
                "?dd",
                ent.active,
                ent.neutralised_t if ent.neutralised_t is not None else -1.0,
                ent.reached_perimeter_t if ent.reached_perimeter_t is not None else -1.0,
            )
            buf += struct.pack(
                "?di",
                ent.neutralised,
                ent.t_first_detectable if ent.t_first_detectable is not None else -1.0,
                ent.engaged_count,
            )
            buf += struct.pack("i", len(ent.ring_crossings))
            for ring, when in sorted(ent.ring_crossings.items()):
                buf += struct.pack("id", ring, when)
        buf += struct.pack("<q", len(self.effectors))
        for eid, eff in sorted(self.effectors.items()):
            buf += _pack_str(eid)
            buf += _pack_str(eff.kind)
            buf += struct.pack("<ii", eff.ammo, eff.shots_fired)
            buf += struct.pack("<ddd", eff.cooldown_remaining, eff.reload_remaining, eff.hits)
        buf += struct.pack("<q", len(self.sensors))
        for sid, run in sorted(self.sensors.items()):
            buf += _pack_str(sid)
            buf += _pack_str(run.state.value)
            buf += struct.pack("<ddd", run.azimuth_deg, run.drift_x_m, run.drift_y_m)
        buf += struct.pack("<q", len(self.spawned_waves))
        for name in sorted(self.spawned_waves):
            buf += _pack_str(name)
        buf += struct.pack("<q", len(self.fired_scenario_events))
        for idx in sorted(self.fired_scenario_events):
            buf += struct.pack("<i", idx)
        buf += struct.pack("<qq", self.rng_draws, len(self.engagement_log))
        for item in self.engagement_log:
            buf += _pack_str(str(item.get("t", 0.0)))
            buf += _pack_str(str(item.get("action", "")))
            buf += _pack_str(str(item.get("track_id", "")))
            buf += _pack_str(str(item.get("target_id", "")))
            buf += struct.pack("<d", float(item.get("success", 0.0)))
        return bytes(buf)

    def state_hash(self) -> str:
        """BLAKE2b-128 hex digest of `hash_bytes` (DECISIONS D010)."""
        return hashlib.blake2b(self.hash_bytes(), digest_size=16).hexdigest()


def _pack_str(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<i", len(raw)) + raw


def _pack_vec(v: Vec3) -> bytes:
    return struct.pack("<ddd", _z(v.x), _z(v.y), _z(v.z))


def _z(value: float) -> float:
    """Normalise -0.0 to 0.0 so arithmetically equal states hash equally."""
    return value + 0.0 if value != 0.0 else 0.0


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


def new_world(scenario: Scenario, dt: float = 0.1) -> WorldState:
    """Create the initial world state for `scenario` (nothing spawned yet)."""
    world = WorldState(
        scenario_id=scenario.id,
        seed=int(scenario.seed),
        t=0.0,
        step=0,
        dt=float(dt),
        duration_s=float(scenario.duration_s),
        status="running",
    )
    for spec in scenario.sensors:
        world.sensors[spec.id] = SensorRuntime(
            spec=spec,
            state=spec.initial_state,
            azimuth_deg=0.0,
        )
    for effector in scenario.effectors:
        world.effectors[effector.id] = EffectorState(
            id=effector.id,
            kind=effector.kind,
            ammo=effector.ammunition,
            cooldown_s=effector.cooldown_s,
            max_range_m=effector.max_range_m,
        )
    return world


def entity_rng(seed: int, entity_id: str) -> np.random.Generator:
    """Per-entity deterministic RNG, used by behaviours that need local noise."""
    digest = hashlib.blake2b(entity_id.encode("utf-8"), digest_size=8).digest()
    return np.random.default_rng((seed + int.from_bytes(digest, "little")) % (2**63))
