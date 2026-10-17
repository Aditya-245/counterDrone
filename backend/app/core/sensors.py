"""The four sensor models behind one interface.

    class Sensor:
        def observe(self, world: WorldState, rng: RngLike) -> list[Detection]

Every sensor is abstract and generic. The parameter tables below are invented
for simulation: ``rcs_m2`` is a scatter parameter used to scale detection
probability, not a radar cross-section of anything real, and no frequency, band
or emission detail of any actual sensor is modelled. See ``README.md``.

Degradation
-----------
Each sensor reads its operational state from ``world.sensors[id].state``:
``FAILED`` produces nothing, ``PARTIAL`` halves the usable range, drops the
detection probability and inflates the measurement noise. GPS denial adds a
slowly growing position offset, which is stored on the runtime so replays
reproduce it exactly.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Final

import numpy as np

from app.core.entities import CLASS_TABLE, params_for
from app.core.rng import RngLike
from app.core.schemas import (
    DegradationState,
    EntityClass,
    EnvSpec,
    LabelType,
    Scenario,
    SensorSpec,
    SensorType,
    TimeOfDay,
    Vec3,
)
from app.core.terrain import Terrain
from app.core.tracking import Detection
from app.core.world import EntityState, SensorRuntime, WorldState

__all__ = [
    "AcousticSensor",
    "EoIrSensor",
    "FIRST_DETECTABLE_PD",
    "RfSensor",
    "RadarSensor",
    "Sensor",
    "SensorContext",
    "build_sensors",
    "first_detectable",
    "sensor_from_spec",
]


@dataclass(frozen=True, slots=True)
class SensorContext:
    """Static world information every sensor needs."""

    terrain: Terrain
    environment: EnvSpec
    scenario: Scenario

    @property
    def site_x(self) -> float:
        """Protected-zone centre X (always the origin in the shipped world)."""
        return 0.0

    @property
    def site_y(self) -> float:
        """Protected-zone centre Y."""
        return 0.0

    @property
    def protected_radius_m(self) -> float:
        """Protected-zone radius."""
        return float(self.scenario.site.protected_radius_m)

    @property
    def rings(self) -> tuple[float, ...]:
        """Perimeter ring distances."""
        return tuple(float(r) for r in self.scenario.site.ring_distances_m)

    @property
    def is_urban(self) -> bool:
        """True for the urban terrain preset (more clutter and occlusion)."""
        return self.scenario.terrain.preset.value == "urban"


# --------------------------------------------------------------------------- #
# Class-evidence priors
# --------------------------------------------------------------------------- #

#: Coarse size bands keyed by the abstract scatter parameter. Weights are
#: deliberately ambiguous: a radar return cannot by itself identify a class.
_RCS_BANDS: Final[tuple[tuple[float, dict[EntityClass, float]], ...]] = (
    (
        0.006,
        {EntityClass.FPV_FAST: 0.38, EntityClass.QUAD_SMALL: 0.34, EntityClass.BIRD_FLOCK: 0.28},
    ),
    (
        0.022,
        {
            EntityClass.QUAD_SMALL: 0.40,
            EntityClass.FRIENDLY_UAV: 0.26,
            EntityClass.FPV_FAST: 0.20,
            EntityClass.KITE_BALLOON: 0.14,
        },
    ),
    (
        0.09,
        {
            EntityClass.FIXED_WING: 0.52,
            EntityClass.KITE_BALLOON: 0.32,
            EntityClass.QUAD_SMALL: 0.16,
        },
    ),
    (math.inf, {EntityClass.CIVIL_AIRCRAFT: 0.88, EntityClass.FIXED_WING: 0.12}),
)

#: Detection probability at which a target counts as "first detectable".
#: This is deliberately low: it marks the first moment a track could
#: *legitimately* appear, not the moment it certainly will. Detection latency
#: then measures how long the operator took to notice, not sensor luck.
FIRST_DETECTABLE_PD: Final[float] = 0.05

#: Label prior implied by an entity class.
_LABEL_PRIOR: Final[dict[EntityClass, LabelType]] = {
    EntityClass.QUAD_SMALL: LabelType.HOSTILE,
    EntityClass.FPV_FAST: LabelType.HOSTILE,
    EntityClass.FIXED_WING: LabelType.HOSTILE,
    EntityClass.BIRD_FLOCK: LabelType.BIRD,
    EntityClass.KITE_BALLOON: LabelType.CIVIL,
    EntityClass.FRIENDLY_UAV: LabelType.FRIENDLY,
    EntityClass.CIVIL_AIRCRAFT: LabelType.CIVIL,
}


def _nearest_class(candidate: EntityClass) -> EntityClass:
    """Closest plausible class a confused classifier might report."""
    order = [
        EntityClass.QUAD_SMALL,
        EntityClass.FPV_FAST,
        EntityClass.FIXED_WING,
        EntityClass.FRIENDLY_UAV,
        EntityClass.KITE_BALLOON,
        EntityClass.BIRD_FLOCK,
        EntityClass.CIVIL_AIRCRAFT,
    ]
    i = order.index(candidate)
    return order[(i + 1) % len(order)]


def rcs_band_evidence(rcs: float) -> dict[EntityClass, float]:
    """Coarse class evidence implied by an abstract scatter parameter."""
    for upper, ev in _RCS_BANDS:
        if rcs <= upper:
            return dict(ev)
    return dict(_RCS_BANDS[-1][1])


# --------------------------------------------------------------------------- #
# Base class
# --------------------------------------------------------------------------- #


class Sensor(ABC):
    """Common behaviour for every sensor: degradation, LOS and noise plumbing."""

    kind: SensorType

    def __init__(self, spec: SensorSpec, ctx: SensorContext) -> None:
        self.spec = spec
        self.ctx = ctx

    # -- interface ----------------------------------------------------- #
    @abstractmethod
    def observe(self, world: WorldState, rng: RngLike) -> list[Detection]:
        """Return this sensor's detections for the current instant."""

    @abstractmethod
    def detection_probability(self, entity: EntityState, world: WorldState) -> float:
        """Noise-free probability of detecting `entity` right now, in [0, 1]."""

    # -- shared helpers ------------------------------------------------ #
    @property
    def runtime(self) -> SensorRuntime | None:  # pragma: no cover - overridden usage
        """Runtime is looked up from the world; see `runtime_of`."""
        return None

    def runtime_of(self, world: WorldState) -> SensorRuntime | None:
        """Return the live runtime for this sensor, if it is in the world."""
        return world.sensors.get(self.spec.id)

    def effective_state(self, world: WorldState) -> DegradationState:
        """Operational state after applying the scenario's timed degradations."""
        run = self.runtime_of(world)
        return run.state if run else DegradationState.FAILED

    def effective_range(self, world: WorldState) -> tuple[float, float]:
        """(min_range, max_range) after degradation."""
        run = self.runtime_of(world)
        state = run.state if run else DegradationState.FAILED
        rmin = float(self.spec.min_range_m)
        rmax = float(self.spec.max_range_m)
        if state is DegradationState.PARTIAL:
            rmax *= 0.55
        return rmin, rmax

    def effective_position(self, world: WorldState) -> tuple[float, float, float]:
        """Sensor position including the GPS-denial drift offset."""
        run = self.runtime_of(world)
        dx = run.drift_x_m if run else 0.0
        dy = run.drift_y_m if run else 0.0
        return (self.spec.x + dx, self.spec.y + dy, self.spec.z)

    def in_fov(self, world: WorldState, target: Vec3) -> bool:
        """Whether `target` lies inside the sensor's current azimuthal FOV."""
        if self.spec.fov_deg >= 359.9:
            return True
        sx, sy, _ = self.effective_position(world)
        bearing = math.degrees(math.atan2(target.y - sy, target.x - sx)) % 360.0
        run = self.runtime_of(world)
        centre = run.azimuth_deg if run else 0.0
        half = self.spec.fov_deg / 2.0
        delta = ((bearing - centre + 180.0) % 360.0) - 180.0
        return abs(delta) <= half

    def visibility_factor(self, exponent: float = 1.0) -> float:
        """Atmospheric attenuation factor in (0, 1].

        `exponent` encodes how strongly a given sensor class suffers from fog
        and rain: an optical sensor takes the full penalty (``exponent = 1``),
        radar is largely unaffected (``exponent ~ 0.35``), and acoustics ignore
        visibility entirely (``exponent = 0``).
        """
        if exponent <= 0.0:
            return 1.0
        vis = float(self.ctx.environment.visibility_m)
        base = min(1.0, max(0.05, vis / 7000.0))
        rain = 1.0 - 0.40 * float(self.ctx.environment.rain)
        return float(np.clip((base * rain) ** exponent, 0.03, 1.0))

    def los_factor(self, world: WorldState, entity: EntityState) -> float:
        """Fraction of the sensor->target ray that is not masked by terrain."""
        sx, sy, sz = self.effective_position(world)
        origin = Vec3(x=sx, y=sy, z=sz + 2.0)
        frac = self.ctx.terrain.occlusion_fraction(origin, entity.position, samples=32)
        if frac <= 0.0:
            return 1.0
        return float(max(0.0, 1.0 - 0.95 * frac))

    def clutter_factor(self) -> float:
        """Clutter multiplier: urban terrain and rain both make life harder."""
        urban = 1.35 if self.ctx.is_urban else 1.0
        rain = 1.0 + 1.1 * float(self.ctx.environment.rain)
        return float(urban * rain)

    def in_range(self, entity: EntityState, world: WorldState) -> tuple[bool, float]:
        """Range gate check; returns (in_range, slant_range_m)."""
        sx, sy, sz = self.effective_position(world)
        d = math.dist(
            (entity.position.x, entity.position.y, entity.position.z),
            (sx, sy, sz),
        )
        rmin, rmax = self.effective_range(world)
        return (rmin <= d <= rmax, d)


