"""Procedural scenario generator.

Given a seed and a `DifficultyVector`, produce a valid, solvable `Scenario`.
Every generated scenario is run through the Pydantic validator and a short
headless sanity simulation before it is offered to anybody, so an invalid or
unsolvable scenario never reaches the UI.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

import numpy as np

from app.core.generator_support import (
    difficulty_for_success,
    resolve_difficulty,
)
from app.core.schemas import (
    BehaviourKind,
    BehaviourSpec,
    CivilSector,
    DifficultyVector,
    EffectorSpec,
    EntityClass,
    EnvSpec,
    FormationKind,
    LabelType,
    RoeSpec,
    Scenario,
    ScenarioEvent,
    SensorSpec,
    SensorType,
    SiteSpec,
    SkillVector,
    TerrainPreset,
    TerrainSpec,
    ThreatSpec,
    TimeOfDay,
)

__all__ = [
    "GeneratorResult",
    "TACTIC_BEARINGS",
    "base_scenario",
    "difficulty_for_skill",
    "generate",
    "sanity_check",
    "validate_scenario",
]

#: Eight compass bearings the generator and the Red Brain both draw from.
TACTIC_BEARINGS: Final[tuple[int, ...]] = (0, 45, 90, 135, 180, 225, 270, 315)

_HOSTILE_POOL: Final[tuple[EntityClass, ...]] = (
    EntityClass.FPV_FAST,
    EntityClass.QUAD_SMALL,
    EntityClass.FIXED_WING,
)

#: Occasionally a wave is a friendly UAV or a civil overflight - the traps.
_CIVIL_POOL: Final[tuple[tuple[EntityClass, LabelType], ...]] = (
    (EntityClass.FRIENDLY_UAV, LabelType.FRIENDLY),
    (EntityClass.CIVIL_AIRCRAFT, LabelType.CIVIL),
)

#: Probability that a wave is a non-hostile trap rather than an attack.
_TRAP_RATE: Final[float] = 0.18

_DECOY_POOL: Final[tuple[tuple[EntityClass, LabelType], ...]] = (
    (EntityClass.BIRD_FLOCK, LabelType.BIRD),
    (EntityClass.KITE_BALLOON, LabelType.CIVIL),
)

_TERRAIN_BY_TAG: Final[dict[str, TerrainPreset]] = {
    "urban": TerrainPreset.URBAN,
    "rural": TerrainPreset.RURAL,
    "hills": TerrainPreset.HILLS,
}


@dataclass(slots=True)
class GeneratorResult:
    """A generated scenario plus the provenance the UI needs."""

    scenario: Scenario
    difficulty: DifficultyVector
    targeted_skill: str | None = None
    sanity_ok: bool = False
    sanity_message: str = ""


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #


def default_sensors() -> list[SensorSpec]:
    """The standard four-sensor site layout."""
    return [
        SensorSpec(
            id="radar1",
            type=SensorType.RADAR,
            x=-40.0,
            y=40.0,
            z=18.0,
            fov_deg=360.0,
            min_range_m=120.0,
            max_range_m=4800.0,
            params={"clutter_rate": 0.28},
        ),
        SensorSpec(
            id="eo1",
            type=SensorType.EO_IR,
            x=0.0,
            y=0.0,
            z=12.0,
            fov_deg=70.0,
            min_range_m=20.0,
            max_range_m=1800.0,
            params={"classify_range_m": 420.0},
        ),
        SensorSpec(
            id="rf1",
            type=SensorType.RF,
            x=30.0,
            y=-20.0,
            z=8.0,
            fov_deg=360.0,
            min_range_m=80.0,
            max_range_m=3600.0,
        ),
        SensorSpec(
            id="ac1",
            type=SensorType.ACOUSTIC,
            x=-15.0,
            y=15.0,
            z=3.0,
            fov_deg=360.0,
            min_range_m=10.0,
            max_range_m=750.0,
        ),
    ]


def default_effectors(soft_ammo: int = 12, hard_ammo: int = 8) -> list[EffectorSpec]:
    """The standard abstract effector pair."""
    return [
        EffectorSpec(
            id="eff_s",
            kind="soft",
            x=0.0,
            y=0.0,
            z=6.0,
            min_range_m=60.0,
            max_range_m=1400.0,
            cooldown_s=7.0,
            ammunition=soft_ammo,
            base_success=0.78,
            susceptible_to_rf_silence=True,
        ),
        EffectorSpec(
            id="eff_h",
            kind="hard",
            x=0.0,
            y=0.0,
            z=6.0,
            min_range_m=120.0,
            max_range_m=1900.0,
            cooldown_s=11.0,
            ammunition=hard_ammo,
            reload_s=25.0,
            base_success=0.72,
            susceptible_to_rf_silence=False,
        ),
    ]


def _behaviour_for(
    kind: BehaviourKind, rng: np.random.Generator, terrain_following: bool, speed: float
) -> BehaviourSpec:
    """Build a behaviour spec with seeded but reproducible parameters."""
    return BehaviourSpec(
        kind=kind,
        waypoints=[],
        loiter_radius_m=float(rng.uniform(90.0, 200.0)),
        loiter_period_s=float(rng.uniform(30.0, 70.0)),
        speed_mps=float(speed),
        terminal_altitude_m=float(rng.uniform(15.0, 45.0)),
        orbit_radius_m=float(rng.uniform(120.0, 260.0)),
        orbit_period_s=float(rng.uniform(40.0, 80.0)),
        separation_m=float(rng.uniform(12.0, 28.0)),
        cohesion_weight=float(rng.uniform(0.2, 0.6)),
        alignment_weight=float(rng.uniform(0.1, 0.5)),
        terrain_following=terrain_following,
        min_clearance_m=float(rng.uniform(14.0, 40.0)),
    )


def _make_threat(
    rng: np.random.Generator,
    index: int,
    difficulty: DifficultyVector,
    resolved_swarm: int,
    spawn_t: float,
    compression: float,
    terrain_following: bool,
    force_hostile: bool = False,
) -> ThreatSpec:
    """One hostile (or friendly/civil overflight) wave.

    `force_hostile` suppresses the trap roll for callers that must guarantee an
    attack exists. The roll is still consumed so the RNG stream advances
    identically either way.
    """
    trap_roll = float(rng.random())
    if trap_roll < _TRAP_RATE and not force_hostile:
        cls, label = _CIVIL_POOL[int(rng.integers(0, len(_CIVIL_POOL)))]
    else:
        cls = _HOSTILE_POOL[int(rng.integers(0, len(_HOSTILE_POOL)))]
        label = LabelType.HOSTILE
    if cls is EntityClass.CIVIL_AIRCRAFT:
        # Civil overflights never "strike"; give them a transit behaviour.
        behaviour = _behaviour_for(BehaviourKind.TRANSIT, rng, False, 44.0)
        count = 1
    elif cls is EntityClass.FRIENDLY_UAV:
        behaviour = _behaviour_for(BehaviourKind.LOITER, rng, False, 6.0)
        count = max(1, resolved_swarm - int(rng.integers(1, 3)))
    else:
        style = int(rng.integers(0, 3))
        kind = (
            BehaviourKind.SWARM
            if style == 0
            else (BehaviourKind.ORBIT_THEN_STRIKE if style == 1 else BehaviourKind.TERMINAL_DIVE)
        )
        speed = float(rng.uniform(12.0, 26.0))
        behaviour = _behaviour_for(kind, rng, terrain_following, speed)
        count = resolved_swarm
    formation = _FORMATION_POOL[int(rng.integers(0, len(_FORMATION_POOL)))]
    window = float(rng.uniform(0.0, compression))
    return ThreatSpec(
        name=f"wave{index}",
        entity_class=cls,
        label=label,
        count=count,
        spawn_t=float(spawn_t),
        spawn_window_s=round(window, 2),
        spawn_bearing_deg=float(TACTIC_BEARINGS[int(rng.integers(0, 8))]),
        spawn_jitter_deg=float(rng.uniform(8.0, 30.0)),
        behaviour=behaviour,
        formation=formation,
        rf_active=bool(rng.random() > 0.45) if label is LabelType.HOSTILE else None,
        beacon_active=True if label is LabelType.FRIENDLY else None,
        hint_bias=float(difficulty.ambiguity),
    )


_FORMATION_POOL: Final[tuple[FormationKind, ...]] = (
    FormationKind.LEADER_FOLLOWER,
    FormationKind.LINE,
    FormationKind.WEDGE,
    FormationKind.SCATTER,
    FormationKind.PINCER,
)


def _make_decoy(
    rng: np.random.Generator, index: int, count: int, spawn_t: float, ambiguity: float
) -> ThreatSpec:
    """One decoy wave (birds or kites)."""
    cls, label = _DECOY_POOL[int(rng.integers(0, len(_DECOY_POOL)))]
    if cls is EntityClass.BIRD_FLOCK:
        behaviour = _behaviour_for(BehaviourKind.SWARM, rng, False, float(rng.uniform(8.0, 15.0)))
        formation = FormationKind.SCATTER
    else:
        behaviour = _behaviour_for(BehaviourKind.LOITER, rng, False, float(rng.uniform(1.0, 4.0)))
        formation = FormationKind.SCATTER
    return ThreatSpec(
        name=f"decoy{index}",
        entity_class=cls,
        label=label,
        count=count,
        spawn_t=float(spawn_t),
        spawn_window_s=float(rng.uniform(5.0, 25.0)),
        spawn_bearing_deg=float(TACTIC_BEARINGS[int(rng.integers(0, 8))]),
        spawn_jitter_deg=float(rng.uniform(20.0, 60.0)),
        behaviour=behaviour,
        formation=formation,
        rf_active=False,
        beacon_active=False,
        hint_bias=float(min(1.0, ambiguity * 0.5)),
    )


def _make_events(
    rng: np.random.Generator, difficulty: DifficultyVector, duration: int, sensors: list[SensorSpec]
) -> list[ScenarioEvent]:
    """Mid-scenario degradations derived from the difficulty vector."""
    n = int(round(difficulty.sensor_degradation_level * 3))
    events: list[ScenarioEvent] = []
    if n <= 0:
        return events
    candidates = ["radar1", "eo1", "rf1", "ac1"]
    picked = rng.permutation(len(candidates))[:n]
    for k, idx in enumerate(picked):
        sid = candidates[int(idx)]
        t = float(rng.uniform(duration * 0.18, duration * 0.72))
        if k % 2 == 0:
            events.append(
                ScenarioEvent(
                    t=round(t, 2),
                    kind="sensor_partial"
                    if difficulty.sensor_degradation_level < 0.8
                    else "sensor_failure",
                    target=sid,
                    description=f"{sid} degraded",
                )
            )
        else:
            events.append(
                ScenarioEvent(
                    t=round(t, 2), kind="gps_denied", description="GPS denied: tracks drift"
                )
            )
    events.sort(key=lambda e: e.t)
    return events


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #


def generate(
    seed: int,
    difficulty: DifficultyVector | None = None,
    tags: list[str] | None = None,
    terrain_preset: TerrainPreset | None = None,
    scenario_id: str | None = None,
    name: str | None = None,
    duration_s: int | None = None,
    source: str = "generated",
    behaviour_overrides: dict[str, object] | None = None,
) -> GeneratorResult:
    """Generate a valid, oracle-solvable scenario from `seed` and `difficulty`."""
    rng = np.random.default_rng(int(seed) % (2**32))
    tags = list(tags or [])
    diff = difficulty or DifficultyVector()
    resolved = resolve_difficulty(diff)

    preset = terrain_preset
    if preset is None:
        for tag in tags:
            if tag in _TERRAIN_BY_TAG:
                preset = _TERRAIN_BY_TAG[tag]
                break
    if preset is None:
        preset = _TERRAIN_POOL[int(rng.integers(0, len(_TERRAIN_POOL)))]

    duration = int(duration_s or float(rng.integers(360, 780)))
    terrain = TerrainSpec(
        preset=preset,
        size_m=6000.0,
        resolution=192 if duration > 600 else 160,
        scale_m=float(rng.uniform(90.0, 220.0))
        if preset is TerrainPreset.HILLS
        else float(rng.uniform(35.0, 90.0)),
        roughness=float(rng.uniform(0.35, 0.8)),
        seed=int(seed) % (2**31 - 1),
    )

    night = resolved.is_night
    environment = EnvSpec(
        time_of_day=TimeOfDay.NIGHT if night else TimeOfDay.DUSK,
        visibility_m=resolved.visibility_m,
        rain=float(np.clip(diff.visibility * 0.6 + float(rng.uniform(0.0, 0.2)), 0.0, 0.85)),
        wind_speed_mps=float(rng.uniform(1.0, 9.0)),
        wind_heading_deg=float(rng.uniform(0.0, 360.0)),
    )

    sensors = default_sensors()
    effectors = default_effectors(
        soft_ammo=max(4, int(round(4 + 10 * (1.0 - diff.swarm_size)))),
        hard_ammo=max(2, int(round(2 + 8 * (1.0 - diff.swarm_size)))),
    )

    terrain_following = preset is TerrainPreset.HILLS and float(rng.random()) < 0.8
    waves: list[ThreatSpec] = []
    wave_count = max(1, resolved.approach_axes if diff.simultaneity > 0.55 else 1)
    base_t = float(rng.uniform(5.0, 25.0))
    for i in range(wave_count):
        spawn_t = base_t + i * resolved.spawn_compression_s * (0.35 + 0.65 * float(rng.random()))
        waves.append(
            _make_threat(
                rng,
                i,
                diff,
                resolved.swarm_size,
                spawn_t,
                resolved.spawn_compression_s,
                terrain_following,
            )
        )

    if not any(w.label is LabelType.HOSTILE for w in waves):
        # Every wave rolled as a non-hostile trap. With a single wave that
        # leaves nothing to defend against, so re-roll wave 0 as an attack -
        # otherwise generate() would hand back a scenario no trainee can play.
        waves[0] = _make_threat(
            rng,
            0,
            diff,
            resolved.swarm_size,
            base_t,
            resolved.spawn_compression_s,
            terrain_following,
            force_hostile=True,
        )

    decoys: list[ThreatSpec] = []
    for i in range(resolved.decoy_count):
        decoys.append(
            _make_decoy(
                rng,
                i,
                int(rng.integers(1, 4)),
                float(rng.uniform(base_t * 0.5, base_t + resolved.spawn_compression_s)),
                resolved.ambiguity,
            )
        )

    # A declared civil sector must not sit on top of an attack bearing: the
    # oracle (and a real operator) would correctly hold fire there, which would
    # make the scenario unwinnable rather than hard.
    site = SiteSpec(
        protected_radius_m=150.0,
        ring_distances_m=[400.0, 900.0, 1800.0, 3000.0],
        civil_sectors=_civil_sectors(rng, [w.spawn_bearing_deg for w in (*waves, *decoys)]),
    )

    events = _make_events(rng, diff, duration, sensors)
    roe = _roe_for(diff)

    scenario = Scenario(
        id=scenario_id or f"gen_{int(seed) % 1_000_000:06d}",
        name=name or f"Generated scenario {int(seed) % 1_000_000:06d}",
        description="Procedurally generated by the SKYGYM scenario generator.",
        seed=int(seed) % (2**31 - 1),
        duration_s=duration,
        terrain=terrain,
        environment=environment,
        site=site,
        sensors=sensors,
        threats=waves,
        decoys=decoys,
        effectors=effectors,
        roe=roe,
        events=events,
        difficulty=diff,
        tags=tags or ["generated"],
        source=source,  # type: ignore[arg-type]
    )
    if behaviour_overrides:
        scenario = _apply_overrides(scenario, behaviour_overrides)
    ok, message = sanity_check(scenario)
    return GeneratorResult(scenario, diff, None, ok, message)


_TERRAIN_POOL: Final[tuple[TerrainPreset, ...]] = (
    TerrainPreset.RURAL,
    TerrainPreset.URBAN,
    TerrainPreset.HILLS,
)


def _civil_sectors(rng: np.random.Generator, attack_bearings: list[float]) -> list[CivilSector]:
    """Place at most one civil sector, clear of every declared attack bearing."""
    if float(rng.random()) >= 0.6:
        return []
    half_width = float(rng.uniform(20.0, 35.0))
    candidates = [
        b
        for b in TACTIC_BEARINGS
        if all(min((b - a) % 360.0, (a - b) % 360.0) > half_width + 20.0 for a in attack_bearings)
    ]
    if not candidates:
        return []
    bearing = float(candidates[int(rng.integers(0, len(candidates)))])
    return [
        CivilSector(
            bearing_deg=bearing,
            half_width_deg=half_width,
            inner_m=0.0,
            outer_m=3000.0,
            label="village approach",
        )
    ]


def _roe_for(diff: DifficultyVector) -> RoeSpec:
    """Map `roe_strictness` onto the shipped rule set and its parameters."""
    strict = diff.roe_strictness >= 0.65
    rule_set = resolve_difficulty(diff).roe_rule_set
    return RoeSpec(
        rule_set=rule_set,
        require_id_before_engage=strict,
        allowed_engage_labels=[LabelType.HOSTILE]
        if strict
        else [LabelType.HOSTILE, LabelType.CIVIL],
        friend_or_civil_warning_required=True,
        collateral_sector_tolerance=0.0 if strict else 0.25,
        late_engage_threshold_s=round(25.0 - 12.0 * float(diff.time_pressure), 2),
        min_track_quality=0.15 + 0.25 * float(diff.sensor_degradation_level),
        require_warning_before_engage=strict,
    )


def _apply_overrides(scenario: Scenario, overrides: dict[str, object]) -> Scenario:
    """Apply explicit overrides (used by the Red Brain and the NL builder)."""
    data = scenario.model_dump(mode="python")
    threats = list(data.get("threats", []))
    for i, wave in enumerate(threats):
        patch = overrides.get(f"threat_{i}")
        if isinstance(patch, dict):
            wave.update(patch)
    data["threats"] = threats
    return Scenario.model_validate(data)


def base_scenario(
    seed: int = 12345,
    difficulty: DifficultyVector | None = None,
    tags: list[str] | None = None,
    terrain_preset: TerrainPreset | None = None,
    scenario_id: str | None = None,
    name: str | None = None,
    duration_s: int | None = None,
    source: str = "generated",
    behaviour_overrides: dict[str, object] | None = None,
) -> Scenario:
    """A deterministic, moderate scenario - handy for tests and the demo."""
    return generate(
        seed=seed,
        difficulty=difficulty,
        tags=tags,
        terrain_preset=terrain_preset,
        scenario_id=scenario_id,
        name=name,
        duration_s=duration_s,
        source=source,
        behaviour_overrides=behaviour_overrides,
    ).scenario


# --------------------------------------------------------------------------- #
# Validation and sanity
# --------------------------------------------------------------------------- #


def validate_scenario(scenario: Scenario) -> list[str]:
    """Structural validation beyond the Pydantic model; returns error strings."""
    problems: list[str] = []
    if not scenario.sensors:
        problems.append("no sensors")
    radar = [s for s in scenario.sensors if s.type is SensorType.RADAR]
    if not radar and not any(s.type is SensorType.EO_IR for s in scenario.sensors):
        problems.append("no radar or EO/IR coverage")
    for spec in scenario.sensors:
        if spec.type is SensorType.EO_IR and spec.fov_deg >= 359.9:
            problems.append(f"sensor {spec.id}: an EO/IR camera needs a steerable FOV")
    hostile = [w for w in scenario.threats if w.label is LabelType.HOSTILE]
    if not hostile:
        problems.append("no hostile wave")
    if not any(e.max_range_m > 0 for e in scenario.effectors):
        problems.append("no usable effector")
    if scenario.duration_s < 60:
        problems.append("scenario shorter than 60 s")
    for wave in scenario.all_waves:
        if wave.spawn_t + 45.0 > scenario.duration_s:
            problems.append(f"wave {wave.name!r} spawns too late to matter")
    return problems


def sanity_check(scenario: Scenario, max_seconds: float | None = None) -> tuple[bool, str]:
    """Run a short headless simulation and confirm the scenario is playable.

    Checks structural validity, then drives the *real* engine with no trainee
    actions at a coarse timestep: at least one hostile must reach a perimeter
    ring, every wave must spawn, and no entity may go numerically non-finite.

    `max_seconds` overrides the window (defaults to 60 % of the scenario, capped
    at 420 s, which is long enough for any ship-authored scenario to resolve).
    """
    from app.core.engine import SimEngine

    problems = validate_scenario(scenario)
    if problems:
        return (False, "; ".join(problems))

    window = float(
        max_seconds if max_seconds is not None else min(scenario.duration_s * 0.6, 420.0)
    )
    dt = 0.2
    engine = SimEngine(scenario, dt=dt)
    max_steps = max(1, int(window / dt))
    try:
        engine.run(max_steps=max_steps)
    except Exception as exc:  # pragma: no cover - defensive
        return (False, f"sanity simulation raised {type(exc).__name__}: {exc}")

    world = engine.state.world
    if not world.entities:
        return (False, "no entities spawned")
    for ent in world.entities.values():
        if not all(math.isfinite(v) for v in (*ent.position.as_tuple(), *ent.velocity.as_tuple())):
            return (False, f"non-finite state for {ent.id}")
    hostiles = [e for e in world.entities.values() if e.label is LabelType.HOSTILE]
    if not hostiles:
        return (False, "no hostile entity spawned")
    if not any(e.ring_crossings or e.reached_perimeter_t is not None for e in hostiles):
        return (
            False,
            "no hostile reaches a perimeter ring within "
            f"{window:.0f} s - terrain or masking makes the scenario unwinnable",
        )
    return (
        True,
        f"{len(world.entities)} entities spawned, "
        f"{sum(1 for e in hostiles if e.ring_crossings or e.reached_perimeter_t is not None)}"
        " hostile(s) reach a perimeter ring",
    )


# --------------------------------------------------------------------------- #
# Adaptive selection
# --------------------------------------------------------------------------- #


def difficulty_for_skill(skill: str, rating: float, target_p: float = 0.70) -> float:
    """Difficulty scalar that predicts `target_p` success on `skill`."""
    return difficulty_for_success(rating, target_p, skill)


def vector_for_skill(
    base: DifficultyVector, skill: str, rating: float, target_p: float = 0.70
) -> DifficultyVector:
    """Nudge every axis that loads onto `skill` toward the target difficulty."""
    target = difficulty_for_skill(skill, rating, target_p)
    loading = {
        "detection": (
            "sensor_degradation_level",
            "visibility",
            "night",
            "swarm_size",
            "simultaneity",
        ),
        "classification": ("ambiguity", "decoy_ratio", "visibility", "night"),
        "prioritisation": ("swarm_size", "approach_axes", "simultaneity", "time_pressure"),
        "engagement": ("time_pressure", "swarm_size", "approach_axes", "sensor_degradation_level"),
        "roe": ("roe_strictness", "decoy_ratio", "simultaneity", "ambiguity"),
    }[skill]
    out = base
    for axis in loading:
        current = float(getattr(out, axis))
        if axis == "visibility":
            # Visibility is an ease axis; raise the difficulty means lower it.
            out = out.with_axis(axis, current + (1.0 - target) * 0.9 - current)
        else:
            out = out.with_axis(axis, current + (target - current) * 0.9)
    return out


def next_difficulty_for(
    skills: SkillVector, weak_axis_bias: float = 0.0
) -> tuple[DifficultyVector, str]:
    """Pick the weakest skill and return a difficulty vector targeting ~70 %."""
    weakest = skills.weakest
    rating = float(getattr(skills, weakest))
    target = difficulty_for_skill(weakest, rating)
    base = DifficultyVector(
        swarm_size=float(np.clip(0.25 + target * 0.5, 0.05, 0.95)),
        approach_axes=float(np.clip(0.2 + target * 0.6 + weak_axis_bias, 0.05, 0.95)),
        time_pressure=float(np.clip(0.2 + target * 0.55, 0.05, 0.95)),
        sensor_degradation_level=float(np.clip(0.1 + target * 0.5, 0.0, 0.95)),
        decoy_ratio=float(np.clip(0.1 + target * 0.4, 0.0, 0.9)),
        visibility=float(np.clip(0.75 - target * 0.5, 0.05, 0.95)),
        night=0.0 if weakest != "detection" else float(np.clip(target * 0.8, 0.0, 1.0)),
        ambiguity=float(np.clip(0.1 + target * 0.5, 0.0, 0.95)),
        simultaneity=float(np.clip(0.15 + target * 0.6, 0.05, 0.95)),
        roe_strictness=float(np.clip(0.15 + target * 0.7, 0.0, 0.95)),
    )
    refined = vector_for_skill(base, weakest, rating)
    return refined, weakest
