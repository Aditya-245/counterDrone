"""Pydantic v2 schemas for every SKYGYM payload, DB row and scenario file.

Design rules
------------
* Every persisted or transmitted model carries :data:`SCHEMA_VERSION`.
* ``core`` is pure: importing this module must not touch the database, the
  filesystem or the network.
* All magnitudes used for sensors and effectors are **generic, abstract and
  synthetic**. See ``README.md`` § Safety boundary.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)

#: Wire-format version. Bumped whenever a persisted payload changes shape.
SCHEMA_VERSION: int = 3


class VersionedModel(BaseModel):
    """Base for every model that crosses a persistence or network boundary."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: int = Field(
        default=SCHEMA_VERSION,
        ge=1,
        description="Wire schema version; unknown versions are rejected at the boundary.",
    )

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        """Refuse any wire version this build does not understand (DECISIONS D002)."""
        if v != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema_version {v}; this build understands {SCHEMA_VERSION}"
            )
        return v

    @classmethod
    def require_version(cls, value: Any) -> int:
        """Validate a decoded payload's schema version."""
        if not isinstance(value, int):
            raise ValueError(f"schema_version must be an integer, got {type(value)!r}")
        if value != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema_version {value}; this build understands {SCHEMA_VERSION}"
            )
        return value


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #


class TimeOfDay(str, Enum):
    """Lighting condition; drives EO/IR and radar clutter."""

    DAY = "day"
    DUSK = "dusk"
    NIGHT = "night"


class TerrainPreset(str, Enum):
    """Procedural terrain generator preset."""

    URBAN = "urban"
    RURAL = "rural"
    HILLS = "hills"


class SensorType(str, Enum):
    """The four sensor classes, behind one interface."""

    RADAR = "radar"
    EO_IR = "eo_ir"
    RF = "rf"
    ACOUSTIC = "acoustic"


class DegradationState(str, Enum):
    """Operational state of a sensor."""

    NONE = "none"
    PARTIAL = "partial"
    FAILED = "failed"


class EntityClass(str, Enum):
    """Physical class of an airborne object. Generic, synthetic categories."""

    QUAD_SMALL = "quad_small"
    FPV_FAST = "fpv_fast"
    FIXED_WING = "fixed_wing"
    BIRD_FLOCK = "bird_flock"
    KITE_BALLOON = "kite_balloon"
    FRIENDLY_UAV = "friendly_uav"
    CIVIL_AIRCRAFT = "civil_aircraft"


class LabelType(str, Enum):
    """Ground-truth identity."""

    HOSTILE = "hostile"
    CIVIL = "civil"
    FRIENDLY = "friendly"
    BIRD = "bird"


class BehaviourKind(str, Enum):
    """Scripted / procedural movement behaviours."""

    WAYPOINT = "waypoint"
    LOITER = "loiter"
    TERMINAL_DIVE = "terminal_dive"
    ORBIT_THEN_STRIKE = "orbit_then_strike"
    DECOY_LURE = "decoy_lure"
    SWARM = "swarm"
    TRANSIT = "transit"


class FormationKind(str, Enum):
    """Swarm formation presets."""

    LINE = "line"
    WEDGE = "wedge"
    SCATTER = "scatter"
    PINCER = "pincer"
    LEADER_FOLLOWER = "leader_follower"


class ActionType(str, Enum):
    """Everything a trainee (or the oracle) can do."""

    ACK_TRACK = "ACK_TRACK"
    CLASSIFY = "CLASSIFY"
    SET_PRIORITY = "SET_PRIORITY"
    STEER_SENSOR = "STEER_SENSOR"
    WARN = "WARN"
    ENGAGE_S = "ENGAGE_S"
    ENGAGE_H = "ENGAGE_H"
    HOLD_FIRE = "HOLD_FIRE"
    REQUEST_ID = "REQUEST_ID"
    PAUSE_REQUEST = "PAUSE_REQUEST"


class Verdict(str, Enum):
    """Every leaf of the ROE decision tree."""

    CORRECT_ENGAGE = "CORRECT_ENGAGE"
    CORRECT_HOLD = "CORRECT_HOLD"
    LATE_ENGAGE = "LATE_ENGAGE"
    MISSED_THREAT = "MISSED_THREAT"
    FALSE_ENGAGE_CIVIL = "FALSE_ENGAGE_CIVIL"
    FALSE_ENGAGE_FRIENDLY = "FALSE_ENGAGE_FRIENDLY"
    COLLATERAL_VIOLATION = "COLLATERAL_VIOLATION"
    PREMATURE_ENGAGE = "PREMATURE_ENGAGE"
    UNNECESSARY_ESCALATION = "UNNECESSARY_ESCALATION"


class EventKind(str, Enum):
    """Kinds of events recorded in the session event log."""

    SPAWN = "spawn"
    RING_CROSSING = "ring_crossing"
    SENSOR_DEGRADED = "sensor_degraded"
    SENSOR_RECOVERED = "sensor_recovered"
    SENSOR_FAILURE = "sensor_failure"
    ENGAGEMENT = "engagement"
    ENGAGEMENT_RESULT = "engagement_result"
    TRACK_INITIATED = "track_initiated"
    TRACK_DELETED = "track_deleted"
    SCENARIO_EVENT = "scenario_event"
    COMMS_LOSS = "comms_loss"
    END = "end"