# --------------------------------------------------------------------------- #
# Radar
# --------------------------------------------------------------------------- #


class RadarSensor(Sensor):
    """Abstract ground/ship-style radar: range/RCS/clutter driven, with false alarms.

    Radar is largely insensitive to fog and rain compared with an optical
    sensor, which is modelled with a reduced ``visibility_exponent``.
    """

    kind = SensorType.RADAR

    #: Fog/rain attenuation exponent for a radar return.
    visibility_exponent: float = 0.35
    #: Abstract clutter density: expected false plots per scan at nominal settings.
    base_clutter_rate: float = 0.035
    #: Abstract radial speed below which a hovering target is much harder.
    min_speed_mps: float = 1.5

    def _pd(self, entity: EntityState, world: WorldState, slant_range: float) -> float:
        params = params_for(entity.entity_class)
        rmax = self.effective_range(world)[1]
        if slant_range <= 1e-3:
            return 0.0
        rcs_term = min(1.0, (params.rcs_m2 / 0.008) ** 0.30)
        range_term = max(0.0, 1.0 - (slant_range / rmax) ** 4.0)
        speed = entity.speed_mps
        # Minimum-speed filtering: slow hoverers are much harder.
        speed_term = (
            1.0
            if speed >= self.min_speed_mps
            else 0.45 + 0.55 * (speed / max(1e-6, self.min_speed_mps))
        )
        base = 0.95 * rcs_term * range_term * speed_term
        base *= self.los_factor(world, entity)
        base *= self.visibility_factor(self.visibility_exponent)
        state = self.effective_state(world)
        if state is DegradationState.PARTIAL:
            base *= 0.55
        return float(np.clip(base, 0.0, 0.97))

    def detection_probability(self, entity: EntityState, world: WorldState) -> float:
        """Noise-free radar detection probability."""
        if not entity.active or entity.neutralised:
            return 0.0
        if self.effective_state(world) is DegradationState.FAILED:
            return 0.0
        if not self.in_fov(world, entity.position):
            return 0.0
        ok, slant = self.in_range(entity, world)
        if not ok:
            return 0.0
        if self.ctx.terrain.height_at(entity.position.x, entity.position.y) > entity.position.z:
            return 0.0
        return self._pd(entity, world, slant)

    def observe(self, world: WorldState, rng: RngLike) -> list[Detection]:
        """Radar scan: real detections plus clutter-driven false alarms."""
        if self.effective_state(world) is DegradationState.FAILED:
            return []
        out: list[Detection] = []
        sx, sy, sz = self.effective_position(world)
        for ent in world.active_entities():
            pd = self.detection_probability(ent, world)
            if pd <= 0.0:
                continue
            if float(rng.random()) >= pd:
                continue
            ok, slant = self.in_range(ent, world)
            if not ok:
                continue
            out.append(self._make_detection(world, rng, ent, sx, sy, sz, slant))
        out.extend(self._false_alarms(world, rng, sx, sy, sz))
        return out

    def _make_detection(
        self,
        world: WorldState,
        rng: RngLike,
        ent: EntityState,
        sx: float,
        sy: float,
        sz: float,
        slant: float,
    ) -> Detection:
        strength = 1.0
        if self.effective_state(world) is DegradationState.PARTIAL:
            strength = 0.55
        range_sigma = float(np.clip(5.0 + slant * 0.0012, 5.0, 70.0)) / strength
        bearing_sigma_deg = float(np.clip(0.12 + slant / 14000.0, 0.12, 0.7)) / strength

        true_bearing = math.atan2(ent.position.y - sy, ent.position.x - sx)
        true_elev = math.atan2(
            ent.position.z - sz, max(1e-6, math.hypot(ent.position.x - sx, ent.position.y - sy))
        )
        bearing = true_bearing + math.radians(float(rng.normal(0.0, bearing_sigma_deg)))
        elev = true_elev + math.radians(float(rng.normal(0.0, bearing_sigma_deg * 0.7)))
        ground = math.hypot(ent.position.x - sx, ent.position.y - sy)
        px = sx + ground * math.cos(bearing) + float(rng.normal(0.0, range_sigma))
        py = sy + ground * math.sin(bearing) + float(rng.normal(0.0, range_sigma))
        pz = sz + ground * math.tan(elev) + float(rng.normal(0.0, range_sigma))

        los = math.dist((px, py, pz), (sx, sy, sz)) or 1.0
        radial = float(
            (
                (ent.position.x - sx) * ent.velocity.x
                + (ent.position.y - sy) * ent.velocity.y
                + (ent.position.z - sz) * ent.velocity.z
            )
            / los
        )
        has_rate = abs(radial) >= self.min_speed_mps
        radial_meas = radial + float(rng.normal(0.0, 1.2)) if has_rate else 0.0

        params = params_for(ent.entity_class)
        evidence = {k: v * 0.55 * strength for k, v in rcs_band_evidence(params.rcs_m2).items()}
        label_ev = _label_prior_from_class(evidence, ent, 0.35 * strength)
        return Detection(
            sensor_id=self.spec.id,
            sensor_type=self.kind.value,
            entity_id=ent.id,
            x=px,
            y=py,
            z=pz,
            t=world.t,
            range_m=slant,
            bearing_deg=math.degrees(true_bearing) % 360.0,
            altitude_m=ent.position.z,
            radial_mps=radial_meas,
            speed_sigma_mps=1.2,
            class_evidence=evidence,
            label_evidence=label_ev,
            has_range_rate=has_rate,
            strength=strength,
            hint_bias=ent.hint_bias,
        )

    def _false_alarms(
        self, world: WorldState, rng: RngLike, sx: float, sy: float, sz: float
    ) -> list[Detection]:
        rmin, rmax = self.effective_range(world)
        rate = self.base_clutter_rate * self.clutter_factor()
        count = int(rng.poisson(rate))
        out: list[Detection] = []
        fov = float(self.spec.fov_deg)
        run = self.runtime_of(world)
        centre = run.azimuth_deg if run else 0.0
        for _ in range(count):
            bearing = math.radians(centre + float(rng.uniform(-fov / 2.0, fov / 2.0)))
            slant = float(rng.uniform(max(rmin, 120.0), rmax))
            elev = math.radians(float(rng.uniform(0.02, 0.42)))
            ground = slant * math.cos(elev)
            x = sx + ground * math.cos(bearing)
            y = sy + ground * math.sin(bearing)
            z = max(2.0, sz + slant * math.sin(elev))
            out.append(
                Detection(
                    sensor_id=self.spec.id,
                    sensor_type=self.kind.value,
                    entity_id="",
                    x=x,
                    y=y,
                    z=z,
                    t=world.t,
                    range_m=slant,
                    bearing_deg=math.degrees(bearing) % 360.0,
                    altitude_m=z,
                    speed_sigma_mps=3.0,
                    class_evidence={},
                    label_evidence={},
                    has_range_rate=False,
                    strength=0.25,
                    hint_bias=0.0,
                )
            )
        return out


