"""Author the city scenarios and retag the rural ones in `scenarios/`.

The shipped library is eight scenarios: four `rural` and four `city`. The city
scenarios declare their building blocks explicitly rather than relying on the
urban preset's random scatter, so occlusion is *deliberate* - a street canyon
that masks a low run, roof lines that hide a swarm launch, a civil corridor
where the buildings sit either side of the air route.

Run from the repo root:

    .venv/Scripts/python backend/scripts/author_city_scenarios.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.core.generator import default_effectors, default_sensors  # noqa: E402
from app.core.schemas import (  # noqa: E402
    BuildingSpec,
    DifficultyVector,
    EnvSpec,
    RoeSpec,
    Scenario,
    SiteSpec,
    TerrainPreset,
    TerrainSpec,
    ThreatSpec,
)

OUT_DIR = REPO_ROOT / "scenarios"


def site() -> SiteSpec:
    """The defended site shared by the city scenarios."""
    return SiteSpec(
        protected_radius_m=150.0,
        ring_distances_m=[400.0, 900.0, 1800.0, 3000.0],
        civil_sectors=[],
    )


def terrain(seed: int, buildings: list[BuildingSpec], roughness: float = 0.6) -> TerrainSpec:
    """Flat-ish urban ground with explicit building blocks."""
    return TerrainSpec(
        preset=TerrainPreset.URBAN,
        size_m=6000.0,
        resolution=192,
        scale_m=80.0,
        roughness=roughness,
        seed=seed,
        buildings=buildings,
    )


def block(x: float, y: float, w: float, d: float, h: float) -> BuildingSpec:
    """One abstract building block."""
    return BuildingSpec(x=x, y=y, width_m=w, depth_m=d, height_m=h)


def env(visibility: float, wind: float, heading: float) -> EnvSpec:
    """Daylight urban air."""
    return EnvSpec(
        time_of_day="day",
        visibility_m=visibility,
        rain=0.0,
        wind_speed_mps=wind,
        wind_heading_deg=heading,
    )


def behaviour(
    kind: str,
    speed: float,
    terminal_alt: float,
    *,
    clearance: float = 20.0,
    following: bool = False,
    separation: float = 20.0,
    alignment: float = 0.3,
    cohesion: float = 0.4,
) -> dict[str, object]:
    """One behaviour block, with the unused orbit/loiter knobs pinned."""
    return {
        "kind": kind,
        "speed_mps": speed,
        "terminal_altitude_m": terminal_alt,
        "min_clearance_m": clearance,
        "terrain_following": following,
        "separation_m": separation,
        "alignment_weight": alignment,
        "cohesion_weight": cohesion,
        "loiter_period_s": 48.0,
        "loiter_radius_m": 140.0,
        "orbit_period_s": 55.0,
        "orbit_radius_m": 190.0,
        "strike_bearing_deg": None,
        "waypoints": [],
    }


def wave(
    name: str,
    entity_class: str,
    label: str,
    count: int,
    spawn_t: float,
    window: float,
    bearing: float,
    jitter: float,
    behaviour_spec: dict[str, object],
    *,
    formation: str = "line",
    rf_active: bool | None = None,
    beacon_active: bool | None = None,
    hint_bias: float = 0.0,
) -> ThreatSpec:
    """One threat or decoy wave."""
    return ThreatSpec(
        name=name,
        entity_class=entity_class,
        label=label,
        count=count,
        spawn_t=spawn_t,
        spawn_window_s=window,
        spawn_bearing_deg=bearing,
        spawn_jitter_deg=jitter,
        behaviour=behaviour_spec,
        formation=formation,
        rf_active=rf_active,
        beacon_active=beacon_active,
        hint_bias=hint_bias,
    )


def difficulty(
    *,
    swarm: float,
    axes: float,
    pressure: float,
    degradation: float,
    decoys: float,
    visibility: float,
    ambiguity: float,
    simultaneity: float,
    roe: float,
    night: float = 0.0,
) -> DifficultyVector:
    """One difficulty vector across all ten axes."""
    return DifficultyVector(
        swarm_size=swarm,
        approach_axes=axes,
        time_pressure=pressure,
        sensor_degradation_level=degradation,
        decoy_ratio=decoys,
        visibility=visibility,
        night=night,
        ambiguity=ambiguity,
        simultaneity=simultaneity,
        roe_strictness=roe,
    )


def standard_roe() -> RoeSpec:
    """The STANDARD rule set used by three of the four city scenarios."""
    return RoeSpec(
        rule_set="STANDARD",
        require_id_before_engage=False,
        allowed_engage_labels=["hostile"],
        friend_or_civil_warning_required=True,
        collateral_sector_tolerance=0.0,
        late_engage_threshold_s=20.0,
        min_track_quality=0.15,
        require_warning_before_engage=True,
    )


# --------------------------------------------------------------------------- #
# The three new city scenarios
# --------------------------------------------------------------------------- #


def canyon_run() -> Scenario:
    """A quad threads a street canyon, using the blocks as masking."""
    seed = 3001
    # Two rows of tower blocks facing each other across a ~95 m street running
    # east-west, so the approach from bearing 090 is screened until the gaps.
    buildings = [
        block(700.0, 75.0, 85.0, 55.0, 34.0),
        block(700.0, -75.0, 85.0, 55.0, 30.0),
        block(1000.0, 80.0, 90.0, 60.0, 38.0),
        block(1000.0, -80.0, 90.0, 60.0, 32.0),
        block(1350.0, 75.0, 88.0, 52.0, 36.0),
        block(1350.0, -75.0, 88.0, 52.0, 40.0),
        block(900.0, 190.0, 70.0, 45.0, 24.0),
        block(900.0, -190.0, 70.0, 45.0, 26.0),
        block(1400.0, 200.0, 75.0, 50.0, 28.0),
        block(1400.0, -200.0, 75.0, 50.0, 22.0),
    ]
    return Scenario(
        id="scn_city_canyon_run",
        name="Canyon run between tower blocks",
        description=(
            "A quad works down a street canyon between two rows of tower blocks. "
            "The radar loses it every time it drops behind a roof line, and the "
            "EO camera only gets a look in the cross-street gaps. Expect the "
            "track to fade in and out rather than arrive clean."
        ),
        seed=seed,
        duration_s=480,
        terrain=terrain(seed, buildings, roughness=0.65),
        environment=env(6000.0, 3.0, 210.0),
        site=site(),
        sensors=default_sensors(),
        effectors=default_effectors(),
        threats=[
            wave(
                "canyon runner",
                "quad_small",
                "hostile",
                1,
                spawn_t=40.0,
                window=30.0,
                bearing=90.0,
                jitter=8.0,
                behaviour_spec=behaviour(
                    "terminal_dive", 6.0, 38.0, clearance=14.0, following=True
                ),
                rf_active=True,
                hint_bias=0.15,
            )
        ],
        decoys=[],
        roe=standard_roe(),
        difficulty=difficulty(
            swarm=0.1,
            axes=0.35,
            pressure=0.3,
            degradation=0.2,
            decoys=0.0,
            visibility=0.6,
            ambiguity=0.25,
            simultaneity=0.15,
            roe=0.3,
        ),
        tags=["city", "street-canyon", "occlusion", "masking", "quad"],
    )


def rooftop_swarm() -> Scenario:
    """A swarm launches from behind a roof line to the north."""
    seed = 3002
    # A roof line on the north side, tall enough to hide a launch but with
    # gaps so the swarm is picked up as it clears the buildings.
    buildings = [
        block(0.0, 950.0, 75.0, 70.0, 52.0),
        block(230.0, 1060.0, 60.0, 60.0, 40.0),
        block(-230.0, 1060.0, 60.0, 60.0, 46.0),
        block(430.0, 1220.0, 65.0, 65.0, 34.0),
        block(-430.0, 1220.0, 65.0, 65.0, 38.0),
        block(130.0, 1360.0, 75.0, 55.0, 48.0),
        block(-130.0, 1360.0, 75.0, 55.0, 42.0),
    ]
    return Scenario(
        id="scn_city_rooftop_swarm",
        name="Swarm launch from the roof line",
        description=(
            "Six quads lift off from behind buildings to the north and cross the "
            "city in a loose wedge. The roof line hides the launch, so the first "
            "warning is the swarm clearing the buildings already inside the "
            "middle ring. Bird traffic shares the same airspace."
        ),
        seed=seed,
        duration_s=600,
        terrain=terrain(seed, buildings, roughness=0.6),
        environment=env(6500.0, 5.0, 250.0),
        site=site(),
        sensors=default_sensors(),
        effectors=default_effectors(),
        threats=[
            wave(
                "rooftop swarm",
                "quad_small",
                "hostile",
                6,
                spawn_t=50.0,
                window=45.0,
                bearing=0.0,
                jitter=25.0,
                behaviour_spec=behaviour(
                    "swarm",
                    10.0,
                    55.0,
                    clearance=20.0,
                    following=True,
                    separation=26.0,
                    alignment=0.8,
                    cohesion=0.9,
                ),
                formation="wedge",
                rf_active=True,
                hint_bias=0.1,
            )
        ],
        decoys=[
            wave(
                "city flock",
                "bird_flock",
                "bird",
                4,
                spawn_t=180.0,
                window=90.0,
                bearing=320.0,
                jitter=40.0,
                behaviour_spec=behaviour("swarm", 12.0, 35.0, separation=12.0),
                formation="scatter",
                rf_active=False,
                beacon_active=False,
                hint_bias=0.55,
            )
        ],
        roe=standard_roe(),
        difficulty=difficulty(
            swarm=0.6,
            axes=0.25,
            pressure=0.45,
            degradation=0.1,
            decoys=0.25,
            visibility=0.65,
            ambiguity=0.2,
            simultaneity=0.5,
            roe=0.3,
        ),
        tags=["city", "swarm", "rooftop", "saturation", "birds"],
    )


def civil_corridor() -> Scenario:
    """One hostile hiding under a civil air route through the city."""
    seed = 3003
    # Buildings sit either side of the east-west air route, so the civil
    # traffic is periodically screened and the low hostile is always screened.
    buildings = [
        block(820.0, 130.0, 80.0, 60.0, 32.0),
        block(820.0, -130.0, 80.0, 60.0, 36.0),
        block(1180.0, 125.0, 90.0, 55.0, 40.0),
        block(1180.0, -125.0, 90.0, 55.0, 38.0),
        block(320.0, 230.0, 70.0, 70.0, 26.0),
        block(-560.0, 160.0, 65.0, 65.0, 48.0),
        block(1650.0, 100.0, 85.0, 60.0, 30.0),
    ]
    return Scenario(
        id="scn_city_civil_corridor",
        name="Hostile under the civil corridor",
        description=(
            "A civil air route runs east-west over the city and a single quad "
            "comes in low beneath it from the west. STRICT rules apply: the "
            "positive identification has to be made before anything is "
            "released, and the airliners keep re-entering the picture."
        ),
        seed=seed,
        duration_s=540,
        terrain=terrain(seed, buildings, roughness=0.55),
        environment=env(7000.0, 4.0, 200.0),
        site=site(),
        sensors=default_sensors(),
        effectors=default_effectors(),
        threats=[
            wave(
                "low intruder",
                "quad_small",
                "hostile",
                1,
                spawn_t=45.0,
                window=30.0,
                bearing=250.0,
                jitter=12.0,
                behaviour_spec=behaviour(
                    "terminal_dive", 7.0, 40.0, clearance=18.0, following=True
                ),
                rf_active=True,
                hint_bias=0.2,
            )
        ],
        decoys=[
            wave(
                "airliners",
                "civil_aircraft",
                "civil",
                2,
                spawn_t=30.0,
                window=60.0,
                bearing=20.0,
                jitter=15.0,
                behaviour_spec=behaviour("transit", 55.0, 900.0, clearance=200.0, separation=400.0),
                formation="leader_follower",
                rf_active=False,
                beacon_active=False,
                hint_bias=0.4,
            )
        ],
        roe=RoeSpec(
            rule_set="STRICT",
            require_id_before_engage=True,
            allowed_engage_labels=["hostile"],
            friend_or_civil_warning_required=True,
            collateral_sector_tolerance=0.0,
            late_engage_threshold_s=25.0,
            min_track_quality=0.2,
            require_warning_before_engage=True,
        ),
        difficulty=difficulty(
            swarm=0.05,
            axes=0.3,
            pressure=0.35,
            degradation=0.15,
            decoys=0.4,
            visibility=0.7,
            ambiguity=0.35,
            simultaneity=0.2,
            roe=0.75,
        ),
        tags=["city", "civil", "corridor", "strict-roe", "lookalike"],
    )


# --------------------------------------------------------------------------- #
# Rural retags
# --------------------------------------------------------------------------- #

RURAL_IDS = [
    "scn_solo_fpv_dusk",
    "scn_civil_overflight",
    "scn_decoy_gauntlet",
    "scn_friendly_lookalike",
]

# Tags replaced wholesale on the kept scenarios so `rural` / `city` is the
# single, reliable category facet in the library.
RETAGS: dict[str, list[str]] = {
    "scn_solo_fpv_dusk": ["rural", "intro", "solo", "dusk", "fpv"],
    "scn_civil_overflight": ["rural", "civil", "overflight", "collateral", "civil-sector"],
    "scn_decoy_gauntlet": ["rural", "decoys", "friendly", "beacon-dropout", "trap"],
    "scn_friendly_lookalike": ["rural", "strict-roe", "fratricide-risk", "trap", "friendly"],
    "scn_urban_slow_quad": ["city", "slow", "occlusion", "quad"],
}


def write(scenario: Scenario) -> Path:
    """Validate, then write one scenario with stable key ordering."""
    data = scenario.model_dump(mode="json")
    path = OUT_DIR / f"{scenario.id}.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def retag(path: Path, tags: list[str]) -> None:
    """Replace the tag list on an existing scenario file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tags"] = tags
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    """Author the three city scenarios and retag the kept ones."""
    for factory in (canyon_run, rooftop_swarm, civil_corridor):
        scenario = factory()
        Scenario.model_validate(scenario.model_dump(mode="json"))
        print(f"wrote {write(scenario).name}")

    for stem, tags in RETAGS.items():
        path = OUT_DIR / f"{stem}.json"
        if not path.exists():
            print(f"SKIP {stem} (not present)")
            continue
        retag(path, tags)
        print(f"retagged {stem}")

    missing = [s for s in RURAL_IDS if not (OUT_DIR / f"{s}.json").exists()]
    if missing:
        raise SystemExit(f"rural scenarios missing: {missing}")


if __name__ == "__main__":
    main()