class Role(str, Enum):
    """Team-mode roles (section 7.8) and the single-player equivalence."""

    TRAINEE = "trainee"
    SENSOR_OPERATOR = "sensor_operator"
    COMMANDER = "commander"
    EFFECTOR_OPERATOR = "effector_operator"
    INSTRUCTOR = "instructor"


class RuleSetId(str, Enum):
    """Shipped ROE rule sets."""

    STRICT = "STRICT"
    STANDARD = "STANDARD"
    PERMISSIVE = "PERMISSIVE"


# --------------------------------------------------------------------------- #
# Small value objects
# --------------------------------------------------------------------------- #


class Vec3(BaseModel):
    """A 3-vector in local ENU metres."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def as_tuple(self) -> tuple[float, float, float]:
        """Return ``(x, y, z)``."""
        return (self.x, self.y, self.z)

    def norm(self) -> float:
        """Euclidean length."""
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def as_list(self) -> list[float]:
        """Return a JSON-friendly list."""
        return [self.x, self.y, self.z]


class BearingPoint(BaseModel):
    """A polar spawn specification: bearing from the site plus slant range."""

    bearing_deg: float = Field(ge=0.0, le=360.0, description="Clockwise from +X (east).")
    range_m: float = Field(ge=50.0, le=6000.0)
    altitude_m: float = Field(default=120.0, ge=0.0, le=1200.0)


# --------------------------------------------------------------------------- #
# Terrain / environment / site
# --------------------------------------------------------------------------- #


class TerrainSpec(VersionedModel):
    """Terrain description: procedural preset or imported heightmap."""

    preset: TerrainPreset = TerrainPreset.RURAL
    size_m: float = Field(default=6000.0, gt=500.0, description="Square ENU extent.")
    resolution: int = Field(default=256, ge=64, le=1024, description="Grid posts per side.")
    scale_m: float = Field(default=180.0, ge=1.0, description="Peak height above datum.")
    roughness: float = Field(default=0.55, ge=0.0, le=1.0)
    seed: int = 0
    heightmap_path: str | None = Field(
        default=None, description="Optional PNG; grayscale -> metres using scale_m."
    )
    max_height_m: float | None = Field(
        default=None, description="Override for imported heightmaps (0..255 -> 0..max)."
    )
    buildings: list[BuildingSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_heightmap(self) -> Self:
        if self.heightmap_path is not None and not self.heightmap_path.strip():
            raise ValueError("heightmap_path must not be blank")
        return self


class BuildingSpec(VersionedModel):
    """An abstract building block, used for LOS/occlusion in urban presets.

    `rotation_deg` turns the footprint so settlements and city blocks can follow
    the road they front instead of sitting on a world-axis grid; the line-of-sight
    test rotates the ray into the footprint's frame, so what the renderer draws is
    what actually occludes.
    """

    x: float
    y: float
    width_m: float = Field(gt=0.0)
    depth_m: float = Field(gt=0.0)
    height_m: float = Field(gt=0.0)
    rotation_deg: float = 0.0


# --------------------------------------------------------------------------- #
# Landscape (what the client draws)
# --------------------------------------------------------------------------- #


class RoadKind(str, Enum):
    """Road classes, widest first: the width drives the carriageway."""

    ARTERIAL = "arterial"
    STREET = "street"
    LANE = "lane"
    TRACK = "track"


class BuildingKind(str, Enum):
    """Building archetypes the renderer knows how to build procedurally."""

    HOUSE_GABLE = "house_gable"
    HOUSE_HIP = "house_hip"
    HOUSE_FLAT = "house_flat"
    SHOP = "shop"
    APARTMENT = "apartment"
    WAREHOUSE = "warehouse"
    SHED = "shed"
    TOWER = "tower"


class TreeKind(str, Enum):
    """Vegetation archetypes."""

    BROADLEAF = "broadleaf"
    CONIFER = "conifer"
    DEAD = "dead"


class PropKind(str, Enum):
    """Small clutter: walls, fences, hedgerows, lamps, vehicles."""

    FENCE = "fence"
    WALL = "wall"
    HEDGE = "hedge"
    LAMP = "lamp"
    VEHICLE = "vehicle"
    HAYSTACK = "haystack"
    SILO = "silo"


class WaterKind(str, Enum):
    """Surface water."""

    POND = "pond"
    STREAM = "stream"


class RoadSpec(VersionedModel):
    """One road centre line, drawn as a ribbon of `width_m`."""

    kind: RoadKind = RoadKind.STREET
    width_m: float = 10.0
    markings: bool = False
    points: list[tuple[float, float]] = Field(default_factory=list)


class BuildingInstance(VersionedModel):
    """One building to draw, carrying its procedural archetype."""

    x: float
    y: float
    width_m: float
    depth_m: float
    height_m: float
    rotation_deg: float = 0.0
    kind: BuildingKind = BuildingKind.HOUSE_GABLE
    occluder: bool = True


class TreeInstance(VersionedModel):
    """One vegetation instance."""

    x: float
    y: float
    height_m: float = 9.0
    radius_m: float = 3.5
    kind: TreeKind = TreeKind.BROADLEAF


class PropInstance(VersionedModel):
    """One piece of small clutter."""

    x: float
    y: float
    rotation_deg: float = 0.0
    length_m: float = 2.0
    kind: PropKind = PropKind.FENCE


class WaterSpec(VersionedModel):
    """A pond or stream surface, as a closed or open polyline."""

    kind: WaterKind = WaterKind.POND
    width_m: float = 6.0
    points: list[tuple[float, float]] = Field(default_factory=list)


class FieldPatch(VersionedModel):
    """One agricultural field: a land-cover index and a closed outline."""

    index: int = 0
    points: list[tuple[float, float]] = Field(default_factory=list)


class LandscapeSpec(VersionedModel):
    """Everything the client needs to draw the world for one scenario.

    `heights_b64` is a base64 little-endian int16 grid of centimetres,
    `landcover_b64` a base64 uint8 grid indexing `palette`. Both are
    `grid_n` square and cover `[-size_m/2, size_m/2]` on both axes.
    """

    scenario_id: str
    seed: int
    size_m: float
    grid_n: int
    max_height_m: float
    heights_b64: str
    landcover_b64: str
    palette: list[str] = Field(default_factory=list)
    roads: list[RoadSpec] = Field(default_factory=list)
    buildings: list[BuildingInstance] = Field(default_factory=list)
    fields: list[FieldPatch] = Field(default_factory=list)
    trees: list[TreeInstance] = Field(default_factory=list)
    props: list[PropInstance] = Field(default_factory=list)
    water: list[WaterSpec] = Field(default_factory=list)


class EnvSpec(VersionedModel):
    """Atmospheric / lighting environment."""

    time_of_day: TimeOfDay = TimeOfDay.DAY
    visibility_m: float = Field(
        default=8000.0, ge=50.0, le=20000.0, description="Meteorological range."
    )
    rain: float = Field(default=0.0, ge=0.0, le=1.0)
    wind_speed_mps: float = Field(default=2.0, ge=0.0, le=35.0)
    wind_heading_deg: float = Field(default=270.0, ge=0.0, le=360.0)

    @property
    def is_night(self) -> bool:
        """True when lighting degrades EO performance and improves IR."""
        return self.time_of_day is TimeOfDay.NIGHT


class CivilSector(VersionedModel):
    """A bearing wedge declared as containing civilians."""

    bearing_deg: float = Field(ge=0.0, le=360.0)
    half_width_deg: float = Field(default=20.0, gt=0.0, le=90.0)
    inner_m: float = Field(default=0.0, ge=0.0)
    outer_m: float = Field(default=6000.0, gt=0.0)
    label: str = "civil sector"


class SiteSpec(VersionedModel):
    """The defended site: protected zone, perimeter rings, civil sectors."""

    protected_radius_m: float = Field(default=150.0, gt=10.0)
    ring_distances_m: list[float] = Field(
        default_factory=lambda: [500.0, 1000.0, 2000.0, 3000.0],
        description="Perimeter rings, ascending.",
    )
    civil_sectors: list[CivilSector] = Field(default_factory=list)

    @field_validator("ring_distances_m")
    @classmethod
    def _ascending_positive(cls, v: list[float]) -> list[float]:
        if any(x <= 0 for x in v):
            raise ValueError("ring distances must be positive")
        if v != sorted(v):
            raise ValueError("ring distances must be ascending")
        return v


# --------------------------------------------------------------------------- #
# Sensors
# --------------------------------------------------------------------------- #


class SensorDegradation(VersionedModel):
    """A timed change of a sensor's operational state."""

    t: float = Field(ge=0.0, description="Seconds from scenario start.")
    state: DegradationState
    note: str = ""


