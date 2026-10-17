"""Procedural terrain heightfields, abstract building blocks and line-of-sight.

All terrain is generated from a seeded NumPy value-noise field, so a scenario
replays to the same ground everywhere. An optional PNG heightmap can be
imported instead (grayscale -> metres).

The terrain is what makes the "degraded sensors" and "terrain-hugging" scenarios
meaningful: it masks radar/EO/AC lines of sight and dictates whether a
low-flying target can reach the site at all.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.core.schemas import BuildingSpec, TerrainPreset, TerrainSpec, Vec3

__all__ = [
    "Terrain",
    "build_terrain",
    "terrain_fingerprint",
]


@dataclass(frozen=True, slots=True)
class Terrain:
    """A sampled heightfield plus optional abstract building blocks."""

    size_m: float
    resolution: int
    scale_m: float
    heights: np.ndarray  # shape (resolution, resolution), row-major, +y rows
    buildings: tuple[BuildingSpec, ...] = ()
    fingerprint: str = ""
    _cached_flat: np.ndarray | None = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------------ #
    # Sampling
    # ------------------------------------------------------------------ #
    @property
    def post_spacing_m(self) -> float:
        """Ground distance between adjacent grid posts."""
        return self.size_m / (self.resolution - 1)

    @property
    def half_extent_m(self) -> float:
        """Half the world extent; the defended site sits at the origin."""
        return self.size_m / 2.0

    def clamp(self, x: float, y: float) -> tuple[float, float]:
        """Clamp a world position into the sampled grid domain."""
        h = self.half_extent_m
        return (min(max(x, -h), h), min(max(y, -h), h))

    def height_at(self, x: float, y: float) -> float:
        """Bilinearly interpolate ground height (metres AGL datum) at ENU (x, y)."""
        cx, cy = self.clamp(x, y)
        res = self.resolution
        gx = (cx + self.half_extent_m) / self.post_spacing_m
        gy = (cy + self.half_extent_m) / self.post_spacing_m
        i0 = min(int(gx), res - 2)
        j0 = min(int(gy), res - 2)
        fx = min(max(gx - i0, 0.0), 1.0)
        fy = min(max(gy - j0, 0.0), 1.0)
        h = self.heights
        h00 = float(h[i0, j0])
        h10 = float(h[i0 + 1, j0])
        h01 = float(h[i0, j0 + 1])
        h11 = float(h[i0 + 1, j0 + 1])
        top = h00 + (h10 - h00) * fx
        bot = h01 + (h11 - h01) * fx
        return top + (bot - top) * fy

    def slope_at(self, x: float, y: float, step_m: float = 20.0) -> tuple[float, float]:
        """Central-difference terrain gradient (dz/dx, dz/dy) in m/m."""
        hx = (self.height_at(x + step_m, y) - self.height_at(x - step_m, y)) / (2 * step_m)
        hy = (self.height_at(x, y + step_m) - self.height_at(x, y - step_m)) / (2 * step_m)
        return (hx, hy)

    def min_clearance_m(self, p0: Vec3, p1: Vec3, samples: int = 48) -> float:
        """Smallest gap between the straight line p0->p1 and the ground.

        A negative value means the line is buried in terrain.
        """
        best = float("inf")
        for i in range(samples + 1):
            f = i / samples
            x = p0.x + (p1.x - p0.x) * f
            y = p0.y + (p1.y - p0.y) * f
            z = p0.z + (p1.z - p0.z) * f
            best = min(best, z - self.height_at(x, y))
        return best

    # ------------------------------------------------------------------ #
    # Buildings / line of sight
    # ------------------------------------------------------------------ #
    def building_blocks_line(self, p0: Vec3, p1: Vec3) -> bool:
        """True if an abstract building box intersects the segment p0->p1 in plan."""
        if not self.buildings:
            return False
        return any(_segment_intersects_rect(p0, p1, b) for b in self.buildings)

    def line_of_sight(self, p0: Vec3, p1: Vec3, ignore_terrain: bool = False) -> bool:
        """Conservative visibility test from p0 to p1.

        Blocks on terrain masking and on abstract building boxes.
        """
        if not ignore_terrain and self.min_clearance_m(p0, p1) <= 0.0:
            return False
        if self.building_blocks_line(p0, p1):
            # A building only blocks if it is tall enough to reach the ray.
            for b in self.buildings:
                if not _segment_intersects_rect(p0, p1, b):
                    continue
                f = _segment_rect_param(p0, p1, b)
                if f is None:
                    continue
                ray_z = p0.z + (p1.z - p0.z) * f
                base = self.height_at(b.x, b.y)
                if base + b.height_m > ray_z:
                    return False
        return True

    def occlusion_fraction(self, p0: Vec3, p1: Vec3, samples: int = 48) -> float:
        """Fraction of sampled points on the ray that are masked by terrain."""
        if samples <= 0:
            return 0.0
        blocked = 0
        for i in range(1, samples):
            f = i / samples
            x = p0.x + (p1.x - p0.x) * f
            y = p0.y + (p1.y - p0.y) * f
            z = p0.z + (p1.z - p0.z) * f
            if z <= self.height_at(x, y):
                blocked += 1
        return blocked / (samples - 1)

    # ------------------------------------------------------------------ #
    def flat_grid(self) -> np.ndarray:
        """Return (and cache) a contiguous copy of the height array."""
        if self._cached_flat is None:
            object.__setattr__(self, "_cached_flat", np.ascontiguousarray(self.heights))
        return self._cached_flat  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Noise
# --------------------------------------------------------------------------- #


def _value_noise_2d(
    rng: np.random.Generator, resolution: int, octaves: int, persistence: float
) -> np.ndarray:
    """Fractal value noise on the unit square, returned in [0, 1]."""
    acc = np.zeros((resolution, resolution), dtype=np.float64)
    amplitude = 1.0
    total = 0.0
    for octave in range(octaves):
        cells = max(2, 2**octave + 1)
        grid = rng.random((cells + 1, cells + 1))
        acc += amplitude * _bilinear_resample(grid, resolution)
        total += amplitude
        amplitude *= persistence
    out = acc / total
    return (out - out.min()) / max(1e-12, float(out.max() - out.min()))


def _bilinear_resample(grid: np.ndarray, resolution: int) -> np.ndarray:
    """Bilinearly resample a coarse lattice onto a `resolution`-square grid."""
    g = grid.shape[0] - 1
    coords = np.linspace(0.0, float(g), resolution)
    i = np.clip(np.floor(coords).astype(np.int64), 0, g - 1)
    f = coords - i
    i1 = np.minimum(i + 1, g)
    # columns (x index) then rows (y index)
    c0 = grid[i, i]
    c1 = grid[i1, i]
    c2 = grid[i, i1]
    c3 = grid[i1, i1]
    fx = f[None, :]
    fy = f[:, None]
    top = c0 + (c1 - c0) * fx
    bot = c2 + (c3 - c2) * fx
    return top + (bot - top) * fy


def _ridged_noise(rng: np.random.Generator, resolution: int, octaves: int) -> np.ndarray:
    """Ridged multifractal field in [0, 1]; sharp crests for hill terrain."""
    acc = np.zeros((resolution, resolution), dtype=np.float64)
    amplitude = 1.0
    total = 0.0
    for octave in range(octaves):
        cells = max(2, 2**octave + 1)
        grid = rng.random((cells + 1, cells + 1))
        field_ = 1.0 - np.abs(2.0 * grid - 1.0)
        acc += amplitude * _bilinear_resample(field_, resolution) ** 2
        total += amplitude
        amplitude *= 0.5
    out = acc / total
    return (out - out.min()) / max(1e-12, float(out.max() - out.min()))


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=8)
def _cached_terrain(
    preset: TerrainPreset,
    size_m: float,
    resolution: int,
    scale_m: float,
    roughness: float,
    seed: int,
    heightmap_path: str | None,
    max_height_m: float | None,
    building_count: int,
) -> Terrain:
    """Memoised terrain construction (the same spec always yields the same field)."""
    rng = np.random.default_rng((seed * 2654435761 + 1013904223) % (2**32))

    if heightmap_path:
        heights = _load_heightmap(Path(heightmap_path), resolution, max_height_m or scale_m)
        buildings: tuple[BuildingSpec, ...] = ()
    elif preset is TerrainPreset.HILLS:
        base = _value_noise_2d(rng, resolution, octaves=5, persistence=0.55)
        ridge = _ridged_noise(rng, resolution, octaves=5)
        mix = float(np.clip(0.25 + 0.6 * roughness, 0.0, 0.95))
        norm = base * (1.0 - mix) + ridge * mix
        heights = _normalise(norm) * scale_m
        buildings = ()
    elif preset is TerrainPreset.URBAN:
        base = _value_noise_2d(rng, resolution, octaves=4, persistence=0.5)
        heights = _normalise(base) * (scale_m * 0.25) + 4.0
        heights += _urban_roughness(rng, resolution) * roughness * 8.0
        buildings = _make_buildings(rng, size_m, building_count)
    else:  # RURAL
        base = _value_noise_2d(rng, resolution, octaves=4, persistence=0.5)
        heights = _normalise(base) * (scale_m * 0.45) + 3.0
        buildings = ()

    # Guarantee the defended site is flat and clear of buildings.
    heights = _flatten_site(heights, resolution, size_m, radius_m=180.0, blend_m=220.0)

    fingerprint = terrain_fingerprint(heights, buildings)
    return Terrain(
        size_m=size_m,
        resolution=resolution,
        scale_m=scale_m,
        heights=heights,
        buildings=buildings,
        fingerprint=fingerprint,
    )


def _normalise(a: np.ndarray) -> np.ndarray:
    lo = float(a.min())
    hi = float(a.max())
    if hi - lo < 1e-12:
        return np.zeros_like(a)
    return (a - lo) / (hi - lo)


def _urban_roughness(rng: np.random.Generator, resolution: int) -> np.ndarray:
    """Blocky, low-frequency roughness typical of a built-up area."""
    cells = max(4, resolution // 8)
    grid = rng.random((cells + 1, cells + 1))
    return _bilinear_resample(grid, resolution)


def _make_buildings(
    rng: np.random.Generator, size_m: float, count: int
) -> tuple[BuildingSpec, ...]:
    """Scatter abstract building boxes, leaving a clear bubble around the site."""
    out: list[BuildingSpec] = []
    half = size_m / 2.0
    guard = 260.0
    attempts = 0
    max_attempts = count * 40 + 200
    while len(out) < count and attempts < max_attempts:
        attempts += 1
        x = float(rng.uniform(-half + 60.0, half - 60.0))
        y = float(rng.uniform(-half + 60.0, half - 60.0))
        if math.hypot(x, y) < guard:
            continue
        if any(math.hypot(x - b.x, y - b.y) < 55.0 for b in out):
            continue
        out.append(
            BuildingSpec(
                x=round(x, 2),
                y=round(y, 2),
                width_m=round(float(rng.uniform(18.0, 70.0)), 2),
                depth_m=round(float(rng.uniform(18.0, 70.0)), 2),
                height_m=round(float(rng.uniform(9.0, 45.0)), 2),
            )
        )
    return tuple(out)


def _flatten_site(
    heights: np.ndarray, resolution: int, size_m: float, radius_m: float, blend_m: float
) -> np.ndarray:
    """Blend the terrain to datum inside a disc around the origin."""
    out = heights.copy()
    half = size_m / 2.0
    ys = np.linspace(-half, half, resolution)
    xs = np.linspace(-half, half, resolution)
    gx, gy = np.meshgrid(xs, ys, indexing="xy")
    # heights is stored as [ix, iy] = [x, y]
    r = np.sqrt(gx**2 + gy**2)
    w = np.clip((r - radius_m) / blend_m, 0.0, 1.0)
    return out * w


def _load_heightmap(path: Path, resolution: int, max_height_m: float) -> np.ndarray:
    """Import a PNG heightmap: grayscale resampled to the grid, scaled to metres."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - pillow is a hard requirement
        raise RuntimeError("Pillow is required for heightmap import") from exc

    with Image.open(path) as img:
        grey = img.convert("L").resize((resolution, resolution), Image.Resampling.BILINEAR)
        data = np.asarray(grey, dtype=np.float64) / 255.0
    return data * max_height_m


