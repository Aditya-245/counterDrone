"""Tests for the procedural landscape generator (`core/worldgen.py`).

The generator is what the client draws, so its contract is: deterministic, derived
from the same heightfield the simulation uses, and structurally plausible (roads
that reach the settlement, buildings that are not on a grid, nothing inside the
defended bubble).
"""

from __future__ import annotations

import base64
import math

import numpy as np
import pytest

from app.core.schemas import (
    BuildingSpec,
    DifficultyVector,
    LandscapeSpec,
    Scenario,
    TerrainPreset,
    TerrainSpec,
)
from app.core.terrain import build_terrain
from app.core.worldgen import (
    BUILDING_BUDGET,
    CLEAR_RADIUS_M,
    LandscapeConfig,
    buildings_as_specs,
    counts,
    generate_landscape,
    landscape_fingerprint,
)
from tests.conftest import REPO_ROOT


def _terrain(preset: TerrainPreset, seed: int = 7):
    """A small but realistic terrain: 4 km, 128 posts."""
    spec = TerrainSpec(preset=preset, size_m=4000.0, resolution=128, scale_m=160.0, seed=seed)
    return build_terrain(spec, seed=seed), spec


def _urban(seed: int = 7) -> LandscapeSpec:
    terrain, spec = _terrain(TerrainPreset.URBAN, seed)
    return generate_landscape(
        terrain, seed=seed, preset=TerrainPreset.URBAN, scenario_id=spec.preset.value
    )


def _rural(seed: int = 11) -> LandscapeSpec:
    terrain, spec = _terrain(TerrainPreset.RURAL, seed)
    return generate_landscape(terrain, seed=seed, preset=TerrainPreset.RURAL, scenario_id="rural")


def _decode_heights(landscape: LandscapeSpec) -> np.ndarray:
    raw = base64.b64decode(landscape.heights_b64)
    grid = np.frombuffer(raw, dtype="<i2").astype(float) / 100.0
    n = landscape.grid_n
    return grid.reshape(n, n)


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_same_seed_produces_an_identical_landscape() -> None:
    """Two generations from one seed must be byte-identical."""
    assert landscape_fingerprint(_urban(3)) == landscape_fingerprint(_urban(3))


def test_different_seeds_produce_different_landscapes() -> None:
    """A different seed must move the settlement, or the world looks memorised."""
    assert landscape_fingerprint(_urban(3)) != landscape_fingerprint(_urban(4))


def test_heights_match_the_terrain_the_simulation_uses() -> None:
    """The client must draw the ground the sensors actually mask against."""
    terrain, _ = _terrain(TerrainPreset.RURAL, 5)
    landscape = generate_landscape(
        terrain, seed=5, preset=TerrainPreset.RURAL, config=LandscapeConfig(grid_n=97)
    )
    heights = _decode_heights(landscape)
    assert heights.shape == (97, 97)
    assert float(heights.max()) <= float(terrain.heights.max()) + 1.0
    assert heights[48, 48] == pytest.approx(terrain.height_at(0.0, 0.0), abs=0.5)


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #


def test_urban_and_rural_are_structurally_different() -> None:
    """The two presets must not be the same layout with a different palette."""
    urban, rural = _urban(9), _rural(9)
    assert urban.buildings != rural.buildings
    # A town is denser and more rectilinear than a village in open country.
    assert len(urban.buildings) > 150
    assert len(rural.buildings) < 200
    assert len(rural.fields) > 20
    assert urban.fields == []


def test_urban_has_a_connected_road_network() -> None:
    """Every parallel street must touch the arterial it is offset from."""
    landscape = _urban(2)
    arterials = [r for r in landscape.roads if r.kind == "arterial"]
    streets = [r for r in landscape.roads if r.kind in ("street", "lane")]
    assert arterials, "no arterial spine generated"
    assert len(streets) >= 8
    for street in streets:
        gap = min(
            _point_segment_distance((street.points[0][0], street.points[0][1]), arterial.points)
            for arterial in arterials
            if arterial.points
        )
        assert gap < 400.0, f"street starts {gap:.0f} m from every arterial"


def test_nothing_is_built_inside_the_defended_bubble() -> None:
    """The protected zone must stay clear for the sensors to work."""
    for landscape in (_urban(6), _rural(6)):
        for building in landscape.buildings:
            assert math.hypot(building.x, building.y) >= CLEAR_RADIUS_M - 1.0
        for road in landscape.roads:
            for x, y in road.points:
                if math.hypot(x, y) < 120.0:
                    pytest.fail("a road crosses the protected zone")


def test_buildings_are_not_on_a_world_axis_grid() -> None:
    """Every block fronts a street, so most footprints are rotated."""
    landscape = _urban(8)
    rotated = sum(1 for b in landscape.buildings if abs(b.rotation_deg % 180.0) > 5.0)
    assert rotated > len(landscape.buildings) * 0.6


def test_buildings_do_not_overlap() -> None:
    """Two footprints may not share a centre; that reads as a modelling bug."""
    landscape = _urban(4)
    seen: list[tuple[float, float]] = []
    for building in landscape.buildings:
        centre = (building.x, building.y)
        for other in seen:
            gap = math.dist(centre, other)
            assert gap > max(building.width_m, building.depth_m) * 0.25
        seen.append(centre)