class SensorSpec(VersionedModel):
    """Placement and static configuration of one sensor."""

    id: str = Field(min_length=1, max_length=32)
    type: SensorType
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    fov_deg: float = Field(default=360.0, gt=0.0, le=360.0, description="Azimuthal field of view.")
    min_range_m: float = Field(default=0.0, ge=0.0)
    max_range_m: float = Field(gt=0.0, default=6000.0)
    initial_state: DegradationState = DegradationState.NONE
    degradations: list[SensorDegradation] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    drift_mps: float = Field(
        default=0.0, ge=0.0, description="GPS-denied position drift rate (metres per second)."
    )

    @field_validator("degradations")
    @classmethod
    def _sorted_degradations(cls, v: list[SensorDegradation]) -> list[SensorDegradation]:
        times = [d.t for d in v]
        if times != sorted(times):
            raise ValueError("sensor degradations must be sorted by time")
        return v


# --------------------------------------------------------------------------- #
# Threats
# --------------------------------------------------------------------------- #


class BehaviourSpec(VersionedModel):
    """Parameters for a scripted or procedural behaviour."""

    kind: BehaviourKind = BehaviourKind.WAYPOINT
    waypoints: list[Vec3] = Field(default_factory=list)
    loiter_radius_m: float = Field(default=120.0, ge=5.0)
    loiter_period_s: float = Field(default=40.0, gt=0.0)
    speed_mps: float | None = Field(default=None, ge=0.0, le=120.0)
    terminal_altitude_m: float = Field(default=30.0, ge=0.0)
    strike_bearing_deg: float | None = Field(default=None, ge=0.0, le=360.0)
    orbit_radius_m: float = Field(default=180.0, ge=10.0)
    orbit_period_s: float = Field(default=55.0, gt=0.0)
    separation_m: float = Field(default=18.0, ge=1.0)
    cohesion_weight: float = Field(default=0.35, ge=0.0, le=2.0)
    alignment_weight: float = Field(default=0.25, ge=0.0, le=2.0)
    terrain_following: bool = Field(
        default=False,
        description="Hold a clearance above local ground instead of a fixed altitude.",
    )
    min_clearance_m: float = Field(default=18.0, ge=2.0, le=400.0)