# --------------------------------------------------------------------------- #
# EO / IR camera
# --------------------------------------------------------------------------- #


class EoIrSensor(Sensor):
    """Steerable EO/IR turret: narrow FOV, strong class evidence when close."""

    kind = SensorType.EO_IR

    #: Angular size (radians) at which detection probability saturates.
    angular_threshold_rad: float = 0.0035
    #: Range inside which the sensor can attempt classification.
    classify_range_m: float = 420.0

    def _light_factor(self) -> float:
        tod = self.ctx.environment.time_of_day
        if tod is TimeOfDay.NIGHT:
            return 0.82
        if tod is TimeOfDay.DUSK:
            return 0.58
        return 1.0

    def detection_probability(self, entity: EntityState, world: WorldState) -> float:
        """Noise-free EO/IR detection probability."""
        if not entity.active or entity.neutralised:
            return 0.0
        if self.effective_state(world) is DegradationState.FAILED:
            return 0.0
        if not self.in_fov(world, entity.position):
            return 0.0
        ok, slant = self.in_range(entity, world)
        if not ok:
            return 0.0
        params = params_for(entity.entity_class)
        angular = params.visual_size_m / max(1.0, slant)
        if self.ctx.terrain.height_at(entity.position.x, entity.position.y) > entity.position.z:
            return 0.0
        pd = 0.95 * (angular / self.angular_threshold_rad)
        pd *= self._light_factor()
        pd *= self.los_factor(world, entity)
        pd *= self.visibility_factor()
        if self.effective_state(world) is DegradationState.PARTIAL:
            pd *= 0.6
        return float(np.clip(pd, 0.0, 0.97))

    def observe(self, world: WorldState, rng: RngLike) -> list[Detection]:
        """One EO/IR frame; returns at most one detection per visible entity."""
        if self.effective_state(world) is DegradationState.FAILED:
            return []
        out: list[Detection] = []
        sx, sy, sz = self.effective_position(world)
        vis = self.visibility_factor()
        tod = self.ctx.environment.time_of_day
        for ent in world.active_entities():
            pd = self.detection_probability(ent, world)
            if pd <= 0.0:
                continue
            if float(rng.random()) >= pd:
                continue
            ok, slant = self.in_range(ent, world)
            if not ok:
                continue
            out.append(self._make_detection(world, rng, ent, sx, sy, sz, slant, vis, tod))
        return out

    def _make_detection(
        self,
        world: WorldState,
        rng: RngLike,
        ent: EntityState,
        sx: float,
        sy: float,
        sz: float,
        slant: float,
        vis: float,
        tod: TimeOfDay,
    ) -> Detection:
        state = self.effective_state(world)
        strength = 1.0 if state is DegradationState.NONE else 0.6
        pos_sigma = float(np.clip(1.5 + slant * 0.0011, 1.5, 40.0)) / strength
        px = ent.position.x + float(rng.normal(0.0, pos_sigma))
        py = ent.position.y + float(rng.normal(0.0, pos_sigma))
        pz = ent.position.z + float(rng.normal(0.0, pos_sigma * 1.4))

        bearing = math.degrees(math.atan2(py - sy, px - sx)) % 360.0
        evidence: dict[EntityClass, float] = {}
        label_ev: dict[LabelType, float] = {}
        params = params_for(ent.entity_class)
        if slant <= self.classify_range_m:
            # Close enough to attempt identification: strong but fallible.
            confusion = 0.18 if tod is not TimeOfDay.DAY else 0.26
            reported = (
                _nearest_class(ent.entity_class)
                if float(rng.random()) < confusion * (0.5 + ent.hint_bias)
                else ent.entity_class
            )
            confidence = float(np.clip(0.95 - confusion * 1.2, 0.55, 0.95)) * strength
            evidence[reported] = confidence
            for alt, share in rcs_band_evidence(params.rcs_m2).items():
                evidence[alt] = evidence.get(alt, 0.0) + share * 0.18
            label_ev[_LABEL_PRIOR[reported]] = 0.85 * strength
            if ent.beacon_active and ent.label is LabelType.FRIENDLY:
                label_ev[LabelType.FRIENDLY] = 0.95 * strength
        else:
            band = rcs_band_evidence(params.visual_size_m * 1.2)
            for k, v in band.items():
                evidence[k] = v * 0.30 * strength
            label_ev = _label_prior_from_class(evidence, ent, 0.20 * strength)
        return Detection(
            sensor_id=self.spec.id,
            sensor_type=self.kind.value,
            entity_id=ent.id,
            x=px,
            y=py,
            z=pz,
            t=world.t,
            range_m=slant,
            bearing_deg=bearing,
            altitude_m=ent.position.z,
            radial_mps=float(np.dot(ent.velocity.as_tuple(), _unit(px - sx, py - sy, pz - sz))),
            speed_sigma_mps=2.0,
            class_evidence=evidence,
            label_evidence=label_ev,
            has_range_rate=True,
            strength=strength,
            hint_bias=ent.hint_bias,
        )