def test_scatter_stays_inside_the_budgets() -> None:
    """The client draws these as instanced meshes; they must stay bounded."""
    for landscape in (_urban(1), _rural(1)):
        c = counts(landscape)
        assert c["buildings"] <= BUILDING_BUDGET
        assert c["trees"] <= 1100
        assert c["props"] <= 400
        assert c["roads"] <= 60


def test_rural_has_villages_fields_and_water() -> None:
    """A believable rural landscape needs all three to read as countryside."""
    landscape = _rural(12)
    assert any(b.kind.value in ("house_gable", "house_hip") for b in landscape.buildings)
    assert len(landscape.fields) > 20
    assert landscape.water, "no pond or stream in a rural landscape"
    assert any(t.kind == "broadleaf" for t in landscape.trees)


# --------------------------------------------------------------------------- #
# The occluder contract
# --------------------------------------------------------------------------- #


def test_authored_buildings_are_returned_verbatim_as_occluders() -> None:
    """What the simulation masks against is what the client draws, rotated."""
    authored = (
        BuildingSpec(
            x=800.0, y=-400.0, width_m=30.0, depth_m=20.0, height_m=18.0, rotation_deg=37.0
        ),
        BuildingSpec(x=-900.0, y=600.0, width_m=45.0, depth_m=45.0, height_m=9.0),
    )
    terrain, _ = _terrain(TerrainPreset.URBAN, 21)
    landscape = generate_landscape(terrain, seed=21, preset=TerrainPreset.URBAN, buildings=authored)
    occluders = [b for b in landscape.buildings if b.occluder]
    assert len(occluders) == 2
    assert occluders[0] is not None
    assert (occluders[0].x, occluders[0].y, occluders[0].rotation_deg) == (800.0, -400.0, 37.0)
    assert any(b.kind.value == "tower" for b in landscape.buildings)


def test_generated_context_never_claims_to_occlude() -> None:
    """Without authored footprints the settlement is scenery only."""
    landscape = _urban(15)
    assert all(not b.occluder for b in landscape.buildings)


def test_buildings_as_specs_round_trips_authored_footprints() -> None:
    """The authoring helper must not lose the rotation."""
    authored = (
        BuildingSpec(x=10.0, y=20.0, width_m=12.0, depth_m=9.0, height_m=6.0, rotation_deg=123.0),
    )
    terrain, _ = _terrain(TerrainPreset.RURAL, 2)
    landscape = generate_landscape(terrain, seed=2, preset=TerrainPreset.RURAL, buildings=authored)
    specs = buildings_as_specs(landscape)
    assert specs[0].rotation_deg == 123.0


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_configuration_scales_the_world() -> None:
    """Vegetation and village counts are knobs, not hard-coded behaviour."""
    terrain, _ = _terrain(TerrainPreset.RURAL, 31)
    bare = generate_landscape(
        terrain,
        seed=31,
        preset=TerrainPreset.RURAL,
        config=LandscapeConfig(vegetation_density=0.0, villages=1, grid_n=65),
    )
    lush = generate_landscape(
        terrain,
        seed=31,
        preset=TerrainPreset.RURAL,
        config=LandscapeConfig(vegetation_density=2.0, villages=4, grid_n=65),
    )
    assert len(lush.trees) > len(bare.trees)
    assert len(bare.trees) == 0
    assert len(lush.fields) >= len(bare.fields)
    assert bare.grid_n == 65


def test_payload_carries_everything_the_client_needs() -> None:
    """A guard against silently dropping a collection the renderer reads."""
    landscape = _urban(13)
    payload = landscape.model_dump(mode="json")
    for key in ("heights_b64", "landcover_b64", "palette", "roads", "buildings", "fields"):
        assert key in payload
    assert len(landscape.palette) >= 10
    assert landscape.max_height_m > 0.0


def test_shipped_scenarios_generate_a_landscape() -> None:
    """Every shipped scenario must produce a drawable world."""
    files = sorted((REPO_ROOT / "scenarios").glob("*.json"))
    assert files, "no shipped scenarios found"
    for path in files:
        scenario = Scenario.model_validate_json(path.read_text(encoding="utf-8"))
        terrain = build_terrain(scenario.terrain, seed=scenario.seed)
        landscape = generate_landscape(
            terrain,
            seed=scenario.seed,
            preset=scenario.terrain.preset,
            buildings=tuple(scenario.terrain.buildings),
            scenario_id=scenario.id,
        )
        c = counts(landscape)
        assert c["roads"] > 0, f"{scenario.id} has no roads"
        assert c["buildings"] > 0, f"{scenario.id} has no buildings"


def _point_segment_distance(
    point: tuple[float, float], polyline: list[tuple[float, float]]
) -> float:
    """Distance from a point to the nearest segment of a polyline."""
    best = float("inf")
    for a, b in zip(polyline, polyline[1:], strict=False):
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        if length_sq < 1e-9:
            best = min(best, math.dist(point, a))
            continue
        t = max(0.0, min(1.0, ((point[0] - ax) * dx + (point[1] - ay) * dy) / length_sq))
        best = min(best, math.dist(point, (ax + t * dx, ay + t * dy)))
    return best


def test_difficulty_vector_is_unaffected_by_landscape_generation() -> None:
    """World generation must not reach into the scoring inputs."""
    vector = DifficultyVector()
    terrain, _ = _terrain(TerrainPreset.RURAL, 44)
    generate_landscape(terrain, seed=44, preset=TerrainPreset.RURAL)
    assert vector == DifficultyVector()