class ThreatSpec(VersionedModel):
    """A group of entities spawned as one wave."""

    name: str = Field(min_length=1, max_length=64)
    entity_class: EntityClass
    label: LabelType
    count: int = Field(default=1, ge=1, le=64)
    spawn_t: float = Field(default=0.0, ge=0.0, description="Earliest spawn time.")
    spawn_window_s: float = Field(default=0.0, ge=0.0)
    spawn_points: list[BearingPoint] = Field(
        default_factory=list, description="Empty means 'procedural from bearing/jitter'."
    )
    spawn_bearing_deg: float = Field(default=45.0, ge=0.0, le=360.0)
    spawn_jitter_deg: float = Field(default=25.0, ge=0.0, le=180.0)
    behaviour: BehaviourSpec = Field(default_factory=BehaviourSpec)
    formation: FormationKind = FormationKind.LEADER_FOLLOWER
    rf_active: bool | None = Field(
        default=None, description="None means 'use the class table default'."
    )
    beacon_active: bool | None = None
    hint_bias: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Bias of weak class hints toward 'hostile'."
    )
    spawn_offset_m: float = Field(default=0.0, description="Formation offset along the bearing.")

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        if self.spawn_window_s < 0:
            raise ValueError("spawn_window_s must be >= 0")
        return self


# --------------------------------------------------------------------------- #
# Effectors (abstract, synthetic - see README safety boundary)
# --------------------------------------------------------------------------- #


class EffectorSpec(VersionedModel):
    """An abstract effector.

    ``Effector-S`` is a soft-kill effector, ``Effector-H`` a hard-kill effector.
    Ranges, cooldowns and probabilities below are invented for simulation and do
    not describe any real system.
    """

    id: str = Field(min_length=1, max_length=32)
    kind: Literal["soft", "hard"] = "soft"
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    min_range_m: float = Field(default=0.0, ge=0.0)
    max_range_m: float = Field(default=1500.0, gt=0.0)
    cooldown_s: float = Field(default=6.0, ge=0.0)
    ammunition: int = Field(default=12, ge=0, le=999)
    reload_s: float = Field(default=0.0, ge=0.0)
    base_success: float = Field(default=0.75, ge=0.0, le=1.0)
    susceptible_to_rf_silence: bool = Field(
        default=True, description="Soft-kill is less effective against RF-silent targets."
    )

    @model_validator(mode="after")
    def _check_range(self) -> Self:
        if self.min_range_m >= self.max_range_m:
            raise ValueError("effector min_range_m must be < max_range_m")
        return self


# --------------------------------------------------------------------------- #
# Rules of engagement
# --------------------------------------------------------------------------- #


class RoeSpec(VersionedModel):
    """Which ROE rule set applies and its scalar parameters."""

    rule_set: RuleSetId = RuleSetId.STANDARD
    require_id_before_engage: bool = Field(
        default=False, description="TRUE for STRICT: positive ID required before any engagement."
    )
    allowed_engage_labels: list[LabelType] = Field(
        default_factory=lambda: [LabelType.HOSTILE],
        description="Ground-truth labels the rule set permits engaging (evaluation only).",
    )
    friend_or_civil_warning_required: bool = True
    collateral_sector_tolerance: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Allowed fraction of civil-sector bearing."
    )
    max_time_to_impact_s: float = Field(
        default=999.0, gt=0.0, description="Engage after this time-to-impact is 'late'."
    )
    late_engage_threshold_s: float = Field(default=20.0, ge=0.0)
    min_track_quality: float = Field(default=0.15, ge=0.0, le=1.0)
    require_warning_before_engage: bool = Field(default=True)


# --------------------------------------------------------------------------- #
# Scenario events
# --------------------------------------------------------------------------- #


class ScenarioEvent(VersionedModel):
    """A timed in-scenario event injected by the scenario author."""

    t: float = Field(ge=0.0)
    kind: Literal[
        "sensor_failure",
        "sensor_partial",
        "sensor_restore",
        "new_wave",
        "comms_loss",
        "comms_restore",
        "gps_denied",
        "gps_restored",
        "note",
    ]
    target: str | None = Field(default=None, description="Sensor id or wave name.")
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""

    @field_validator("t")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("event time must be finite")
        return v


# --------------------------------------------------------------------------- #
# Difficulty
# --------------------------------------------------------------------------- #