def _unit(x: float, y: float, z: float) -> tuple[float, float, float]:
    d = math.sqrt(x * x + y * y + z * z)
    if d < 1e-9:
        return (0.0, 0.0, 0.0)
    return (x / d, y / d, z / d)


# --------------------------------------------------------------------------- #
# RF detector
# --------------------------------------------------------------------------- #


class RfSensor(Sensor):
    """Abstract RF emitter detector: bearing-only, coarse range, RF-silent blind."""

    kind = SensorType.RF

    #: Range error scale for a bearing-only emitter fix (metres).
    position_sigma_m: float = 260.0
    bearing_sigma_deg: float = 4.0

    def detection_probability(self, entity: EntityState, world: WorldState) -> float:
        """Noise-free RF detection probability (zero for RF-silent entities)."""
        if not entity.active or entity.neutralised:
            return 0.0
        if self.effective_state(world) is DegradationState.FAILED:
            return 0.0
        if not entity.rf_active:
            return 0.0
        if not self.in_fov(world, entity.position):
            return 0.0
        ok, slant = self.in_range(entity, world)
        if not ok:
            return 0.0
        params = params_for(entity.entity_class)
        pd = 0.85 * (1.0 - (slant / max(1.0, self.effective_range(world)[1])) ** 1.4)
        pd *= max(0.25, params.acoustic_level + 0.45)
        pd *= self.visibility_factor(0.4)
        if self.effective_state(world) is DegradationState.PARTIAL:
            pd *= 0.6
        return float(np.clip(pd, 0.0, 0.95))

    def observe(self, world: WorldState, rng: RngLike) -> list[Detection]:
        """Emit one coarse, bearing-limited fix per detected emitter."""
        if self.effective_state(world) is DegradationState.FAILED:
            return []
        out: list[Detection] = []
        sx, sy, sz = self.effective_position(world)
        for ent in world.active_entities():
            pd = self.detection_probability(ent, world)
            if pd <= 0.0 or float(rng.random()) >= pd:
                continue
            ok, slant = self.in_range(ent, world)
            if not ok:
                continue
            strength = 1.0 if self.effective_state(world) is DegradationState.NONE else 0.6
            sig = self.position_sigma_m * (1.0 + slant / 3000.0) / strength
            px = ent.position.x + float(rng.normal(0.0, sig))
            py = ent.position.y + float(rng.normal(0.0, sig))
            pz = ent.position.z + float(rng.normal(0.0, sig * 1.2))
            evidence: dict[EntityClass, float] = {}
            for cls, prm in CLASS_TABLE.items():
                if prm.rf_active_default:
                    evidence[cls] = 0.12 * strength
            label_ev = {LabelType.HOSTILE: 0.08 * strength}
            if ent.beacon_active and ent.label is LabelType.FRIENDLY:
                label_ev[LabelType.FRIENDLY] = 0.8 * strength
            out.append(
                Detection(
                    sensor_id=self.spec.id,
                    sensor_type=self.kind.value,
                    entity_id=ent.id,
                    x=px,
                    y=py,
                    z=pz,
                    t=world.t,
                    range_m=slant,
                    bearing_deg=math.degrees(math.atan2(py - sy, px - sx)) % 360.0,
                    altitude_m=ent.position.z,
                    radial_mps=0.0,
                    speed_sigma_mps=6.0,
                    class_evidence=evidence,
                    label_evidence=label_ev,
                    has_range_rate=False,
                    strength=strength * 0.8,
                    hint_bias=ent.hint_bias,
                )
            )
        return out


