"""Entity classes, generic synthetic parameters and movement behaviours.

SAFETY: every magnitude in :data:`CLASS_TABLE` is invented for simulation.
No value here is measured from, or intended to describe, any real airframe,
sensor or effector. See ``README.md`` § Safety boundary.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Final

import numpy as np

from app.core.schemas import (
    BehaviourKind,
    BehaviourSpec,
    EntityClass,
    FormationKind,
    LabelType,
    ThreatSpec,
    Vec3,
)

__all__ = [
    "CLASS_TABLE",
    "BehaviourContext",
    "EntityParams",
    "apply_behaviour",
    "formation_offsets",
    "params_for",
    "resolve_label",
    "resolve_rf_active",
]


@dataclass(frozen=True, slots=True)
class EntityParams:
    """Generic, abstract parameters for one entity class.

    ``rcs_m2`` and ``acoustic_level`` are *simulation* parameters: they set the
    scale of the sensor models so the game is playable. They are not radar
    cross-sections or sound-pressure levels of anything real.
    """

    max_speed_mps: float
    turn_rate_rad_s: float
    accel_mps2: float
    rcs_m2: float
    """Abstract radar-scatter parameter (dimensionless-ish, invented)."""
    acoustic_level: float
    """Abstract acoustic detectability parameter in [0, 1]."""
    rf_active_default: bool
    visual_size_m: float
    """Angular size proxy in metres at 1 m."""
    thermal_signature: float
    """Abstract IR contrast in [0, 1]."""
    hover_capable: bool
    loiter_speed_mps: float
    cruise_altitude_m: float
    susceptibility: float
    """How well a soft-kill effector works against this class in [0, 1]."""

    def max_speed(self, multiplier: float = 1.0) -> float:
        """Class max speed scaled by a per-instance behaviour multiplier."""
        return self.max_speed_mps * max(0.05, float(multiplier))


CLASS_TABLE: Final[dict[EntityClass, EntityParams]] = {
    # --- slow, small, hover-capable multipurpose -------------------------- #
    EntityClass.QUAD_SMALL: EntityParams(
        max_speed_mps=12.0,
        turn_rate_rad_s=1.6,
        accel_mps2=3.0,
        rcs_m2=0.015,
        acoustic_level=0.45,
        rf_active_default=True,
        visual_size_m=0.9,
        thermal_signature=0.35,
        hover_capable=True,
        loiter_speed_mps=3.0,
        cruise_altitude_m=90.0,
        susceptibility=0.72,
    ),
    # --- fast, low, terrain-hugging --------------------------------------- #
    EntityClass.FPV_FAST: EntityParams(
        max_speed_mps=24.0,
        turn_rate_rad_s=2.6,
        accel_mps2=8.0,
        rcs_m2=0.008,
        acoustic_level=0.30,
        rf_active_default=True,
        visual_size_m=0.45,
        thermal_signature=0.22,
        hover_capable=False,
        loiter_speed_mps=6.0,
        cruise_altitude_m=45.0,
        susceptibility=0.55,
    ),
    # --- faster, higher, loiter patterns ---------------------------------- #
    EntityClass.FIXED_WING: EntityParams(
        max_speed_mps=32.0,
        turn_rate_rad_s=0.7,
        accel_mps2=4.0,
        rcs_m2=0.05,
        acoustic_level=0.55,
        rf_active_default=True,
        visual_size_m=1.6,
        thermal_signature=0.30,
        hover_capable=False,
        loiter_speed_mps=26.0,
        cruise_altitude_m=320.0,
        susceptibility=0.80,
    ),
    # --- decoys ----------------------------------------------------------- #
    EntityClass.BIRD_FLOCK: EntityParams(
        max_speed_mps=16.0,
        turn_rate_rad_s=3.4,
        accel_mps2=6.0,
        rcs_m2=0.004,
        acoustic_level=0.22,
        rf_active_default=False,
        visual_size_m=0.25,
        thermal_signature=0.12,
        hover_capable=False,
        loiter_speed_mps=9.0,
        cruise_altitude_m=70.0,
        susceptibility=0.05,
    ),
    EntityClass.KITE_BALLOON: EntityParams(
        max_speed_mps=6.0,
        turn_rate_rad_s=0.5,
        accel_mps2=0.6,
        rcs_m2=0.06,
        acoustic_level=0.15,
        rf_active_default=False,
        visual_size_m=2.2,
        thermal_signature=0.25,
        hover_capable=True,
        loiter_speed_mps=1.5,
        cruise_altitude_m=140.0,
        susceptibility=0.03,
    ),
    EntityClass.FRIENDLY_UAV: EntityParams(
        max_speed_mps=10.0,
        turn_rate_rad_s=1.4,
        accel_mps2=2.5,
        rcs_m2=0.02,
        acoustic_level=0.40,
        rf_active_default=True,
        visual_size_m=1.1,
        thermal_signature=0.40,
        hover_capable=True,
        loiter_speed_mps=4.0,
        cruise_altitude_m=70.0,
        susceptibility=0.90,
    ),
    EntityClass.CIVIL_AIRCRAFT: EntityParams(
        max_speed_mps=48.0,
        turn_rate_rad_s=0.12,
        accel_mps2=1.5,
        rcs_m2=0.30,
        acoustic_level=0.80,
        rf_active_default=True,
        visual_size_m=3.0,
        thermal_signature=0.55,
        hover_capable=False,
        loiter_speed_mps=44.0,
        cruise_altitude_m=900.0,
        susceptibility=0.95,
    ),
}


def params_for(entity_class: EntityClass) -> EntityParams:
    """Return the parameter row for `entity_class`."""
    return CLASS_TABLE[entity_class]


def resolve_rf_active(spec: ThreatSpec) -> bool:
    """Whether an RF emitter is on for this wave (spec override beats class default)."""
    if spec.rf_active is not None:
        return bool(spec.rf_active)
    return CLASS_TABLE[spec.entity_class].rf_active_default


def resolve_label(spec: ThreatSpec) -> LabelType:
    """Ground-truth label for a wave (decoy waves may override in the scenario)."""
    return spec.label


# --------------------------------------------------------------------------- #
# Formations
# --------------------------------------------------------------------------- #


def formation_offsets(kind: FormationKind, count: int, spacing_m: float) -> list[Vec3]:
    """Plan-view offsets (metres, in the wave's local frame) for a formation.

    The frame is: +x points along the approach direction, +y is lateral.
    """
    n = max(1, int(count))
    out: list[Vec3] = []
    if n == 1:
        return [Vec3(x=0.0, y=0.0, z=0.0)]
    if kind is FormationKind.LINE:
        for i in range(n):
            out.append(Vec3(x=-i * spacing_m, y=0.0, z=0.0))
    elif kind is FormationKind.WEDGE:
        row, col = 0, 0
        for i in range(n):
            out.append(Vec3(x=-row * spacing_m, y=col * spacing_m * 0.9, z=0.0))
            col += 1
            if col > row:
                col, row = 0, row + 1
    elif kind is FormationKind.SCATTER:
        # Deterministic golden-angle scatter; no RNG needed so replays match.
        golden = math.pi * (3.0 - math.sqrt(5.0))
        for i in range(n):
            r = spacing_m * math.sqrt(i + 0.5)
            a = i * golden
            out.append(Vec3(x=r * math.cos(a), y=r * math.sin(a), z=0.0))
    elif kind is FormationKind.PINCER:
        half = (n + 1) // 2
        for i in range(n):
            side = -1.0 if i < half else 1.0
            lane = i if i < half else i - half
            out.append(Vec3(x=-lane * spacing_m, y=side * spacing_m * 2.4, z=0.0))
    else:  # LEADER_FOLLOWER
        half = max(1, n // 2)
        for i in range(n):
            out.append(
                Vec3(
                    x=-i * spacing_m,
                    y=(i % max(1, half) - (max(1, half) - 1) / 2.0) * spacing_m * 0.7,
                    z=0.0,
                )
            )
    return out


# --------------------------------------------------------------------------- #
# Behaviour integration
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class BehaviourContext:
    """Everything a behaviour needs that is not the entity's own state."""

    site_x: float = 0.0
    site_y: float = 0.0
    t: float = 0.0
    ground_height: float = 0.0
    members: list[Vec3] = field(default_factory=list)
    """Positions of the rest of the wave, for boids terms."""
    leader_vel: Vec3 = field(default_factory=lambda: Vec3(x=0.0, y=0.0, z=0.0))


def apply_behaviour(
    position: Vec3,
    velocity: Vec3,
    params: EntityParams,
    spec: BehaviourSpec,
    ctx: BehaviourContext,
    rng: np.random.Generator,
    dt: float,
) -> tuple[Vec3, Vec3, bool]:
    """Advance one entity by `dt` and return ``(new_position, new_velocity, active)``.

    `active` is False once the entity has completed its route and is simply
    loitering at the end state.
    """
    desired = _desired_velocity(position, velocity, params, spec, ctx, rng)
    vx = _approach(velocity.x, desired.x, params.accel_mps2 * dt)
    vy = _approach(velocity.y, desired.y, params.accel_mps2 * dt)
    vz = _approach(velocity.z, desired.z, params.accel_mps2 * dt)
    speed = math.sqrt(vx * vx + vy * vy + vz * vz)
    limit = params.max_speed(spec.speed_mps / params.max_speed_mps if spec.speed_mps else 1.0)
    if speed > limit and speed > 1e-9:
        k = limit / speed
        vx, vy, vz = vx * k, vy * k, vz * k
    nx = position.x + vx * dt
    ny = position.y + vy * dt
    nz = max(0.0, position.z + vz * dt)
    return Vec3(x=nx, y=ny, z=nz), Vec3(x=vx, y=vy, z=vz), True


def _desired_velocity(
    position: Vec3,
    velocity: Vec3,
    params: EntityParams,
    spec: BehaviourSpec,
    ctx: BehaviourContext,
    rng: np.random.Generator,
) -> Vec3:
    kind = spec.kind
    if kind is BehaviourKind.LOITER:
        return _loiter_velocity(position, params, spec, ctx, rng)
    if kind is BehaviourKind.TERMINAL_DIVE:
        return _terminal_velocity(position, params, spec, ctx)
    if kind is BehaviourKind.ORBIT_THEN_STRIKE:
        return _orbit_strike_velocity(position, params, spec, ctx)
    if kind is BehaviourKind.DECOY_LURE:
        return _lure_velocity(position, params, spec, ctx, rng)
    if kind is BehaviourKind.SWARM:
        return _swarm_velocity(position, velocity, params, spec, ctx, rng)
    if kind is BehaviourKind.TRANSIT:
        return _transit_velocity(position, params, spec, ctx)
    return _waypoint_velocity(position, params, spec, ctx)


def _approach(current: float, target: float, max_delta: float) -> float:
    if max_delta <= 0.0:
        return target
    if target > current:
        return min(target, current + max_delta)
    return max(target, current - max_delta)


def _unit_towards(p: Vec3, q: Vec3, fallback: Vec3) -> Vec3:
    dx = q.x - p.x
    dy = q.y - p.y
    dz = q.z - p.z
    d = math.sqrt(dx * dx + dy * dy + dz * dz)
    if d < 1e-6:
        return fallback
    return Vec3(x=dx / d, y=dy / d, z=dz / d)


def _waypoint_velocity(
    position: Vec3, params: EntityParams, spec: BehaviourSpec, ctx: BehaviourContext
) -> Vec3:
    speed = params.loiter_speed_mps if params.hover_capable else params.max_speed_mps * 0.7
    if spec.speed_mps:
        speed = float(spec.speed_mps)
    if not spec.waypoints:
        return _transit_velocity(position, params, spec, ctx)
    # Choose the first waypoint we are not already within tolerance of.
    target = spec.waypoints[-1]
    for wp in spec.waypoints:
        d = math.dist((position.x, position.y, position.z), (wp.x, wp.y, wp.z))
        if d > 25.0:
            target = wp
            break
    u = _unit_towards(position, target, Vec3(x=0.0, y=0.0, z=0.0))
    return Vec3(x=u.x * speed, y=u.y * speed, z=u.z * speed)


def _loiter_velocity(
    position: Vec3,
    params: EntityParams,
    spec: BehaviourSpec,
    ctx: BehaviourContext,
    rng: np.random.Generator,
) -> Vec3:
    """Circle a holding point near the site at loiter speed."""
    speed = params.loiter_speed_mps
    if spec.speed_mps:
        speed = float(spec.speed_mps)
    centre = Vec3(
        x=ctx.site_x + spec.orbit_radius_m * math.cos(ctx.t / 40.0),
        y=ctx.site_y + spec.orbit_radius_m * math.sin(ctx.t / 40.0),
        z=params.cruise_altitude_m,
    )
    radius = math.hypot(position.x - centre.x, position.y - centre.y)
    tangent = Vec3(x=-(position.y - centre.y), y=position.x - centre.x, z=0.0)
    mag = max(1e-6, math.hypot(tangent.x, tangent.y))
    drift = rng.normal(0.0, 0.08, size=2)
    return Vec3(
        x=(tangent.x / mag) * speed + float(drift[0]),
        y=(tangent.y / mag) * speed + float(drift[1]),
        z=(centre.z - position.z) * 0.2,
    )


def _transit_velocity(
    position: Vec3, params: EntityParams, spec: BehaviourSpec, ctx: BehaviourContext
) -> Vec3:
    """Fly toward the site at cruise speed, holding cruise (or ground-clearant) altitude."""
    speed = params.max_speed_mps * 0.55
    if spec.speed_mps:
        speed = float(spec.speed_mps)
    altitude = (
        ctx.ground_height + spec.min_clearance_m
        if spec.terrain_following
        else params.cruise_altitude_m
    )
    target = Vec3(x=ctx.site_x, y=ctx.site_y, z=altitude)
    u = _unit_towards(position, target, Vec3(x=0.0, y=0.0, z=0.0))
    dz = target.z - position.z
    return Vec3(x=u.x * speed, y=u.y * speed, z=max(-6.0, min(6.0, dz)))


def _terminal_velocity(
    position: Vec3, params: EntityParams, spec: BehaviourSpec, ctx: BehaviourContext
) -> Vec3:
    """Descend while closing on the site."""
    speed = params.max_speed_mps * 0.6
    if spec.speed_mps:
        speed = float(spec.speed_mps)
    ground_slant = math.hypot(position.x - ctx.site_x, position.y - ctx.site_y)
    target = Vec3(x=ctx.site_x, y=ctx.site_y, z=spec.terminal_altitude_m)
    u = _unit_towards(position, target, Vec3(x=0.0, y=0.0, z=-1.0))
    dz = max(-8.0, min(2.0, (target.z - position.z) * 0.4))
    if ground_slant < 60.0:
        dz = max(-2.0, dz)
    return Vec3(x=u.x * speed, y=u.y * speed, z=dz)


def _orbit_strike_velocity(
    position: Vec3, params: EntityParams, spec: BehaviourSpec, ctx: BehaviourContext
) -> Vec3:
    """Orbit at `orbit_radius_m` for `orbit_period_s`, then commit to the site."""
    elapsed = ctx.t
    u = _unit_towards(
        position, Vec3(x=ctx.site_x, y=ctx.site_y, z=position.z), Vec3(x=1.0, y=0.0, z=0.0)
    )
    orbit_angle = 2.0 * math.pi * (elapsed / max(1e-6, spec.orbit_period_s))
    tangent = Vec3(x=-u.y, y=u.x, z=0.0)
    radius = math.hypot(position.x - ctx.site_x, position.y - ctx.site_y)
    inward = -1.0 if radius > spec.orbit_radius_m else 1.0
    if elapsed >= spec.orbit_period_s:
        target = Vec3(x=ctx.site_x, y=ctx.site_y, z=spec.terminal_altitude_m)
        d = _unit_towards(position, target, Vec3(x=0.0, y=0.0, z=-1.0))
        speed = params.max_speed_mps * 0.65
        return Vec3(x=d.x * speed, y=d.y * speed, z=max(-8.0, min(2.0, d.z * speed)))
    speed = params.loiter_speed_mps if params.hover_capable else params.max_speed_mps * 0.4
    if spec.speed_mps:
        speed = float(spec.speed_mps)
    blend = 0.65 * math.cos(orbit_angle) + 0.35 * math.sin(orbit_angle)
    radial = (radius - spec.orbit_radius_m) / max(1.0, spec.orbit_radius_m)
    vx = tangent.x * speed * blend + u.x * speed * radial * inward * 1.2
    vy = tangent.y * speed * blend + u.y * speed * radial * inward * 1.2
    return Vec3(x=vx, y=vy, z=(params.cruise_altitude_m - position.z) * 0.2)


def _lure_velocity(
    position: Vec3,
    params: EntityParams,
    spec: BehaviourSpec,
    ctx: BehaviourContext,
    rng: np.random.Generator,
) -> Vec3:
    """Move deliberately slowly and erratically to bait a premature engagement."""
    speed = max(1.0, params.loiter_speed_mps * 0.7)
    jitter = rng.normal(0.0, 0.7, size=3)
    u = _unit_towards(
        position, Vec3(x=ctx.site_x, y=ctx.site_y, z=params.cruise_altitude_m), jitter
    )
    return Vec3(
        x=(u.x + float(jitter[0]) * 0.5) * speed,
        y=(u.y + float(jitter[1]) * 0.5) * speed,
        z=u.z * speed * 0.5,
    )


def _swarm_velocity(
    position: Vec3,
    velocity: Vec3,
    params: EntityParams,
    spec: BehaviourSpec,
    ctx: BehaviourContext,
    rng: np.random.Generator,
) -> Vec3:
    """Boids-style separation / cohesion / alignment plus a goal term."""
    separation = Vec3(x=0.0, y=0.0, z=0.0)
    cohesion = Vec3(x=0.0, y=0.0, z=0.0)
    alignment = Vec3(x=0.0, y=0.0, z=0.0)
    neighbours = 0
    sep_radius = spec.separation_m * 2.5
    for other in ctx.members:
        d = math.dist(
            (position.x, position.y, position.z), (other.x, other.y, other.z)
        )
        if d < 1e-3:
            continue
        neighbours += 1
        cohesion = Vec3(
            x=cohesion.x + other.x, y=cohesion.y + other.y, z=cohesion.z + other.z
        )
        alignment = Vec3(
            x=alignment.x + ctx.leader_vel.x,
            y=alignment.y + ctx.leader_vel.y,
            z=alignment.z + ctx.leader_vel.z,
        )
        if d < sep_radius:
            push = (sep_radius - d) / sep_radius
            separation = Vec3(
                x=separation.x - (other.x - position.x) / d * push,
                y=separation.y - (other.y - position.y) / d * push,
                z=separation.z - (other.z - position.z) / d * push,
            )
    speed = params.max_speed_mps * 0.55
    if spec.speed_mps:
        speed = float(spec.speed_mps)

    goal = _transit_velocity(position, params, spec, ctx)
    if not ctx.members:
        return goal

    cohesion = Vec3(
        x=cohesion.x / neighbours - position.x,
        y=cohesion.y / neighbours - position.y,
        z=cohesion.z / neighbours - position.z,
    )
    cg = math.sqrt(cohesion.x**2 + cohesion.y**2 + cohesion.z**2) or 1.0
    cohesion = Vec3(x=cohesion.x / cg, y=cohesion.y / cg, z=cohesion.z / cg)

    ag = math.sqrt(alignment.x**2 + alignment.y**2 + alignment.z**2) or 1.0
    alignment = Vec3(x=alignment.x / ag, y=alignment.y / ag, z=alignment.z / ag)

    vx = (
        goal.x
        + spec.cohesion_weight * cohesion.x * speed
        + spec.alignment_weight * alignment.x * speed
        + separation.x * speed * 1.4
    )
    vy = (
        goal.y
        + spec.cohesion_weight * cohesion.y * speed
        + spec.alignment_weight * alignment.y * speed
        + separation.y * speed * 1.4
    )
    vz = goal.z + separation.z * speed
    return Vec3(x=vx, y=vy, z=vz)


def label_of(spec: ThreatSpec, index: int) -> LabelType:
    """Ground-truth label for member `index` of `spec` (decoys may vary per member)."""
    return spec.label