class DifficultyVector(VersionedModel):
    """Normalised 0..1 difficulty knobs, one per adaptive axis."""

    swarm_size: float = Field(default=0.3, ge=0.0, le=1.0)
    approach_axes: float = Field(default=0.3, ge=0.0, le=1.0)
    time_pressure: float = Field(default=0.3, ge=0.0, le=1.0)
    sensor_degradation_level: float = Field(default=0.2, ge=0.0, le=1.0)
    decoy_ratio: float = Field(default=0.2, ge=0.0, le=1.0)
    visibility: float = Field(default=0.7, ge=0.0, le=1.0)
    night: float = Field(default=0.0, ge=0.0, le=1.0)
    ambiguity: float = Field(default=0.2, ge=0.0, le=1.0)
    simultaneity: float = Field(default=0.3, ge=0.0, le=1.0)
    roe_strictness: float = Field(default=0.3, ge=0.0, le=1.0)

    AXES: ClassVar[tuple[str, ...]] = (
        "swarm_size",
        "approach_axes",
        "time_pressure",
        "sensor_degradation_level",
        "decoy_ratio",
        "visibility",
        "night",
        "ambiguity",
        "simultaneity",
        "roe_strictness",
    )

    def to_array(self) -> list[float]:
        """Return the axis values in the declared order."""
        return [float(getattr(self, name)) for name in self.AXES]

    @classmethod
    def from_array(cls, values: list[float]) -> DifficultyVector:
        """Build a vector from a 10-element array (inverse of `to_array`)."""
        if len(values) != len(cls.AXES):
            raise ValueError(f"expected {len(cls.AXES)} difficulty values, got {len(values)}")
        data = {name: float(v) for name, v in zip(cls.AXES, values, strict=True)}
        return cls(**cast("dict[str, Any]", data))

    @property
    def mean(self) -> float:
        """Unweighted mean of all axes, used as a coarse difficulty scalar."""
        return sum(self.to_array()) / len(self.AXES)

    def with_axis(self, axis: str, value: float) -> DifficultyVector:
        """Return a copy with one axis replaced (validated)."""
        if axis not in self.AXES:
            raise ValueError(f"unknown difficulty axis {axis!r}")
        data = {name: float(getattr(self, name)) for name in self.AXES}
        data[axis] = max(0.0, min(1.0, float(value)))
        return DifficultyVector(**cast("dict[str, Any]", data))

    def resolve(self) -> ResolvedDifficulty:
        """Map normalised axes onto concrete scenario magnitudes."""
        from app.core.generator_support import resolve_difficulty

        return resolve_difficulty(self)


class ResolvedDifficulty(BaseModel):
    """Concrete magnitudes a DifficultyVector implies."""

    swarm_size: int
    approach_axes: int
    spawn_compression_s: float
    degraded_sensor_count: int
    decoy_count: int
    visibility_m: float
    is_night: bool
    ambiguity: float
    simultaneity: float
    roe_rule_set: RuleSetId
    roe_strictness: float


# --------------------------------------------------------------------------- #
# Scenario
# --------------------------------------------------------------------------- #


class Scenario(VersionedModel):
    """A complete, self-contained simulation scenario."""

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    seed: int = Field(ge=0, le=2**31 - 1)
    duration_s: int = Field(default=600, gt=0, le=3600)
    terrain: TerrainSpec = Field(default_factory=TerrainSpec)
    environment: EnvSpec = Field(default_factory=EnvSpec)
    site: SiteSpec = Field(default_factory=SiteSpec)
    sensors: list[SensorSpec] = Field(default_factory=list)
    threats: list[ThreatSpec] = Field(default_factory=list)
    decoys: list[ThreatSpec] = Field(default_factory=list)
    effectors: list[EffectorSpec] = Field(default_factory=list)
    roe: RoeSpec = Field(default_factory=RoeSpec)
    events: list[ScenarioEvent] = Field(default_factory=list)
    difficulty: DifficultyVector = Field(default_factory=DifficultyVector)
    tags: list[str] = Field(default_factory=list)
    instructor_locked: bool = False
    source: Literal["shipped", "generated", "builder", "nlu", "template"] = "shipped"

    @model_validator(mode="after")
    def _structural_checks(self) -> Self:
        if not self.sensors:
            raise ValueError("a scenario needs at least one sensor")
        if not self.effectors:
            raise ValueError("a scenario needs at least one effector")
        if not self.threats and not self.decoys:
            raise ValueError("a scenario needs at least one threat or decoy")
        sensor_ids = [s.id for s in self.sensors]
        if len(set(sensor_ids)) != len(sensor_ids):
            raise ValueError("sensor ids must be unique")
        effector_ids = [e.id for e in self.effectors]
        if len(set(effector_ids)) != len(effector_ids):
            raise ValueError("effector ids must be unique")
        ts = [e.t for e in self.events]
        if ts != sorted(ts):
            raise ValueError("scenario events must be sorted by time")
        for spec in (*self.threats, *self.decoys):
            if spec.spawn_t > self.duration_s:
                raise ValueError(f"threat {spec.name!r} spawns after the scenario ends")
        for sensor in self.sensors:
            if sensor.min_range_m >= sensor.max_range_m:
                raise ValueError(f"sensor {sensor.id!r} min_range_m must be < max_range_m")
            for deg in sensor.degradations:
                if deg.t > self.duration_s:
                    raise ValueError(f"sensor {sensor.id!r} degrades after the scenario ends")
        return self

    @property
    def all_waves(self) -> list[ThreatSpec]:
        """Threats and decoys together, in spawn order."""
        return sorted([*self.threats, *self.decoys], key=lambda w: w.spawn_t)

    def threat_count(self) -> int:
        """Total number of entities the scenario will spawn."""
        return sum(w.count for w in (*self.threats, *self.decoys))

    def hostile_count(self) -> int:
        """Number of entities whose ground-truth label is hostile."""
        return sum(w.count for w in self.threats)