# --------------------------------------------------------------------------- #
# Acoustic array
# --------------------------------------------------------------------------- #


class AcousticSensor(Sensor):
    """Short-range, wind-limited acoustic array: bearing only, very short reach."""

    kind = SensorType.ACOUSTIC

    position_sigma_m: float = 180.0

    def _wind_factor(self) -> float:
        env = self.ctx.environment
        wind = float(env.wind_speed_mps)
        return float(np.clip(1.0 - 0.035 * wind - 0.35 * float(env.rain), 0.12, 1.0))

    def detection_probability(self, entity: EntityState, world: WorldState) -> float:
        """Noise-free acoustic detection probability."""
        if not entity.active or entity.neutralised:
            return 0.0
        if self.effective_state(world) is DegradationState.FAILED:
            return 0.0
        ok, slant = self.in_range(entity, world)
        if not ok:
            return 0.0
        params = params_for(entity.entity_class)
        pd = (
            0.9
            * params.acoustic_level
            * (1.0 - (slant / max(1.0, self.effective_range(world)[1])) ** 1.1)
        )
        pd *= self._wind_factor()
        # Acoustics are indifferent to terrain masking but do not see far.
        if self.effective_state(world) is DegradationState.PARTIAL:
            pd *= 0.55
        return float(np.clip(pd, 0.0, 0.9))

    def observe(self, world: WorldState, rng: RngLike) -> list[Detection]:
        """Emit one coarse fix per audible entity inside the array's envelope."""
        if self.effective_state(world) is DegradationState.FAILED:
            return []
        out: list[Detection] = []
        sx, sy, sz = self.effective_position(world)
        wind = self._wind_factor()
        for ent in world.active_entities():
            pd = self.detection_probability(ent, world)
            if pd <= 0.0 or float(rng.random()) >= pd:
                continue
            ok, slant = self.in_range(ent, world)
            if not ok:
                continue
            strength = 0.7 * wind
            sig = self.position_sigma_m * (1.0 + slant / 900.0)
            px = ent.position.x + float(rng.normal(0.0, sig))
            py = ent.position.y + float(rng.normal(0.0, sig))
            pz = ent.position.z + float(rng.normal(0.0, sig))
            evidence = {EntityClass.BIRD_FLOCK: 0.10 * strength, ent.entity_class: 0.10 * strength}
            out.append(
                Detection(
                    sensor_id=self.spec.id,
                    sensor_type=self.kind.value,
                    entity_id=ent.id,
                    x=px,
                    y=py,
                    z=pz,
                    t=world.t,
                    range_m=slant,
                    bearing_deg=math.degrees(math.atan2(py - sy, px - sx)) % 360.0,
                    altitude_m=ent.position.z,
                    radial_mps=0.0,
                    speed_sigma_mps=5.0,
                    class_evidence=evidence,
                    label_evidence={_LABEL_PRIOR[ent.entity_class]: 0.10 * strength},
                    has_range_rate=False,
                    strength=max(0.2, strength),
                    hint_bias=ent.hint_bias,
                )
            )
        return out


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _label_prior_from_class(
    evidence: dict[EntityClass, float], ent: EntityState, weight: float
) -> dict[LabelType, float]:
    """Aggregate class evidence into a coarse label prior."""
    out: dict[LabelType, float] = {}
    for cls, ev in evidence.items():
        label = _LABEL_PRIOR[cls]
        out[label] = out.get(label, 0.0) + ev * weight
    if ent.label is LabelType.FRIENDLY and ent.beacon_active:
        out[LabelType.FRIENDLY] = out.get(LabelType.FRIENDLY, 0.0) + 0.4
    return out


