"""Procedural landscape generation: roads, blocks, settlements, fields, water.

The client used to fake the world with sine bumps and wireframe boxes while the
simulation reasoned about a real heightfield. This module closes that gap: it is
the single place that describes what a world looks like, and it reuses
`build_terrain`, so the ground the client draws is the ground the sensors mask
against.

`generate_landscape` is pure and deterministic - the same terrain, seed and
authored buildings always produce the same `LandscapeSpec` - so the client can
cache it and the tests can assert it.

Two layouts share one configuration:

* **urban** - arterial spines with parallel streets and cross streets, blocks
  filled with archetypes that *front* the street they belong to, pocket parks,
  parking, street trees and lamps;
* **rural** - a highway with lanes running to village clusters, a jittered field
  patchwork with hedgerows, farmsteads with sheds and silos, ponds and woodland.

Building footprints are real occluders: pass the scenario's `terrain.buildings` in
and those exact footprints come back (rotated as authored); otherwise settlement
footprints are generated for display and flagged `occluder=False`.
"""

from __future__ import annotations

import base64
import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.core.schemas import (
    BuildingInstance,
    BuildingKind,
    BuildingSpec,
    FieldPatch,
    LandscapeSpec,
    PropInstance,
    PropKind,
    RoadKind,
    RoadSpec,
    TerrainPreset,
    TreeInstance,
    TreeKind,
    WaterKind,
    WaterSpec,
)
from app.core.terrain import Terrain

__all__ = [
    "LANDCOVER_PALETTE",
    "LandscapeConfig",
    "buildings_as_specs",
    "counts",
    "generate_landscape",
    "landscape_fingerprint",
]

Point = tuple[float, float]

#: Land-cover indices; the palette travels with the payload so both agree.
GRASS, CROP_A, CROP_B, SOIL, URBAN, PARK, WATER, GRAVEL, FOREST, ASPHALT = range(10)
LANDCOVER_PALETTE: tuple[str, ...] = (
    "#5f7048",  # grass
    "#98964e",  # crop, mature
    "#ad975a",  # crop, ripening
    "#8a7052",  # bare soil / yard
    "#7c7a73",  # built-up ground between blocks
    "#5b7a4a",  # park
    "#31525c",  # water
    "#8f887c",  # gravel track
    "#48603e",  # woodland floor
    "#4f4f4b",  # asphalt
)

#: Nothing is built or planted inside this radius: the defended site.
CLEAR_RADIUS_M = 300.0
#: Road stations are sampled at this spacing so offsetting stays cheap.
STATION_M = 45.0
#: Scatter budgets. The client instancedraws these in one call each, so the limit
#: is about payload size and silhouette density, not draw calls.
TREE_BUDGET = 1100
PROP_BUDGET = 400
#: Buildings drawn per landscape before the generator stops: a small town reads
#: better (and renders cheaper) than a city that covers the whole map.
BUILDING_BUDGET = 520
#: Road polylines per landscape, so the payload stays a sane size.
ROAD_BUDGET = 60
#: Blocks are only laid out this far from the defended site, so the settlement
#: thins into open ground instead of carpeting the terrain.
TOWN_RADIUS_M = 1_500.0
#: Nominal city-block edge length, which sets how many blocks fit along a street.
BLOCK_M = 165.0
#: Buildings shorter than this are drawn but never treated as occluders by the
#: renderer; only footprints carried by the scenario block the simulation.
BLOCKING_MIN_M = 4.0


@dataclass(frozen=True, slots=True)
class LandscapeConfig:
    """Tunables for `generate_landscape`; every value is a multiplier or a count."""

    building_density: float = 1.0
    vegetation_density: float = 1.0
    terrain_variation: float = 1.0
    villages: int = 3
    grid_n: int = 97


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #


def _rotate(p: Point, deg: float) -> Point:
    """Rotate a point about the origin."""
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def _add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def _scale(a: Point, k: float) -> Point:
    return (a[0] * k, a[1] * k)


def _norm(a: Point) -> Point:
    length = math.hypot(a[0], a[1])
    return (a[0] / length, a[1] / length) if length > 1e-9 else (0.0, 0.0)


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _lerp(a: Point, b: Point, t: float) -> Point:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _bearing(a: Point, b: Point) -> float:
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def _polygon_area(poly: Sequence[Point]) -> float:
    total = 0.0
    for a, b in zip(poly, [*poly[1:], poly[0]], strict=False):
        total += a[0] * b[1] - b[0] * a[1]
    return abs(total) / 2.0


def _sample_polyline(points: Sequence[Point], step: float = STATION_M) -> list[Point]:
    """Resample a polyline into evenly spaced stations."""
    if len(points) < 2:
        return list(points)
    out: list[Point] = [points[0]]
    carry = 0.0
    for a, b in zip(points, points[1:], strict=False):
        seg = _dist(a, b)
        if seg < 1e-6:
            continue
        travelled = carry
        while travelled + step <= seg:
            travelled += step
            out.append(_lerp(a, b, travelled / seg))
        carry = travelled - seg
    if _dist(out[-1], points[-1]) > step * 0.4:
        out.append(points[-1])
    return out


def _offset_polyline(points: Sequence[Point], distance: float) -> list[Point]:
    """Offset a polyline sideways; positive is to the left of travel."""
    if len(points) < 2:
        return list(points)
    out: list[Point] = []
    for i, p in enumerate(points):
        if i == 0:
            direction = _norm(_sub(points[1], p))
        elif i == len(points) - 1:
            direction = _norm(_sub(p, points[i - 1]))
        else:
            direction = _norm(_sub(points[i + 1], points[i - 1]))
        out.append(_add(p, _scale((-direction[1], direction[0]), distance)))
    return out


def _nearest_on_polyline(p: Point, poly: Sequence[Point]) -> tuple[Point, float]:
    """Closest point on a polyline and its distance to `p`."""
    best = poly[0]
    best_d = float("inf")
    for a, b in zip(poly, poly[1:], strict=False):
        ab = _sub(b, a)
        length_sq = ab[0] * ab[0] + ab[1] * ab[1]
        if length_sq < 1e-9:
            candidates: tuple[float, ...] = (0.0,)
        else:
            candidates = (
                max(0.0, min(1.0, ((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1]) / length_sq)),
            )
        for t in candidates:
            candidate = _lerp(a, b, t)
            d = _dist(p, candidate)
            if d < best_d:
                best_d = d
                best = candidate
    return best, best_d