# --------------------------------------------------------------------------- #
# Trainee actions and events
# --------------------------------------------------------------------------- #


class TraineeAction(VersionedModel):
    """One trainee decision, as stored in the action log.

    The ordered log plus the seed *is* the determinism contract.
    """

    t: float = Field(ge=0.0)
    type: ActionType
    track_id: str | None = Field(default=None, max_length=48)
    params: dict[str, Any] = Field(default_factory=dict)
    source: Literal["trainee", "oracle", "scripted", "instructor"] = "trainee"

    def is_engagement(self) -> bool:
        """True for the two engagement action types."""
        return self.type in (ActionType.ENGAGE_S, ActionType.ENGAGE_H)

    def effector_id(self) -> str | None:
        """Effector named by an engagement action, if any."""
        if not self.is_engagement():
            return None
        value = self.params.get("effector")
        return str(value) if value is not None else None


class SimEvent(VersionedModel):
    """A recorded simulation event."""

    t: float = Field(ge=0.0)
    kind: EventKind
    entity_id: str | None = None
    track_id: str | None = None
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Tracks (the only thing the client ever sees about an entity)
# --------------------------------------------------------------------------- #


class TrackView(VersionedModel):
    """A fused, trainee-visible track. Contains no ground truth."""

    id: str
    x: float
    y: float
    z: float
    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0
    position_sigma_m: float = 100.0
    velocity_sigma_mps: float = 10.0
    quality: float = Field(ge=0.0, le=1.0)
    age_s: float = 0.0
    sensors: list[str] = Field(default_factory=list)
    suggested_class: EntityClass | None = None
    suggested_label: LabelType | None = None
    class_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    range_m: float = 0.0
    bearing_deg: float = 0.0
    altitude_m: float = 0.0
    speed_mps: float = 0.0
    radial_mps: float = 0.0
    time_to_perimeter_s: float | None = None
    acknowledged: bool = False
    trainee_class: LabelType | None = None
    trainee_priority: int | None = None
    neutralised: bool = False
    missed: bool = False
    confirmed: bool = False


class SensorHealthView(VersionedModel):
    """Sensor status strip entry."""

    id: str
    type: SensorType
    state: DegradationState
    azimuth_deg: float = 0.0
    fov_deg: float = 360.0
    detections_last_step: int = 0
    note: str = ""


class EffectorView(VersionedModel):
    """Effector status panel entry."""

    id: str
    kind: Literal["soft", "hard"]
    ready: bool = True
    cooldown_remaining_s: float = 0.0
    ammunition: int = 0
    max_range_m: float = 0.0


class StateFrame(VersionedModel):
    """The 10 Hz authoritative frame pushed over the WebSocket."""

    session_id: str
    t: float
    step: int
    duration_s: float
    status: Literal["running", "paused", "finished"] = "running"
    tracks: list[TrackView] = Field(default_factory=list)
    sensors: list[SensorHealthView] = Field(default_factory=list)
    effectors: list[EffectorView] = Field(default_factory=list)
    events: list[SimEvent] = Field(default_factory=list)
    ammo_remaining: dict[str, int] = Field(default_factory=dict)
    paused: bool = False
    allow_pause: bool = False


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


class VerdictRecord(VersionedModel):
    """One ROE evaluation, with the tree path that produced it."""

    entity_id: str
    track_id: str | None = None
    t: float
    verdict: Verdict
    points: float
    path: list[str] = Field(default_factory=list)
    explanation: str
    true_label: LabelType
    trainee_label: LabelType | None = None
    effector: str | None = None
    range_m: float | None = None
    collateral_risk: bool = False


class EntityScore(VersionedModel):
    """Per-entity detail block in the score breakdown."""

    entity_id: str
    entity_class: EntityClass
    true_label: LabelType
    t_first_detectable: float | None = None
    t_first_ack: float | None = None
    detection_latency_s: float | None = None
    trainee_label: LabelType | None = None
    t_classified: float | None = None
    classification_correct: bool | None = None
    classification_calibration: str = "none"
    t_engaged: float | None = None
    engage_range_m: float | None = None
    effector: str | None = None
    neutralised: bool = False
    reached_perimeter: bool = False
    t_perimeter: float | None = None
    t_closest_approach: float | None = None
    ring_crossings: dict[str, float] = Field(default_factory=dict)
    verdicts: list[VerdictRecord] = Field(default_factory=list)
    score: float = 0.0


class CoachingPoint(VersionedModel):
    """A rule-based coaching message (never LLM generated)."""

    code: str
    severity: Literal["info", "warn", "critical"]
    text: str
    related_entities: list[str] = Field(default_factory=list)


class ScoreWeights(VersionedModel):
    """Configurable sub-score weights (must sum to 100)."""

    detection: float = 20.0
    classification: float = 25.0
    prioritisation: float = 15.0
    engagement: float = 30.0
    workload: float = 10.0

    @model_validator(mode="after")
    def _sums_to_100(self) -> Self:
        total = self.detection + self.classification + self.prioritisation
        total += self.engagement + self.workload
        if abs(total - 100.0) > 1e-6:
            raise ValueError(f"score weights must sum to 100, got {total}")
        return self