_SENSOR_CLASSES: Final[dict[SensorType, type[Sensor]]] = {
    SensorType.RADAR: RadarSensor,
    SensorType.EO_IR: EoIrSensor,
    SensorType.RF: RfSensor,
    SensorType.ACOUSTIC: AcousticSensor,
}


def sensor_from_spec(spec: SensorSpec, ctx: SensorContext) -> Sensor:
    """Instantiate the sensor class matching `spec.type`."""
    cls = _SENSOR_CLASSES[spec.type]
    return cls(spec, ctx)


def build_sensors(scenario: Scenario, terrain: Terrain) -> list[Sensor]:
    """Instantiate every sensor declared by `scenario`."""
    ctx = SensorContext(terrain=terrain, environment=scenario.environment, scenario=scenario)
    return [sensor_from_spec(spec, ctx) for spec in scenario.sensors]


def first_detectable(sensors: list[Sensor], world: WorldState, entity: EntityState) -> bool:
    """True when at least one operational sensor could legitimately see `entity`.

    Noise-free, and evaluated at the threshold at which a track could actually
    be initiated (`_FIRST_DETECTABLE_PD`), which is what the ground-truth
    timeline in `core/scoring.py` is measured against.
    """
    for s in sensors:
        try:
            if s.detection_probability(entity, world) >= FIRST_DETECTABLE_PD:
                return True
        except Exception:  # pragma: no cover - defensive
            continue
    return False