def _segment_intersects_rect(p0: Vec3, p1: Vec3, rect: BuildingSpec) -> bool:
    """Plan-view segment vs axis-aligned rectangle intersection."""
    return _segment_rect_param(p0, p1, rect) is not None


def _segment_rect_param(p0: Vec3, p1: Vec3, rect: BuildingSpec) -> float | None:
    """Liang-Barsky clip: return the entry parameter t, or None when disjoint."""
    dx = p1.x - p0.x
    dy = p1.y - p0.y
    x0 = rect.x - rect.width_m / 2.0
    x1 = rect.x + rect.width_m / 2.0
    y0 = rect.y - rect.depth_m / 2.0
    y1 = rect.y + rect.depth_m / 2.0
    t_enter, t_exit = 0.0, 1.0
    for p, q in ((-dx, p0.x - x0), (dx, x1 - p0.x), (-dy, p0.y - y0), (dy, y1 - p0.y)):
        if abs(p) < 1e-9:
            if q < 0.0:
                return None
            continue
        r = q / p
        if p < 0.0:
            if r > t_exit:
                return None
            t_enter = max(t_enter, r)
        else:
            if r < t_enter:
                return None
            t_exit = min(t_exit, r)
    if t_enter > t_exit:
        return None
    return t_enter


def terrain_fingerprint(heights: np.ndarray, buildings: tuple[BuildingSpec, ...]) -> str:
    """Stable hash of the sampled terrain, used to validate save/load round trips."""
    h = hashlib.blake2b(digest_size=12)
    h.update(np.ascontiguousarray(heights, dtype=np.float64).tobytes())
    for b in buildings:
        h.update(f"{b.x:.2f},{b.y:.2f},{b.width_m:.2f},{b.depth_m:.2f},{b.height_m:.2f}".encode())
    return h.hexdigest()


def build_terrain(spec: TerrainSpec, seed: int | None = None) -> Terrain:
    """Build (or fetch from cache) the terrain described by `spec`."""
    building_count = len(spec.buildings)
    explicit = spec.buildings
    terrain = _cached_terrain(
        spec.preset,
        float(spec.size_m),
        int(spec.resolution),
        float(spec.scale_m),
        float(spec.roughness),
        int(spec.seed if seed is None else seed),
        spec.heightmap_path,
        spec.max_height_m,
        building_count,
    )
    if not explicit:
        return terrain
    return Terrain(
        size_m=terrain.size_m,
        resolution=terrain.resolution,
        scale_m=terrain.scale_m,
        heights=terrain.heights,
        buildings=tuple(explicit),
        fingerprint=terrain_fingerprint(terrain.heights, tuple(explicit)),
    )


def export_heightmap(terrain: Terrain, path: Path) -> Path:
    """Write the heightfield out as an 8-bit PNG heightmap (round-trippable)."""
    from PIL import Image

    peak = max(1e-6, float(terrain.heights.max()))
    data = np.clip(terrain.heights / peak, 0.0, 1.0)
    img = Image.fromarray((data * 255.0).astype(np.uint8), mode="L")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path