class ScoreBreakdown(VersionedModel):
    """The full score of a session."""

    total: float = Field(ge=0.0, le=100.0)
    raw_total: float = Field(ge=0.0, le=100.0)
    detection: float = Field(default=0.0, ge=0.0, le=20.0)
    classification: float = Field(default=0.0, ge=0.0, le=25.0)
    prioritisation: float = Field(default=0.0, ge=0.0, le=15.0)
    engagement: float = Field(default=0.0, ge=0.0, le=30.0)
    workload: float = Field(default=0.0, ge=0.0, le=10.0)
    weights: ScoreWeights = Field(default_factory=ScoreWeights)
    entities: list[EntityScore] = Field(default_factory=list)
    verdicts: list[VerdictRecord] = Field(default_factory=list)
    verdict_counts: dict[str, int] = Field(default_factory=dict)
    coaching_points: list[CoachingPoint] = Field(default_factory=list)
    capped: bool = False
    cap_reason: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)


class SessionRecord(VersionedModel):
    """A persisted training session."""

    id: str
    scenario_id: str
    seed: int
    trainee_id: str
    started_at: str
    ended_at: str | None = None
    actions: list[TraineeAction] = Field(default_factory=list)
    events_log: list[SimEvent] = Field(default_factory=list)
    final_state_hash: str = ""
    score: ScoreBreakdown = Field(default_factory=lambda: ScoreBreakdown(total=0.0, raw_total=0.0))
    difficulty: DifficultyVector = Field(default_factory=DifficultyVector)
    aborted: bool = False
    synthetic: bool = False


# --------------------------------------------------------------------------- #
# Skills and habits
# --------------------------------------------------------------------------- #


class SkillVector(VersionedModel):
    """Five skill ratings in [0, 100]."""

    detection: float = 50.0
    classification: float = 50.0
    prioritisation: float = 50.0
    engagement: float = 50.0
    roe: float = 50.0

    NAMES: ClassVar[tuple[str, ...]] = (
        "detection",
        "classification",
        "prioritisation",
        "engagement",
        "roe",
    )

    def to_array(self) -> list[float]:
        """Values in `NAMES` order."""
        return [float(getattr(self, n)) for n in self.NAMES]

    @classmethod
    def from_array(cls, values: list[float]) -> SkillVector:
        """Inverse of `to_array`."""
        if len(values) != 5:
            raise ValueError("expected exactly five skill values")
        return cls(
            **cast("dict[str, Any]", dict(zip(cls.NAMES, (float(v) for v in values), strict=True)))
        )

    @property
    def weakest(self) -> str:
        """Name of the lowest-rated skill."""
        return min(self.NAMES, key=lambda n: float(getattr(self, n)))

    @property
    def mean(self) -> float:
        """Mean skill rating."""
        return sum(self.to_array()) / 5.0


class HabitFeatures(VersionedModel):
    """Per-trainee habit features derived from the action log (DECISIONS D013)."""

    sector_scan_share: list[float] = Field(default_factory=lambda: [1 / 8] * 8)
    mean_detection_latency_by_sector: list[float] = Field(default_factory=lambda: [30.0] * 8)
    over_engage_rate: float = 0.0
    under_engage_rate: float = 0.0
    saturation_reaction_ms: float = 0.0
    mean_actions_per_10s: float = 1.0
    classify_early_rate: float = 0.0
    hold_fire_rate: float = 0.0
    least_scanned_sector: int = 0
    most_scanned_sector: int = 0
    sessions_observed: int = 0
    total_actions: int = 0

    def context_vector(self, difficulty: DifficultyVector) -> list[float]:
        """Flatten into the fixed-order context vector the bandit consumes."""
        return [
            *self.sector_scan_share,
            *(v / 120.0 for v in self.mean_detection_latency_by_sector),
            self.over_engage_rate,
            self.under_engage_rate,
            self.saturation_reaction_ms / 3000.0,
            self.mean_actions_per_10s / 10.0,
            self.classify_early_rate,
            self.hold_fire_rate,
            *difficulty.to_array(),
        ]

    @property
    def context_dim(self) -> int:
        """Length of `context_vector`."""
        return 8 + 8 + 6 + 10


# --------------------------------------------------------------------------- #
# Red Brain
# --------------------------------------------------------------------------- #


class Tactic(VersionedModel):
    """One attacker tactic, i.e. one bandit arm."""

    id: str
    name: str
    approach_bearings: list[int] = Field(default_factory=list)
    altitude_profile: Literal["high", "terrain_hugging"] = "high"
    speed_profile: Literal["slow", "medium", "fast"] = "medium"
    timing: Literal["simultaneous", "staggered"] = "staggered"
    decoy: Literal["none", "bird_mimic", "lure"] = "none"
    formation: FormationKind = FormationKind.LEADER_FOLLOWER
    description: str = ""


class HabitExploit(VersionedModel):
    """A verified, data-backed statement about what the adversary exploited."""

    habit: str
    description: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class RedBrainReport(VersionedModel):
    """What the adversary learned about a trainee and what it did about it."""

    trainee_id: str
    tactic_id: str
    tactic_name: str
    tactics_considered: int
    exploration_pulls: int
    total_pulls: int
    exploits: list[HabitExploit] = Field(default_factory=list)
    available_tactics: list[str] = Field(default_factory=list)
    learned_policy_available: bool = False


