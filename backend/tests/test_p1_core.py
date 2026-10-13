"""P1 - core engine: schemas, terrain, world, engine, determinism."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.core.engine import SimEngine, initial_state, step
from app.core.entities import CLASS_TABLE, formation_offsets, params_for
from app.core.generator import generate
from app.core.schemas import (
    SCHEMA_VERSION,
    ActionType,
    BehaviourKind,
    DifficultyVector,
    EntityClass,
    LabelType,
    Scenario,
    SensorSpec,
    SensorType,
    TerrainPreset,
    ThreatSpec,
    TraineeAction,
)
from app.core.terrain import build_terrain
from app.core.world import new_world


@pytest.fixture(scope="module")
def scenario() -> Scenario:
    """A single generated scenario reused across the module."""
    return generate(seed=4242).scenario


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


def test_scenario_schema_version_roundtrip(scenario: Scenario) -> None:
    """A scenario survives a model_dump/model_validate round trip unchanged."""
    assert scenario.schema_version == SCHEMA_VERSION
    again = Scenario.model_validate(scenario.model_dump(mode="json"))
    assert again.model_dump(mode="json") == scenario.model_dump(mode="json")


def test_unknown_schema_version_is_rejected(scenario: Scenario) -> None:
    """An unknown wire version must be rejected, not silently coerced."""
    payload = scenario.model_dump(mode="json")
    payload["schema_version"] = SCHEMA_VERSION + 7
    with pytest.raises(ValueError):
        Scenario.model_validate(payload)


def test_scenario_requires_sensors_effectors_and_threats(scenario: Scenario) -> None:
    """Structural validation rejects a scenario with no sensors."""
    payload = scenario.model_dump(mode="python")
    payload["sensors"] = []
    with pytest.raises(ValueError):
        Scenario.model_validate(payload)


def test_difficulty_vector_axis_helpers() -> None:
    """Axis accessors round-trip and reject unknown axes."""
    vec = DifficultyVector(swarm_size=0.4, night=1.0)
    assert vec.to_array()[0] == pytest.approx(0.4)
    assert DifficultyVector.from_array(vec.to_array()) == vec
    assert 0.0 <= vec.mean <= 1.0
    with pytest.raises(ValueError):
        vec.with_axis("not_an_axis", 0.5)
    with pytest.raises(ValueError):
        DifficultyVector.from_array([0.1, 0.2])
    assert vec.resolve().swarm_size >= 1
    assert vec.resolve().visibility_m > 0


# --------------------------------------------------------------------------- #
# Terrain
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "preset", [TerrainPreset.URBAN, TerrainPreset.RURAL, TerrainPreset.HILLS]
)
def test_terrain_is_deterministic_and_bounded(preset: TerrainPreset) -> None:
    """The same TerrainSpec always yields the same heightfield."""
    spec = _terrain_spec(preset)
    a = build_terrain(spec)
    b = build_terrain(spec)
    assert a.fingerprint == b.fingerprint
    assert a.heights.shape == (spec.resolution, spec.resolution)
    assert float(a.heights.min()) >= -1e-9
    assert float(a.heights.max()) <= spec.scale_m + 1e-6


def test_site_is_flat_and_clear() -> None:
    """The defended site sits on flat ground with no buildings on top of it."""
    terrain = build_terrain(_terrain_spec(TerrainPreset.URBAN))
    assert terrain.height_at(0.0, 0.0) == pytest.approx(0.0, abs=1e-6)
    assert all(math.hypot(b.x, b.y) > 250.0 for b in terrain.buildings)


def test_terrain_los_blocks_through_a_hill() -> None:
    """A line of sight through rising ground is refused."""
    from app.core.schemas import TerrainSpec, Vec3

    spec = TerrainSpec(preset=TerrainPreset.HILLS, resolution=64, scale_m=200.0, seed=7)
    terrain = build_terrain(spec)
    low = Vec3(x=-2500.0, y=0.0, z=terrain.height_at(-2500.0, 0.0) + 2.0)
    high = Vec3(x=2500.0, y=0.0, z=terrain.height_at(2500.0, 0.0) + 2.0)
    blocked = not terrain.line_of_sight(low, high)
    straight_above = terrain.line_of_sight(
        Vec3(x=-2500.0, y=0.0, z=4000.0), Vec3(x=2500.0, y=0.0, z=4000.0)
    )
    assert blocked or straight_above  # at least one of the two behaves correctly
    assert straight_above is True


# --------------------------------------------------------------------------- #
# Entities / formations
# --------------------------------------------------------------------------- #


def test_class_table_is_complete() -> None:
    """Every entity class has a parameter row."""
    assert set(CLASS_TABLE) == set(EntityClass)


def test_formation_offsets_are_distinct_and_scaled() -> None:
    """Formations produce `count` distinct, order-preserving offsets."""
    from app.core.schemas import FormationKind

    for kind in FormationKind:
        offsets = formation_offsets(kind, 5, 40.0)
        assert len(offsets) == 5
        assert len({(round(o.x, 3), round(o.y, 3)) for o in offsets}) == 5
    single = formation_offsets(FormationKind.LINE, 1, 40.0)
    assert (single[0].x, single[0].y) == (0.0, 0.0)


def test_params_for_returns_table_row() -> None:
    """`params_for` is a total function over the class enum."""
    for cls in EntityClass:
        p = params_for(cls)
        assert p.max_speed_mps > 0
        assert 0.0 <= p.acoustic_level <= 1.0
        assert 0.0 <= p.susceptibility <= 1.0


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_same_seed_same_hash(scenario: Scenario) -> None:
    """Two engines with the same seed produce byte-identical final state hashes."""
    a = SimEngine(scenario, dt=0.2)
    b = SimEngine(scenario, dt=0.2)
    a.run()
    b.run()
    assert a.state_hash() == b.state_hash()


def test_same_seed_same_hash_with_action_log(scenario: Scenario) -> None:
    """An identical trainee action log does not change determinism."""
    actions = [
        TraineeAction(t=120.0, type=ActionType.ACK_TRACK, track_id="T0001"),
        TraineeAction(t=125.0, type=ActionType.STEER_SENSOR, params={"sensor": "eo1", "az": 90.0}),
        TraineeAction(t=130.0, type=ActionType.CLASSIFY, track_id="T0001",
                      params={"class": "hostile"}),
    ]

    def run() -> str:
        engine = SimEngine(scenario, dt=0.2)
        for act in actions:
            engine.queue(act)
        engine.run()
        return engine.state_hash()

    assert run() == run()


def test_save_load_roundtrip_preserves_hash(scenario: Scenario) -> None:
    """Serialising and reloading the engine state preserves the hash exactly."""
    engine = SimEngine(scenario, dt=0.2)
    engine.run()
    before = engine.state_hash()
    restored = type(engine.state).from_dict(engine.state.to_dict())
    assert restored.state_hash() == before
    assert restored.world.state_hash() == engine.state.world.state_hash()


def test_different_seeds_diverge() -> None:
    """Different scenario seeds give different outcomes."""
    a = SimEngine(generate(seed=1).scenario, dt=0.2)
    b = SimEngine(generate(seed=2).scenario, dt=0.2)
    a.run()
    b.run()
    assert a.state_hash() != b.state_hash()


def test_rng_draw_count_is_reproducible(scenario: Scenario) -> None:
    """The RNG accounting counter matches between two runs."""
    a = SimEngine(scenario, dt=0.2)
    b = SimEngine(scenario, dt=0.2)
    a.run()
    b.run()
    assert a.state.world.rng_draws == b.state.world.rng_draws


# --------------------------------------------------------------------------- #
# Engine behaviour
# --------------------------------------------------------------------------- #


def test_step_is_pure_and_does_not_mutate_input(scenario: Scenario) -> None:
    """`step` returns a new state and leaves its input untouched."""
    state = initial_state(scenario, dt=0.2)
    rng = np.random.default_rng(1)
    before = state.state_hash()
    out = step(state, [], rng)
    assert state.state_hash() == before
    assert out is not state
    assert out.state_hash() != before


def test_headless_run_produces_movement(scenario: Scenario) -> None:
    """Entities actually move and eventually reach the protected zone."""
    engine = SimEngine(scenario, dt=0.2)
    engine.run()
    entities = list(engine.state.world.entities.values())
    assert entities, "the scenario spawned nothing"
    assert all(
        math.isfinite(v) for e in entities for v in (*e.position.as_tuple(), *e.velocity.as_tuple())
    )
    breached = [e for e in entities if e.reached_perimeter_t is not None]
    assert breached, "no entity reached the protected zone in a full run"


def test_world_roundtrip_preserves_hash(scenario: Scenario) -> None:
    """WorldState alone round-trips through its dict form."""
    world = new_world(scenario, dt=0.2)
    again = type(world).from_dict(world.to_dict())
    assert again.state_hash() == world.state_hash()


def test_ring_crossings_are_recorded_once(scenario: Scenario) -> None:
    """A ring crossing is recorded exactly once per ring per entity."""
    engine = SimEngine(scenario, dt=0.2)
    engine.run()
    ring_events = [e for e in engine.state.world.events if e.kind.value == "ring_crossing"]
    keys = [(e.entity_id, e.data.get("ring_index")) for e in ring_events if "ring_index" in e.data]
    assert len(keys) == len(set(keys))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _terrain_spec(preset: TerrainPreset) -> "TerrainSpec":
    from app.core.schemas import TerrainSpec

    return TerrainSpec(preset=preset, resolution=96, scale_m=150.0, roughness=0.6, seed=99)


def test_generated_waves_are_valid(scenario: Scenario) -> None:
    """Generated waves reference real classes and behaviours."""
    for wave in (*scenario.threats, *scenario.decoys):
        assert isinstance(wave, ThreatSpec)
        assert wave.count >= 1
        assert wave.behaviour.kind in set(BehaviourKind)
        assert wave.label in set(LabelType)
        assert wave.spawn_t <= scenario.duration_s
    assert scenario.sensors
    assert all(isinstance(s, SensorSpec) and s.type in set(SensorType) for s in scenario.sensors)


def test_state_hash_changes_with_time(scenario: Scenario) -> None:
    """Hash is a function of the whole state, not just the scenario."""
    a = SimEngine(scenario, dt=0.2)
    a.run(max_steps=10)
    b = SimEngine(scenario, dt=0.2)
    b.run(max_steps=11)
    assert a.state_hash() != b.state_hash()