def _clip_to_map(points: Sequence[Point], half: float) -> list[Point]:
    """Keep the longest contiguous run of stations inside the square extent."""
    best: list[Point] = []
    run: list[Point] = []
    for p in points:
        if abs(p[0]) <= half and abs(p[1]) <= half:
            run.append(p)
            if len(run) > len(best):
                best = list(run)
        else:
            run = []
    return best


def _clip_to_bubble(points: Sequence[Point], radius: float = CLEAR_RADIUS_M) -> list[Point]:
    """Keep the longest run that stays outside the defended bubble.

    Roads must not run through the protected zone: a carriageway there would be
    both a visual lie and an obstacle the simulation knows nothing about.
    """
    best: list[Point] = []
    run: list[Point] = []
    for p in points:
        if math.hypot(p[0], p[1]) >= radius:
            run.append(p)
            if len(run) > len(best):
                best = list(run)
        else:
            run = []
    return best


def _catmull_rom(points: Sequence[Point], steps: int = 12) -> list[Point]:
    """Smooth a control polyline so roads curve instead of kinking."""
    if len(points) < 3:
        return list(points)
    out: list[Point] = []
    for i in range(len(points) - 1):
        p0 = points[max(i - 1, 0)]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[min(i + 2, len(points) - 1)]
        for s in range(steps):
            t = s / steps
            t2, t3 = t * t, t * t * t
            x = 0.5 * (
                (2 * p1[0])
                + (-p0[0] + p2[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                (2 * p1[1])
                + (-p0[1] + p2[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
            )
            out.append((x, y))
    out.append(points[-1])
    return out


def _wander(
    rng: np.random.Generator, start: Point, end: Point, bends: int, amp: float
) -> list[Point]:
    """A road that walks from `start` to `end` with seeded lateral wander."""
    direction = _norm(_sub(end, start))
    normal = (-direction[1], direction[0])
    controls: list[Point] = [start]
    for i in range(1, bends + 1):
        t = i / (bends + 1)
        controls.append(
            _add(
                _lerp(start, end, t),
                _scale(normal, float(rng.uniform(-amp, amp)) * math.sin(math.pi * t)),
            )
        )
    controls.append(end)
    return _catmull_rom(controls)


def _point_in_polygon(p: Point, poly: Sequence[Point]) -> bool:
    """Crossing-number point-in-polygon test."""
    inside = False
    for a, b in zip(poly, [*poly[1:], poly[0]], strict=False):
        if (a[1] > p[1]) != (b[1] > p[1]):
            t = (p[1] - a[1]) / (b[1] - a[1])
            if p[0] < a[0] + t * (b[0] - a[0]):
                inside = not inside
    return inside


# --------------------------------------------------------------------------- #
# Land-cover raster
# --------------------------------------------------------------------------- #


class _Raster:
    """A square uint8 land-cover grid in world metres."""

    def __init__(self, half: float, n: int) -> None:
        self.half = half
        self.n = n
        self.step = (2.0 * half) / max(1, n - 1)
        self.grid = np.full((n, n), GRASS, dtype=np.uint8)

    def _index_range(self, centre: float, radius: float) -> tuple[int, int]:
        i0 = max(0, int((centre - radius + self.half) / self.step))
        i1 = min(self.n - 1, int((centre + radius + self.half) / self.step) + 1)
        return i0, i1

    def paint_polygon(self, index: int, poly: Sequence[Point]) -> None:
        """Fill a polygon with a land-cover index.

        Quads (fields and city blocks, the overwhelming majority) take a
        vectorised half-plane test; anything rarer falls back to per-cell crossing
        tests, which is fine because ponds and parks are few.
        """
        if len(poly) < 3:
            return
        if len(poly) == 4:
            self._paint_quad(index, poly)
            return
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        i0, i1 = self._index_range(min(xs), max(xs))
        j0, j1 = self._index_range(min(ys), max(ys))
        for j in range(j0, j1 + 1):
            y = -self.half + j * self.step
            for i in range(i0, i1 + 1):
                if _point_in_polygon((-self.half + i * self.step, y), poly):
                    self.grid[j, i] = index

    def _paint_quad(self, index: int, quad: Sequence[Point]) -> None:
        """Vectorised convex-quad fill."""
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        i0, i1 = self._index_range(min(xs), max(xs))
        j0, j1 = self._index_range(min(ys), max(ys))
        if i1 <= i0 or j1 <= j0:
            return
        gx, gy = np.meshgrid(
            -self.half + np.arange(i0, i1 + 1) * self.step,
            -self.half + np.arange(j0, j1 + 1) * self.step,
        )
        cx = sum(p[0] for p in quad) / 4.0
        cy = sum(p[1] for p in quad) / 4.0
        inside = np.ones(gx.shape, dtype=bool)
        for a_pt, b_pt in zip(quad, [*quad[1:], quad[0]], strict=False):
            ex, ey = b_pt[0] - a_pt[0], b_pt[1] - a_pt[1]
            cross = ex * (gy - a_pt[1]) - ey * (gx - a_pt[0])
            keep_positive = ex * (cy - a_pt[1]) - ey * (cx - a_pt[0]) >= 0.0
            inside &= cross >= 0.0 if keep_positive else cross <= 0.0
        self.grid[j0 : j1 + 1, i0 : i1 + 1][inside] = index

    def paint_disc(self, index: int, centre: Point, radius: float) -> None:
        """Fill a disc: roads, yards and ponds read as smooth patches."""
        i0, i1 = self._index_range(centre[0], radius)
        j0, j1 = self._index_range(centre[1], radius)
        for j in range(j0, j1 + 1):
            y = -self.half + j * self.step
            for i in range(i0, i1 + 1):
                if math.hypot(-self.half + i * self.step - centre[0], y - centre[1]) <= radius:
                    self.grid[j, i] = index

    def paint_corridor(self, index: int, points: Sequence[Point], width: float) -> None:
        """Paint the ground a road runs over."""
        radius = width / 2 + self.step
        for a, b in zip(points, points[1:], strict=False):
            steps = max(1, int(_dist(a, b) / max(1.0, self.step * 0.75)))
            for s in range(steps + 1):
                self.paint_disc(index, _lerp(a, b, s / steps), radius)


# --------------------------------------------------------------------------- #
# Accumulator
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Accumulator:
    """Everything the two generators accumulate, in one place."""

    roads: list[RoadSpec]
    buildings: list[BuildingInstance]
    fields: list[FieldPatch]
    trees: list[TreeInstance]
    props: list[PropInstance]
    water: list[WaterSpec]
    vegetation: float = 1.0

    @classmethod
    def empty(cls, vegetation: float = 1.0) -> _Accumulator:
        return cls([], [], [], [], [], [], vegetation)

    def add_tree(self, tree: TreeInstance) -> None:
        """Append a tree while the scatter budget and the density knob allow it."""
        if self.vegetation <= 0.0:
            return
        if self.vegetation < 1.0 and float(rng_unit(self.trees)) > self.vegetation:
            return
        if len(self.trees) < TREE_BUDGET:
            self.trees.append(tree)

    def add_prop(self, prop: PropInstance) -> None:
        """Append a prop while the scatter budget lasts."""
        if len(self.props) < PROP_BUDGET:
            self.props.append(prop)


def _road_width(kind: RoadKind) -> float:
    return {
        RoadKind.ARTERIAL: 15.0,
        RoadKind.STREET: 9.5,
        RoadKind.LANE: 6.5,
        RoadKind.TRACK: 4.5,
    }[kind]


def rng_unit(placed: Sequence[TreeInstance]) -> float:
    """A deterministic 0..1 value from the scatter already placed.

    Thinning has to be reproducible like everything else here, and drawing from
    the shared generator would make the *number* of trees change the layout.
    """
    if not placed:
        return 0.5
    last = placed[-1]
    return ((int(abs(last.x) * 7) * 31 + int(abs(last.y) * 11) * 17) % 1000) / 1000.0


def _clear(p: Point, extra: float = 0.0) -> bool:
    """True when a position is outside the defended-site bubble."""
    return math.hypot(p[0], p[1]) >= CLEAR_RADIUS_M + extra


def _place(
    acc: _Accumulator,
    centre: Point,
    bearing: float,
    width: float,
    depth: float,
    height: float,
    kind: BuildingKind,
    occluder: bool,
) -> bool:
    """Append one building if it is outside the site and clear of its neighbours."""
    if not _clear(centre) or len(acc.buildings) >= BUILDING_BUDGET:
        return False
    radius = max(width, depth) * 0.45
    if any(
        _dist(centre, (b.x, b.y)) < radius + max(b.width_m, b.depth_m) * 0.5 for b in acc.buildings
    ):
        return False
    acc.buildings.append(
        BuildingInstance(
            x=round(centre[0], 2),
            y=round(centre[1], 2),
            width_m=round(width, 2),
            depth_m=round(depth, 2),
            height_m=round(height, 2),
            rotation_deg=round(bearing % 360.0, 1),
            kind=kind,
            occluder=occluder,
        )
    )
    return True


# --------------------------------------------------------------------------- #
# Urban layout
# --------------------------------------------------------------------------- #


def _zone_for(distance_m: float, rng: np.random.Generator) -> str:
    """Inner blocks go commercial, the middle is residential, the rim industrial."""
    roll = float(rng.random())
    if distance_m < 900.0:
        return "commercial" if roll < 0.78 else "residential"
    if distance_m < 2200.0:
        return "residential" if roll < 0.72 else "commercial"
    return "industrial" if roll < 0.62 else "residential"


def _building_for_zone(
    zone: str, rng: np.random.Generator
) -> tuple[BuildingKind, float, float, float] | None:
    """Pick an archetype and footprint (width, depth, height) for one building."""
    roll = float(rng.random())
    if zone == "commercial":
        if roll < 0.26:
            return (
                BuildingKind.TOWER,
                float(rng.uniform(22, 40)),
                26.0,
                float(rng.uniform(34, 66)),
            )
        if roll < 0.60:
            return (
                BuildingKind.APARTMENT,
                float(rng.uniform(16, 30)),
                18.0,
                float(rng.uniform(16, 30)),
            )
        if roll < 0.92:
            return (BuildingKind.SHOP, float(rng.uniform(14, 26)), 16.0, float(rng.uniform(7, 14)))
        return (
            BuildingKind.HOUSE_FLAT,
            float(rng.uniform(14, 22)),
            15.0,
            float(rng.uniform(7, 11)),
        )
    if zone == "residential":
        if roll < 0.34:
            return (
                BuildingKind.HOUSE_GABLE,
                float(rng.uniform(9, 14)),
                12.0,
                float(rng.uniform(5.5, 8.5)),
            )
        if roll < 0.62:
            return (
                BuildingKind.HOUSE_HIP,
                float(rng.uniform(11, 17)),
                14.0,
                float(rng.uniform(6, 9)),
            )
        if roll < 0.86:
            return (
                BuildingKind.HOUSE_FLAT,
                float(rng.uniform(12, 18)),
                14.0,
                float(rng.uniform(7, 11)),
            )
        return (
            BuildingKind.APARTMENT,
            float(rng.uniform(18, 26)),
            16.0,
            float(rng.uniform(14, 22)),
        )
    if roll < 0.62:
        return (BuildingKind.WAREHOUSE, float(rng.uniform(30, 60)), 34.0, float(rng.uniform(9, 14)))
    if roll < 0.85:
        return (BuildingKind.SHED, float(rng.uniform(14, 26)), 18.0, float(rng.uniform(5, 8)))
    return None


def _fill_block(
    acc: _Accumulator,
    front_a: Point,
    front_b: Point,
    rear_a: Point,
    rear_b: Point,
    rng: np.random.Generator,
    occluder: bool = False,
) -> None:
    """Line a block's street-facing edges with buildings that face the street."""
    bearing = _bearing(front_a, front_b)
    normal = (-math.sin(math.radians(bearing)), math.cos(math.radians(bearing)))
    for a_pt, b_pt, sign in ((front_a, front_b, 1.0), (rear_a, rear_b, -1.0)):
        span = _dist(a_pt, b_pt)
        if span < 30.0:
            continue
        count = max(2, min(int(span / 26.0), 8))
        for i in range(count):
            mid = _lerp(a_pt, b_pt, (i + 0.5) / count)
            choice = _building_for_zone(_zone_for(math.hypot(*mid), rng), rng)
            if choice is None:
                continue
            kind, width, depth, height = choice
            # Push the building back from the street it fronts.
            centre = _add(mid, _scale(normal, sign * max(14.0, depth * 0.5 + 8.0)))
            _place(acc, centre, bearing, width, depth, height, kind, occluder)


def _park(
    acc: _Accumulator, raster: _Raster, polygon: Sequence[Point], rng: np.random.Generator
) -> None:
    """A pocket park or car park instead of a built-up block."""
    if _polygon_area(polygon) < 900.0:
        return
    centre = _lerp(polygon[0], polygon[2], 0.5)
    if float(rng.random()) < 0.4:
        raster.paint_polygon(GRAVEL, polygon)
        for _ in range(int(4 + rng.random() * 6)):
            spot = _lerp(
                _lerp(polygon[0], polygon[1], float(rng.random())),
                _lerp(polygon[3], polygon[2], float(rng.random())),
                0.5,
            )
            if _clear(spot, 12.0):
                acc.add_prop(
                    PropInstance(
                        x=round(spot[0], 1),
                        y=round(spot[1], 1),
                        rotation_deg=round(float(rng.uniform(0, 180)), 1),
                        kind=PropKind.VEHICLE,
                    )
                )
        return
    raster.paint_polygon(PARK, polygon)
    radius = math.sqrt(max(1.0, _polygon_area(polygon))) * 0.35
    for _ in range(int(10 + rng.random() * 20)):
        spot = (
            centre[0] + float(rng.normal(0, radius)),
            centre[1] + float(rng.normal(0, radius)),
        )
        if _clear(spot, 8.0) and _point_in_polygon(spot, polygon):
            acc.add_tree(
                TreeInstance(
                    x=round(spot[0], 1),
                    y=round(spot[1], 1),
                    height_m=round(float(rng.uniform(6, 13)), 1),
                    radius_m=round(float(rng.uniform(2.4, 4.6)), 1),
                    kind=TreeKind.BROADLEAF,
                )
            )


def _street_furniture(
    rng: np.random.Generator, acc: _Accumulator, line: Sequence[Point], width_m: float
) -> None:
    """Street trees and lamps along one street, alternating sides."""
    step = max(1, int(float(rng.uniform(34, 50)) / STATION_M))
    for i in range(0, len(line) - 1, step):
        station = line[i]
        direction = _norm(_sub(line[i + 1], station))
        normal = (-direction[1], direction[0])
        side = 1.0 if (i // step) % 2 == 0 else -1.0
        kerb = _add(station, _scale(normal, side * (width_m / 2 + 4.0)))
        if not _clear(kerb, 6.0):
            continue
        roll = float(rng.random())
        if roll < 0.58:
            acc.add_tree(
                TreeInstance(
                    x=round(kerb[0], 1),
                    y=round(kerb[1], 1),
                    height_m=round(float(rng.uniform(5, 10)), 1),
                    radius_m=round(float(rng.uniform(2.0, 3.8)), 1),
                    kind=TreeKind.BROADLEAF,
                )
            )
        elif roll < 0.85:
            acc.add_prop(
                PropInstance(
                    x=round(kerb[0], 1),
                    y=round(kerb[1], 1),
                    kind=PropKind.LAMP,
                    length_m=6.0,
                )
            )


def _installation(rng: np.random.Generator, acc: _Accumulator, raster: _Raster) -> None:
    """A small base camp in the gap between the defended bubble and the settlement.

    Without it the middle of every map is an empty disc, which reads as unfinished
    rather than as a protected site. These are context, so they never claim to be
    occluders.
    """
    arc = float(rng.uniform(0.0, 2.0 * math.pi))
    for _ in range(6 + int(rng.random() * 4)):
        angle = arc + float(rng.uniform(-1.1, 1.1))
        radius = float(rng.uniform(340.0, 540.0))
        centre = (math.cos(angle) * radius, math.sin(angle) * radius)
        kind = BuildingKind.SHED if rng.random() < 0.7 else BuildingKind.HOUSE_FLAT
        _place(
            acc,
            centre,
            angle + math.pi / 2 + float(rng.uniform(-0.4, 0.4)),
            float(rng.uniform(6.0, 13.0)),
            float(rng.uniform(8.0, 15.0)),
            float(rng.uniform(3.2, 6.0)),
            kind,
            False,
        )
    for _ in range(int(34 + rng.random() * 30)):
        angle = arc + float(rng.uniform(-1.4, 1.4))
        radius = float(rng.uniform(310.0, 600.0))
        centre = (math.cos(angle) * radius, math.sin(angle) * radius)
        acc.add_tree(
            TreeInstance(
                x=round(centre[0], 1),
                y=round(centre[1], 1),
                height_m=round(float(rng.uniform(6, 14)), 1),
                radius_m=round(float(rng.uniform(2.6, 4.6)), 1),
                kind=TreeKind.BROADLEAF,
            )
        )
    for _ in range(int(16 + rng.random() * 14)):
        angle = arc + float(rng.uniform(-1.2, 1.2))
        radius = float(rng.uniform(330.0, 520.0))
        centre = (math.cos(angle) * radius, math.sin(angle) * radius)
        acc.add_prop(
            PropInstance(
                x=round(centre[0], 1),
                y=round(centre[1], 1),
                rotation_deg=round(angle * 57.3 + 90.0, 1),
                length_m=round(float(rng.uniform(6, 18)), 1),
                kind=PropKind.FENCE if rng.random() < 0.6 else PropKind.HEDGE,
            )
        )
    for _ in range(3):
        angle = arc + float(rng.uniform(-1.0, 1.0))
        radius = float(rng.uniform(340.0, 460.0))
        raster.paint_disc(GRAVEL, (math.cos(angle) * radius, math.sin(angle) * radius), 26.0)


def _urban(
    rng: np.random.Generator,
    terrain: Terrain,
    acc: _Accumulator,
    raster: _Raster,
) -> None:
    """Arterial spines, parallel streets, cross streets and the blocks between."""
    half = terrain.half_extent_m
    spines: list[list[Point]] = []
    for index in range(2):
        angle = math.radians(20.0 + 90.0 * index + float(rng.uniform(-14, 14)))
        direction = (math.cos(angle), math.sin(angle))
        normal = (-direction[1], direction[0])
        offset = float(rng.uniform(-700, 700))
        a = _add(_scale(direction, -half * 1.3), _scale(normal, offset))
        b = _add(
            _scale(direction, half * 1.3),
            _scale(normal, offset + float(rng.uniform(-300, 300))),
        )
        line = _clip_to_bubble(
            _clip_to_map(_sample_polyline(_wander(rng, a, b, 4, 210.0), 60.0), half)
        )
        if len(line) < 4:
            continue
        spines.append(line)
        acc.roads.append(
            RoadSpec(
                kind=RoadKind.ARTERIAL,
                width_m=_road_width(RoadKind.ARTERIAL),
                markings=True,
                points=[(round(x, 1), round(y, 1)) for x, y in line],
            )
        )
        raster.paint_corridor(GRAVEL, line, _road_width(RoadKind.ARTERIAL) + 9.0)
        raster.paint_corridor(ASPHALT, line, _road_width(RoadKind.ARTERIAL) + 1.0)

    for spine in spines:
        for side in (1.0, -1.0):
            for base_offset in (85.0, 185.0, 300.0):
                offset = side * (base_offset + float(rng.uniform(-14, 14)))
                if base_offset <= 85.0 and float(rng.random()) > 0.45:
                    continue  # thin out the innermost rows so density falls off
                line = _clip_to_map(_sample_polyline(_offset_polyline(spine, offset), 55.0), half)
                if len(line) < 4:
                    continue
                kind = RoadKind.STREET if base_offset < 250 else RoadKind.LANE
                width = _road_width(kind)
                acc.roads.append(
                    RoadSpec(
                        kind=kind,
                        width_m=width,
                        points=[(round(x, 1), round(y, 1)) for x, y in line],
                    )
                )
                raster.paint_corridor(ASPHALT, line, _road_width(kind) + 1.0)
                _street_furniture(rng, acc, line, width)

                rear = _clip_to_map(
                    _sample_polyline(_offset_polyline(spine, offset * 0.45), 60.0),
                    half,
                )
                # Roads are presentation, so they are always generated; blocks are
                # only laid out when this landscape is also generating the buildings.
                _blocks_between(acc, raster, rng, line, rear)

                if len(acc.buildings) >= BUILDING_BUDGET or len(acc.roads) >= ROAD_BUDGET:
                    break
                # Cross streets back to the spine keep the network connected.
                step = max(1, int(float(rng.uniform(130, 200)) / 55.0))
                for i in range(0, len(line), step):
                    station = line[i]
                    if math.hypot(*station) > TOWN_RADIUS_M:
                        continue
                    target, gap = _nearest_on_polyline(station, spine)
                    if gap < 60.0 or not _clear(station, 40.0):
                        continue
                    acc.roads.append(
                        RoadSpec(
                            kind=RoadKind.LANE,
                            width_m=_road_width(RoadKind.LANE),
                            points=[
                                (round(station[0], 1), round(station[1], 1)),
                                (round(target[0], 1), round(target[1], 1)),
                            ],
                        )
                    )
                    raster.paint_corridor(
                        ASPHALT, [station, target], _road_width(RoadKind.LANE) + 1.0
                    )


def _blocks_between(
    acc: _Accumulator,
    raster: _Raster,
    rng: np.random.Generator,
    front: Sequence[Point],
    rear: Sequence[Point],
) -> None:
    """Fill the band between two roughly parallel streets with city blocks."""
    if len(front) < 3 or len(rear) < 3:
        return
    front_stations = _sample_polyline(front, BLOCK_M)
    rear_stations = _sample_polyline(rear, BLOCK_M)
    count = min(len(front_stations), len(rear_stations))
    for i in range(count - 1):
        if len(acc.buildings) >= BUILDING_BUDGET:
            return
        quad = [front_stations[i], front_stations[i + 1], rear_stations[i + 1], rear_stations[i]]
        if _polygon_area(quad) < 1_400.0:
            continue
        if math.hypot(*_lerp(quad[0], quad[2], 0.5)) > TOWN_RADIUS_M:
            continue
        if float(rng.random()) < 0.13:
            _park(acc, raster, quad, rng)
            continue
        raster.paint_polygon(URBAN, quad)
        _fill_block(acc, quad[0], quad[1], quad[2], quad[3], rng, False)
        _fill_block(acc, quad[3], quad[2], quad[1], quad[0], rng, False)


# --------------------------------------------------------------------------- #
# Rural layout
# --------------------------------------------------------------------------- #


def _fields(
    rng: np.random.Generator,
    acc: _Accumulator,
    raster: _Raster,
    half: float,
    avoid: Sequence[Point],
) -> None:
    """Field polygons in two rotated, staggered lattices.

    Field *outlines* are sent to the client, which extrudes the hedgerows from
    them; only the occasional hedgerow tree becomes an instance, so a patchwork of
    several hundred fields stays a few hundred objects instead of tens of
    thousands.
    """
    crops = (CROP_A, CROP_B, SOIL, GRASS)
    for _frame in range(2):
        bearing = float(rng.uniform(0, 180))
        cell = float(rng.uniform(300, 470))
        origin = float(rng.uniform(-half * 0.25, half * 0.25))
        span = int(half / cell) + 2
        lattice: list[list[Point]] = []
        for row in range(-span, span + 1):
            stagger = 0.5 * cell if row % 2 else 0.0
            lattice.append(
                [
                    _rotate(
                        (
                            origin + col * cell + stagger + float(rng.uniform(-0.18, 0.18)) * cell,
                            row * cell + float(rng.uniform(-0.18, 0.18)) * cell,
                        ),
                        bearing,
                    )
                    for col in range(-span, span + 1)
                ]
            )
        for row in range(len(lattice) - 1):
            for col in range(len(lattice[0]) - 1):
                quad = [
                    lattice[row][col],
                    lattice[row][col + 1],
                    lattice[row + 1][col + 1],
                    lattice[row + 1][col],
                ]
                if any(abs(p[0]) > half or abs(p[1]) > half for p in quad):
                    continue
                if _polygon_area(quad) < 8_000.0:
                    continue
                centre = _lerp(quad[0], quad[2], 0.5)
                if any(_dist(centre, v) < 340.0 for v in avoid):
                    continue
                index = int(crops[int(rng.integers(0, len(crops)))])
                raster.paint_polygon(index, quad)
                rounded = [(round(p[0], 1), round(p[1], 1)) for p in quad]
                acc.fields.append(FieldPatch(index=index, points=rounded))
                if len(acc.trees) < TREE_BUDGET:
                    _hedgerow_trees(acc, quad, rng)


def _hedgerow_trees(acc: _Accumulator, quad: Sequence[Point], rng: np.random.Generator) -> None:
    """The odd tree standing in a hedgerow, spaced well apart."""
    for a, b in zip(quad, [*quad[1:], quad[0]], strict=False):
        steps = max(1, int(_dist(a, b) / 85.0))
        for s in range(steps):
            if float(rng.random()) > 0.16:
                continue
            p = _lerp(a, b, s / steps)
            if math.hypot(*p) < CLEAR_RADIUS_M or len(acc.trees) >= TREE_BUDGET:
                continue
            acc.add_tree(
                TreeInstance(
                    x=round(p[0], 1),
                    y=round(p[1], 1),
                    height_m=round(float(rng.uniform(8, 14)), 1),
                    radius_m=round(float(rng.uniform(2.8, 4.4)), 1),
                    kind=TreeKind.BROADLEAF,
                )
            )


def _village(
    rng: np.random.Generator,
    acc: _Accumulator,
    raster: _Raster,
    centre: Point,
    lane: Sequence[Point],
) -> None:
    """Houses, sheds, walls and trees along one village lane, both sides."""
    raster.paint_disc(URBAN, centre, 70.0)
    start, end = lane[0], lane[-1]
    direction = _norm(_sub(end, start))
    normal = (-direction[1], direction[0])
    bearing = _bearing(start, end)
    span = _dist(start, end)
    cursor = 0.0
    while cursor < span:
        width = float(rng.uniform(9, 15))
        mid = _lerp(start, end, (cursor + width / 2) / max(1.0, span))
        for side in (1.0, -1.0):
            base = _add(mid, _scale(normal, side * 16.0))
            roll = float(rng.random())
            if roll < 0.7:
                _place(
                    acc,
                    base,
                    bearing,
                    float(rng.uniform(8, 13)),
                    float(rng.uniform(9, 14)),
                    float(rng.uniform(4.5, 8.0)),
                    BuildingKind.HOUSE_GABLE if roll < 0.36 else BuildingKind.HOUSE_HIP,
                    True,
                )
            elif roll < 0.86:
                _place(
                    acc,
                    base,
                    bearing,
                    float(rng.uniform(6, 10)),
                    float(rng.uniform(7, 11)),
                    float(rng.uniform(3.0, 4.5)),
                    BuildingKind.SHED,
                    True,
                )
            elif roll < 0.95:
                acc.add_tree(
                    TreeInstance(
                        x=round(base[0], 1),
                        y=round(base[1], 1),
                        height_m=round(float(rng.uniform(7, 14)), 1),
                        radius_m=round(float(rng.uniform(3.0, 5.2)), 1),
                        kind=TreeKind.BROADLEAF if roll < 0.92 else TreeKind.CONIFER,
                    )
                )
            else:
                acc.add_prop(
                    PropInstance(
                        x=round(base[0], 1),
                        y=round(base[1], 1),
                        rotation_deg=round(bearing, 1),
                        kind=PropKind.WALL,
                        length_m=round(width, 1),
                    )
                )
        cursor += width + float(rng.uniform(6, 26))
    for _ in range(int(1 + rng.random() * 3)):
        spot = (
            centre[0] + float(rng.uniform(-70, 70)),
            centre[1] + float(rng.uniform(-70, 70)),
        )
        if _clear(spot, 20.0):
            acc.add_prop(
                PropInstance(
                    x=round(spot[0], 1),
                    y=round(spot[1], 1),
                    kind=PropKind.SILO,
                    length_m=round(float(rng.uniform(7, 12)), 1),
                )
            )


def _farmstead(rng: np.random.Generator, acc: _Accumulator, raster: _Raster, centre: Point) -> None:
    """A farmhouse, sheds and a cluttered yard, oriented to its track."""
    raster.paint_disc(SOIL, centre, 38.0)
    bearing = float(rng.uniform(0, 360))
    _place(
        acc,
        centre,
        bearing,
        float(rng.uniform(11, 16)),
        float(rng.uniform(13, 18)),
        float(rng.uniform(5.0, 8.5)),
        BuildingKind.HOUSE_GABLE,
        True,
    )
    for _ in range(2):
        offset = _rotate((float(rng.uniform(18, 34)), float(rng.uniform(-16, 16))), bearing)
        _place(
            acc,
            _add(centre, offset),
            bearing + float(rng.uniform(-25, 25)),
            float(rng.uniform(9, 18)),
            float(rng.uniform(12, 22)),
            float(rng.uniform(4.0, 6.5)),
            BuildingKind.SHED,
            True,
        )
    if float(rng.random()) < 0.7:
        offset = _rotate((float(rng.uniform(-40, 40)), float(rng.uniform(-40, 40))), bearing)
        acc.add_prop(
            PropInstance(
                x=round(centre[0] + offset[0], 1),
                y=round(centre[1] + offset[1], 1),
                rotation_deg=round(bearing, 1),
                kind=PropKind.SILO,
                length_m=round(float(rng.uniform(8, 14)), 1),
            )
        )
    for _ in range(int(4 + rng.random() * 5)):
        spot = _add(centre, (float(rng.uniform(-45, 45)), float(rng.uniform(-45, 45))))
        acc.add_prop(
            PropInstance(
                x=round(spot[0], 1),
                y=round(spot[1], 1),
                rotation_deg=round(float(rng.uniform(0, 180)), 1),
                kind=PropKind.HAYSTACK if float(rng.random()) < 0.5 else PropKind.FENCE,
                length_m=round(float(rng.uniform(2, 5)), 1),
            )
        )


def _pond(rng: np.random.Generator, centre: Point, radius: float) -> list[Point]:
    """An irregular pond outline (radial noise around a centre)."""
    lobes = int(rng.integers(5, 9))
    return [
        (
            centre[0] + math.cos(2 * math.pi * i / lobes) * radius * float(rng.uniform(0.62, 1.3)),
            centre[1] + math.sin(2 * math.pi * i / lobes) * radius * float(rng.uniform(0.62, 1.3)),
        )
        for i in range(lobes)
    ]


def _roadside_trees(
    rng: np.random.Generator, acc: _Accumulator, line: Sequence[Point], vegetation: float
) -> None:
    """Trees and hedges along a rural road, alternating sides."""
    if vegetation <= 0.0:
        return
    step = max(1, int(float(rng.uniform(55, 85)) / 60.0))
    for i in range(0, len(line) - 1, step):
        a_pt = line[i]
        b_pt = line[i + 1]
        direction = _norm(_sub(b_pt, a_pt))
        normal = (-direction[1], direction[0])
        side = 1.0 if (i // step) % 2 == 0 else -1.0
        spot = _add(a_pt, _scale(normal, side * float(rng.uniform(14, 26))))
        if not _clear(spot, 20.0):
            continue
        if float(rng.random()) < 0.6 * vegetation:
            acc.add_tree(
                TreeInstance(
                    x=round(spot[0], 1),
                    y=round(spot[1], 1),
                    height_m=round(float(rng.uniform(8, 15)), 1),
                    radius_m=round(float(rng.uniform(3.0, 5.4)), 1),
                    kind=TreeKind.BROADLEAF,
                )
            )
        else:
            acc.add_prop(
                PropInstance(
                    x=round(spot[0], 1),
                    y=round(spot[1], 1),
                    rotation_deg=round(_bearing(a_pt, b_pt), 1),
                    length_m=round(float(rng.uniform(8, 20)), 1),
                    kind=PropKind.HEDGE,
                )
            )


def _rural(
    rng: np.random.Generator,
    terrain: Terrain,
    acc: _Accumulator,
    raster: _Raster,
    config: LandscapeConfig,
) -> None:
    """Highway, village lanes, fields, farmsteads, water and woodland."""
    half = terrain.half_extent_m
    a = (-half * 1.2, float(rng.uniform(-half * 0.35, half * 0.35)))
    b = (half * 1.2, float(rng.uniform(-half * 0.35, half * 0.35)))
    highway = _clip_to_bubble(
        _clip_to_map(_sample_polyline(_wander(rng, a, b, 5, 260.0), 60.0), half)
    )
    if len(highway) >= 4:
        acc.roads.append(
            RoadSpec(
                kind=RoadKind.ARTERIAL,
                width_m=_road_width(RoadKind.ARTERIAL),
                markings=True,
                points=[(round(x, 1), round(y, 1)) for x, y in highway],
            )
        )
        raster.paint_corridor(GRAVEL, highway, _road_width(RoadKind.ARTERIAL) + 8.0)
        raster.paint_corridor(ASPHALT, highway, _road_width(RoadKind.ARTERIAL) + 1.0)
        _roadside_trees(rng, acc, highway, config.vegetation_density)

    village_centres: list[Point] = []
    if len(highway) >= 4:
        count = max(1, min(config.villages, len(highway) // 5))
        for i in range(count):
            at = int((i + 0.5) * (len(highway) - 1) / count)
            station = highway[at]
            ahead = highway[min(len(highway) - 1, at + 2)]
            direction = _norm(_sub(ahead, station))
            normal = (-direction[1], direction[0])
            side = 1.0 if i % 2 == 0 else -1.0
            centre = _add(station, _scale(normal, side * float(rng.uniform(260, 640))))
            centre = (
                max(-half + 220, min(half - 220, centre[0])),
                max(-half + 220, min(half - 220, centre[1])),
            )
            lane = _clip_to_map(
                _sample_polyline(
                    _wander(rng, _add(station, _scale(normal, side * 45.0)), centre, 3, 90.0), 40.0
                ),
                half,
            )
            if len(lane) < 3:
                continue
            acc.roads.append(
                RoadSpec(
                    kind=RoadKind.STREET,
                    width_m=_road_width(RoadKind.STREET),
                    points=[(round(p[0], 1), round(p[1], 1)) for p in lane],
                )
            )
            raster.paint_corridor(ASPHALT, lane, _road_width(RoadKind.STREET) + 1.0)
            _village(rng, acc, raster, centre, lane)
            village_centres.append(centre)

    _fields(rng, acc, raster, half, village_centres)

    for _ in range(int(2 + rng.random() * 2)):
        spot = (
            float(rng.uniform(-half * 0.8, half * 0.8)),
            float(rng.uniform(-half * 0.8, half * 0.8)),
        )
        if not _clear(spot, 240.0):
            continue
        outline = _pond(rng, spot, float(rng.uniform(35, 90)))
        acc.water.append(
            WaterSpec(
                kind=WaterKind.POND,
                width_m=float(rng.uniform(4, 9)),
                points=[(round(x, 1), round(y, 1)) for x, y in outline],
            )
        )
        raster.paint_polygon(WATER, outline)
        outlet = _wander(
            rng,
            spot,
            (spot[0] + float(rng.uniform(-700, 700)), spot[1] + float(rng.uniform(-700, 700))),
            4,
            140.0,
        )
        stream = _clip_to_map(_sample_polyline(outlet, 60.0), half)[:9]
        if len(stream) >= 3:
            acc.water.append(
                WaterSpec(
                    kind=WaterKind.STREAM,
                    width_m=float(rng.uniform(3, 7)),
                    points=[(round(p[0], 1), round(p[1], 1)) for p in stream],
                )
            )
            raster.paint_corridor(WATER, stream, 6.0)

    for _ in range(3):
        spot = (
            float(rng.uniform(-half * 0.75, half * 0.75)),
            float(rng.uniform(-half * 0.75, half * 0.75)),
        )
        if math.hypot(*spot) < 800.0:
            continue
        # Farm tracks leave the base camp on the far side of the defended bubble,
        # never from its centre.
        gate = math.atan2(spot[1], spot[0])
        start = (math.cos(gate) * 380.0, math.sin(gate) * 380.0)
        track = _clip_to_bubble(
            _clip_to_map(_sample_polyline(_wander(rng, start, spot, 3, 200.0), 50.0), half)
        )
        if len(track) < 3:
            continue
        acc.roads.append(
            RoadSpec(
                kind=RoadKind.TRACK,
                width_m=_road_width(RoadKind.TRACK),
                points=[(round(p[0], 1), round(p[1], 1)) for p in track],
            )
        )
        raster.paint_corridor(GRAVEL, track, _road_width(RoadKind.TRACK) + 4.0)
        _farmstead(rng, acc, raster, track[-1])

    if config.vegetation_density > 0.0:
        corner = _rotate((half * 0.72, half * 0.72), float(rng.uniform(0, 360)))
        for _ in range(int(240 * config.vegetation_density)):
            spot = (
                corner[0] + float(rng.normal(0, 420)),
                corner[1] + float(rng.normal(0, 420)),
            )
            if abs(spot[0]) > half or abs(spot[1]) > half or not _clear(spot, 70.0):
                continue
            acc.add_tree(
                TreeInstance(
                    x=round(spot[0], 1),
                    y=round(spot[1], 1),
                    height_m=round(float(rng.uniform(9, 18)), 1),
                    radius_m=round(float(rng.uniform(3.4, 6.4)), 1),
                    kind=TreeKind.CONIFER if float(rng.random()) < 0.45 else TreeKind.BROADLEAF,
                )
            )


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #


def _downsample(heights: np.ndarray, grid_n: int) -> np.ndarray:
    """Bilinear resample of a square heightfield down to `grid_n` posts per side."""
    res = heights.shape[0]
    if res == grid_n:
        return heights.astype(np.float64)
    coords = np.linspace(0.0, res - 1.0, grid_n)
    i0 = np.clip(np.floor(coords).astype(np.int64), 0, res - 2)
    frac = coords - i0
    i1 = i0 + 1
    a = heights[np.ix_(i0, i0)]
    b = heights[np.ix_(i1, i0)]
    c = heights[np.ix_(i0, i1)]
    d = heights[np.ix_(i1, i1)]
    top = a + (b - a) * frac[None, :]
    bottom = c + (d - c) * frac[None, :]
    return top + (bottom - top) * frac[:, None]


def _authored_instances(
    buildings: Sequence[BuildingSpec], rng: np.random.Generator
) -> list[BuildingInstance]:
    """Turn the scenario's occluder footprints into drawable instances."""
    out: list[BuildingInstance] = []
    for spec in buildings:
        roll = float(rng.random())
        if spec.height_m >= 30.0:
            kind = BuildingKind.TOWER
        elif spec.height_m >= 16.0:
            kind = BuildingKind.APARTMENT
        elif spec.width_m >= 28.0 and spec.depth_m >= 26.0:
            kind = BuildingKind.WAREHOUSE
        elif roll < 0.4:
            kind = BuildingKind.HOUSE_GABLE
        elif roll < 0.75:
            kind = BuildingKind.HOUSE_HIP
        elif roll < 0.9:
            kind = BuildingKind.SHOP
        else:
            kind = BuildingKind.HOUSE_FLAT
        out.append(
            BuildingInstance(
                x=spec.x,
                y=spec.y,
                width_m=spec.width_m,
                depth_m=spec.depth_m,
                height_m=spec.height_m,
                rotation_deg=spec.rotation_deg,
                kind=kind,
                occluder=True,
            )
        )
    return out


def generate_landscape(
    terrain: Terrain,
    *,
    seed: int,
    preset: TerrainPreset,
    buildings: Sequence[BuildingSpec] = (),
    config: LandscapeConfig | None = None,
    scenario_id: str = "",
) -> LandscapeSpec:
    """Describe the world for `terrain` at `seed`.

    Deterministic: the same arguments always produce the same `LandscapeSpec`.
    When `buildings` is empty the layout generates its own settlement footprints
    (flagged `occluder=True` but not carried by any scenario, so they only affect
    the picture); when it is not empty those exact footprints are returned, so what
    occludes the simulation is what the client draws.
    """
    cfg = config or LandscapeConfig()
    rng = np.random.default_rng((int(seed) * 6364136223846793005 + 1442695040888963407) % (2**64))
    half = terrain.half_extent_m
    raster = _Raster(half, cfg.grid_n)
    acc = _Accumulator.empty(cfg.vegetation_density)
    authored = bool(buildings)
    # Seeded first so the generated settlement is laid out around the real
    # occluders instead of on top of them.
    if authored:
        acc.buildings = _authored_instances(buildings, rng)

    if preset is TerrainPreset.URBAN:
        _urban(rng, terrain, acc, raster)
    else:
        _rural(rng, terrain, acc, raster, cfg)

    _installation(rng, acc, raster)

    if authored:
        for spec in buildings:
            raster.paint_disc(URBAN, (spec.x, spec.y), max(spec.width_m, spec.depth_m) * 0.8 + 12.0)

    heights = _downsample(terrain.flat_grid(), cfg.grid_n)
    height_cm = np.clip(np.round(heights * 100.0), -32768, 32767).astype("<i2")
    return LandscapeSpec(
        scenario_id=scenario_id,
        seed=int(seed),
        size_m=float(terrain.size_m),
        grid_n=cfg.grid_n,
        max_height_m=float(max(1.0, float(heights.max()))),
        heights_b64=base64.b64encode(height_cm.tobytes()).decode("ascii"),
        landcover_b64=base64.b64encode(np.ascontiguousarray(raster.grid).tobytes()).decode("ascii"),
        palette=list(LANDCOVER_PALETTE),
        roads=acc.roads,
        buildings=acc.buildings,
        fields=acc.fields,
        trees=acc.trees,
        props=acc.props,
        water=acc.water,
    )


def landscape_fingerprint(spec: LandscapeSpec) -> str:
    """Stable hash of a generated layout, for caching and tests."""
    h = hashlib.blake2b(digest_size=12)
    h.update(spec.heights_b64.encode())
    h.update(spec.landcover_b64.encode())
    for road in spec.roads:
        h.update(f"R{road.kind.value}{road.width_m:.1f}".encode())
        h.update(b";".join(f"{x:.0f},{y:.0f}".encode() for x, y in road.points))
    for b in spec.buildings:
        h.update(
            f"B{b.x:.1f},{b.y:.1f},{b.width_m:.1f},{b.depth_m:.1f},{b.height_m:.1f},"
            f"{b.rotation_deg:.1f},{b.kind.value}".encode()
        )
    for t in spec.trees:
        h.update(f"T{t.x:.0f},{t.y:.0f},{t.height_m:.1f}".encode())
    for p in spec.props:
        h.update(f"P{p.x:.0f},{p.y:.0f},{p.kind.value}".encode())
    for w in spec.water:
        h.update(f"W{w.kind.value}".encode())
        h.update(b";".join(f"{x:.0f},{y:.0f}".encode() for x, y in w.points))
    return h.hexdigest()


def counts(spec: LandscapeSpec) -> dict[str, int]:
    """Element counts, used by the tests and the authoring script."""
    return {
        "roads": len(spec.roads),
        "buildings": len(spec.buildings),
        "trees": len(spec.trees),
        "props": len(spec.props),
        "water": len(spec.water),
    }


def buildings_as_specs(spec: LandscapeSpec) -> tuple[BuildingSpec, ...]:
    """Generated footprints as occluder specs, for authoring a scenario from them."""
    return tuple(
        BuildingSpec(
            x=b.x,
            y=b.y,
            width_m=b.width_m,
            depth_m=b.depth_m,
            height_m=b.height_m,
            rotation_deg=b.rotation_deg,
        )
        for b in spec.buildings
        if b.height_m >= BLOCKING_MIN_M
    )