# --------------------------------------------------------------------------- #
# AAR / counterfactual fork
# --------------------------------------------------------------------------- #


class FrameSample(VersionedModel):
    """One recorded replay frame."""

    t: float
    x: float
    y: float
    z: float


class GhostAction(VersionedModel):
    """What the oracle would have done."""

    t: float
    type: ActionType
    track_id: str | None = None
    entity_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class ReplayEntity(VersionedModel):
    """Ground-truth entity track for the AAR replay (post-mortem only)."""

    id: str
    entity_class: EntityClass
    label: LabelType
    path: list[FrameSample] = Field(default_factory=list)
    t_first_detectable: float | None = None
    ring_crossings: dict[str, float] = Field(default_factory=dict)
    t_closest_approach: float | None = None
    neutralised_t: float | None = None
    reached_perimeter_t: float | None = None


class ReplaySession(VersionedModel):
    """A full deterministic replay of a session."""

    session_id: str
    scenario_id: str
    duration_s: float
    final_state_hash: str
    ticks_per_second: int
    trainee_actions: list[TraineeAction]
    ghost_actions: list[GhostAction]
    entities: list[ReplayEntity] = Field(default_factory=list)
    frames: list[StateFrame] = Field(default_factory=list)
    events: list[SimEvent] = Field(default_factory=list)
    sector_attention: list[float] = Field(default_factory=list)
    score: ScoreBreakdown


class ForkRequest(VersionedModel):
    """Re-simulate from a decision point with a modified action."""

    session_id: str
    fork_t: float = Field(ge=0.0)
    replace_index: int | None = Field(
        default=None, description="Index into the trainee action log to replace."
    )
    inject_action: TraineeAction | None = None
    replace_all_from: bool = Field(
        default=True, description="Drop every trainee action at or after fork_t."
    )
    follow_with_ghost: bool = Field(
        default=True,
        description=(
            "Let the counterfactual oracle take over at fork_t and append its "
            "actions to the fork log (the default counterfactual). Set false to "
            "re-simulate the trainee's own log unchanged."
        ),
    )


class ForkResult(VersionedModel):
    """The outcome of a counterfactual fork."""

    fork_t: float
    original_hash: str
    forked_hash: str
    diverged: bool
    original_score: float
    forked_score: float
    score_delta: float
    original_outcome: dict[str, Any] = Field(default_factory=dict)
    forked_outcome: dict[str, Any] = Field(default_factory=dict)
    explanation: str = ""
    ghost_actions: list[GhostAction] = Field(default_factory=list)
    newly_appended_actions: list[TraineeAction] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# NL builder
# --------------------------------------------------------------------------- #


class NlBuildResult(VersionedModel):
    """Outcome of the natural-language scenario builder."""

    scenario: Scenario | None
    path: Literal["llm", "rule_based", "llm_fallback_rule_based"]
    explanation: str
    attempts: int
    validation_errors: list[str] = Field(default_factory=list)
    llm_available: bool = False
    sanity_ok: bool = False
    sanity_message: str = ""


# --------------------------------------------------------------------------- #
# API envelopes
# --------------------------------------------------------------------------- #


class ApiError(BaseModel):
    """Uniform machine-readable error body."""

    detail: str
    code: str = "error"
    expected_schema_version: int = SCHEMA_VERSION
    received_schema_version: int | None = None


class OkResponse(BaseModel):
    """Simple acknowledgement."""

    ok: bool = True
    message: str = ""


Scenario.model_rebuild()
TerrainSpec.model_rebuild()

__all__ = [
    "SCHEMA_VERSION",
    "ActionType",
    "ApiError",
    "BearingPoint",
    "BehaviourKind",
    "BehaviourSpec",
    "BuildingSpec",
    "CivilSector",
    "CoachingPoint",
    "DegradationState",
    "DifficultyVector",
    "EffectorSpec",
    "EffectorView",
    "EntityClass",
    "EntityScore",
    "EnvSpec",
    "EventKind",
    "FormationKind",
    "ForkRequest",
    "ForkResult",
    "FrameSample",
    "GhostAction",
    "HabitExploit",
    "HabitFeatures",
    "LabelType",
    "NlBuildResult",
    "OkResponse",
    "RedBrainReport",
    "ResolvedDifficulty",
    "ReplayEntity",
    "ReplaySession",
    "Role",
    "RoeSpec",
    "RuleSetId",
    "Scenario",
    "ScenarioEvent",
    "ScoreBreakdown",
    "ScoreWeights",
    "SensorDegradation",
    "SensorHealthView",
    "SensorSpec",
    "SensorType",
    "SessionRecord",
    "SimEvent",
    "SiteSpec",
    "SkillVector",
    "StateFrame",
    "Tactic",
    "TerrainPreset",
    "TerrainSpec",
    "ThreatSpec",
    "TimeOfDay",
    "TrackView",
    "TraineeAction",
    "Vec3",
    "Verdict",
    "VerdictRecord",
    "VersionedModel",
]

# Silence unused-import linters for names kept for schema readability.
_ = (Annotated, NonNegativeFloat, NonNegativeInt, PositiveInt)
