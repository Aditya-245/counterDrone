"""Author the twelve shipped scenarios in `scenarios/`.

The scenarios themselves are the deliverable - this script is how they were
first laid out, with every parameter written out explicitly rather than rolled
by the generator. Re-running it reproduces the same twelve JSON files byte for
byte (it validates and sanity-checks each one first).

    .venv/Scripts/python backend/scripts/author_scenarios.py
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
    BehaviourKind,
    BehaviourSpec,
    CivilSector,
    DifficultyVector,
    EnvSpec,
    FormationKind,
    RoeSpec,
    Scenario,
    ScenarioEvent,
    SiteSpec,
    TerrainPreset,
    TerrainSpec,
    ThreatSpec,
    TimeOfDay,
)

OUT_DIR = REPO_ROOT / "scenarios"


def site(civil: list[CivilSector] | None = None, radius: float = 150.0) -> SiteSpec:
    """The defended site used by every shipped scenario."""
    return SiteSpec(
        protected_radius_m=radius,
        ring_distances_m=[400.0, 900.0, 1800.0, 3000.0],
        civil_sectors=civil or [],
    )


def terrain(
    preset: TerrainPreset, scale: float, roughness: float, seed: int, res: int = 192
) -> TerrainSpec:
    """Explicit terrain for one scenario."""
    return TerrainSpec(
        preset=preset,
        size_m=6000.0,
        resolution=res,
        scale_m=scale,
        roughness=roughness,
        seed=seed,
    )


def behaviour(
    kind: BehaviourKind,
    speed: float,
    terrain_following: bool = False,
    clearance: float = 22.0,
    separation: float = 20.0,
    terminal: float = 30.0,
) -> BehaviourSpec:
    """Explicit behaviour parameters."""
    return BehaviourSpec(
        kind=kind,
        speed_mps=speed,
        terminal_altitude_m=terminal,
        separation_m=separation,
        terrain_following=terrain_following,
        min_clearance_m=clearance,
        loiter_radius_m=140.0,
        loiter_period_s=48.0,
        orbit_radius_m=190.0,
        orbit_period_s=55.0,
        cohesion_weight=0.4,
        alignment_weight=0.3,
    )


SCENARIOS: list[Scenario] = []


def add(scenario: Scenario) -> None:
    """Register one scenario for writing."""
    SCENARIOS.append(scenario)


# --------------------------------------------------------------------------- #
# 1. Single FPV at dusk - the introductory drill
# --------------------------------------------------------------------------- #
add(
    Scenario(
        id="scn_solo_fpv_dusk",
        name="Single FPV at dusk",
        description=(
            "One fast FPV inbound on a single bearing at last light. The introductory "
            "drill: detect, identify, decide, and engage before it reaches the perimeter."
        ),
        seed=1001,
        duration_s=420,
        terrain=terrain(TerrainPreset.RURAL, 70.0, 0.45, 1001),
        environment=EnvSpec(
            time_of_day=TimeOfDay.DUSK,
            visibility_m=6500.0,
            rain=0.0,
            wind_speed_mps=3.0,
            wind_heading_deg=270.0,
        ),
        site=site(),
        sensors=default_sensors(),
        threats=[
            ThreatSpec(
                name="solo",
                entity_class="fpv_fast",
                label="hostile",
                count=1,
                spawn_t=20.0,
                spawn_window_s=0.0,
                spawn_bearing_deg=45.0,
                spawn_jitter_deg=0.0,
                behaviour=behaviour(BehaviourKind.TERMINAL_DIVE, 20.0),
                formation=FormationKind.LEADER_FOLLOWER,
                rf_active=True,
                hint_bias=0.0,
            )
        ],
        decoys=[],
        effectors=default_effectors(soft_ammo=8, hard_ammo=6),
        roe=RoeSpec(rule_set="STANDARD"),
        events=[],
        difficulty=DifficultyVector(
            swarm_size=0.05,
            approach_axes=0.1,
            time_pressure=0.15,
            sensor_degradation_level=0.0,
            decoy_ratio=0.0,
            visibility=0.7,
            night=0.0,
            ambiguity=0.0,
            simultaneity=0.1,
            roe_strictness=0.2,
        ),
        tags=["intro", "solo", "dusk", "fpv"],
    )
)


# --------------------------------------------------------------------------- #
# 2. Slow quad over urban terrain - occlusion and clutter
# --------------------------------------------------------------------------- #
add(
    Scenario(
        id="scn_urban_slow_quad",
        name="Slow quad over urban terrain",
        description=(
            "A hover-capable quad creeps over an urban site. Buildings block radar and "
            "the camera, and the target is slow enough to slip the Doppler filter."
        ),
        seed=1002,
        duration_s=540,
        terrain=terrain(TerrainPreset.URBAN, 80.0, 0.6, 1002),
        environment=EnvSpec(
            time_of_day=TimeOfDay.DAY,
            visibility_m=5500.0,
            rain=0.05,
            wind_speed_mps=4.0,
            wind_heading_deg=200.0,
        ),
        site=site(),
        sensors=default_sensors(),
        threats=[
            ThreatSpec(
                name="crawler",
                entity_class="quad_small",
                label="hostile",
                count=2,
                spawn_t=35.0,
                spawn_window_s=25.0,
                spawn_bearing_deg=120.0,
                spawn_jitter_deg=15.0,
                behaviour=behaviour(BehaviourKind.LOITER, 5.0, terminal=45.0),
                formation=FormationKind.LINE,
                rf_active=True,
                hint_bias=0.1,
            )
        ],
        decoys=[],
        effectors=default_effectors(soft_ammo=10, hard_ammo=5),
        roe=RoeSpec(rule_set="STANDARD"),
        events=[],
        difficulty=DifficultyVector(
            swarm_size=0.15,
            approach_axes=0.2,
            time_pressure=0.25,
            sensor_degradation_level=0.15,
            decoy_ratio=0.0,
            visibility=0.65,
            night=0.0,
            ambiguity=0.15,
            simultaneity=0.2,
            roe_strictness=0.3,
        ),
        tags=["urban", "slow", "occlusion", "quad"],
    )
)


# --------------------------------------------------------------------------- #
# 3. Night pincer swarm - the pressure test
# --------------------------------------------------------------------------- #
add(
    Scenario(
        id="scn_night_pincer_swarm",
        name="8-drone pincer swarm at night",
        description=(
            "Eight drones arrive simultaneously from two opposite bearings at night. "
            "Work the time-to-impact column or the perimeter will be overrun."
        ),
        seed=1003,
        duration_s=600,
        terrain=terrain(TerrainPreset.RURAL, 95.0, 0.5, 1003),
        environment=EnvSpec(
            time_of_day=TimeOfDay.NIGHT,
            visibility_m=5200.0,
            rain=0.1,
            wind_speed_mps=5.0,
            wind_heading_deg=90.0,
        ),
        site=site(),
        sensors=default_sensors(),
        threats=[
            ThreatSpec(
                name="pincer_a",
                entity_class="fpv_fast",
                label="hostile",
                count=4,
                spawn_t=45.0,
                spawn_window_s=0.0,
                spawn_bearing_deg=45.0,
                spawn_jitter_deg=6.0,
                behaviour=behaviour(BehaviourKind.SWARM, 22.0, separation=24.0),
                formation=FormationKind.PINCER,
                rf_active=True,
                hint_bias=0.15,
            ),
            ThreatSpec(
                name="pincer_b",
                entity_class="fpv_fast",
                label="hostile",
                count=4,
                spawn_t=45.0,
                spawn_window_s=0.0,
                spawn_bearing_deg=225.0,
                spawn_jitter_deg=6.0,
                behaviour=behaviour(BehaviourKind.SWARM, 22.0, separation=24.0),
                formation=FormationKind.PINCER,
                rf_active=True,
                hint_bias=0.15,
            ),
        ],
        decoys=[],
        effectors=default_effectors(soft_ammo=16, hard_ammo=12),
        roe=RoeSpec(rule_set="STANDARD"),
        events=[],
        difficulty=DifficultyVector(
            swarm_size=0.7,
            approach_axes=0.7,
            time_pressure=0.6,
            sensor_degradation_level=0.2,
            decoy_ratio=0.0,
            visibility=0.55,
            night=1.0,
            ambiguity=0.2,
            simultaneity=0.9,
            roe_strictness=0.35,
        ),
        tags=["night", "swarm", "pincer", "pressure"],
    )
)


# --------------------------------------------------------------------------- #
# 4. Decoy gauntlet with a friendly beacon dropout
# --------------------------------------------------------------------------- #
add(
    Scenario(
        id="scn_decoy_gauntlet",
        name="Decoy gauntlet with friendly beacon dropout",
        description=(
            "Birds, kites and one friendly UAV whose beacon drops out mid-scenario, "
            "alongside a genuine hostile. Do not engage a decoy to look decisive."
        ),
        seed=1004,
        duration_s=660,
        terrain=terrain(TerrainPreset.RURAL, 85.0, 0.55, 1004),
        environment=EnvSpec(
            time_of_day=TimeOfDay.DUSK,
            visibility_m=7000.0,
            rain=0.0,
            wind_speed_mps=6.0,
            wind_heading_deg=45.0,
        ),
        site=site(),
        sensors=default_sensors(),
        threats=[
            ThreatSpec(
                name="real_one",
                entity_class="quad_small",
                label="hostile",
                count=1,
                spawn_t=50.0,
                spawn_window_s=0.0,
                spawn_bearing_deg=200.0,
                spawn_jitter_deg=0.0,
                behaviour=behaviour(BehaviourKind.TERMINAL_DIVE, 14.0),
                formation=FormationKind.LEADER_FOLLOWER,
                rf_active=False,
                hint_bias=0.35,
            ),
            ThreatSpec(
                name="friendly_support",
                entity_class="friendly_uav",
                label="friendly",
                count=1,
                spawn_t=40.0,
                spawn_window_s=10.0,
                spawn_bearing_deg=210.0,
                spawn_jitter_deg=5.0,
                behaviour=behaviour(BehaviourKind.LOITER, 6.0, terminal=70.0),
                formation=FormationKind.LEADER_FOLLOWER,
                rf_active=True,
                beacon_active=True,
                hint_bias=0.2,
            ),
        ],
        decoys=[
            ThreatSpec(
                name="birds",
                entity_class="bird_flock",
                label="bird",
                count=3,
                spawn_t=60.0,
                spawn_window_s=40.0,
                spawn_bearing_deg=195.0,
                spawn_jitter_deg=45.0,
                behaviour=behaviour(BehaviourKind.SWARM, 12.0, separation=14.0),
                formation=FormationKind.SCATTER,
                rf_active=False,
                beacon_active=False,
                hint_bias=0.45,
            ),
            ThreatSpec(
                name="kites",
                entity_class="kite_balloon",
                label="civil",
                count=2,
                spawn_t=90.0,
                spawn_window_s=50.0,
                spawn_bearing_deg=185.0,
                spawn_jitter_deg=35.0,
                behaviour=behaviour(BehaviourKind.LOITER, 2.0, terminal=150.0),
                formation=FormationKind.SCATTER,
                rf_active=False,
                beacon_active=False,
                hint_bias=0.4,
            ),
        ],
        effectors=default_effectors(soft_ammo=8, hard_ammo=6),
        roe=RoeSpec(rule_set="STANDARD"),
        events=[
            ScenarioEvent(
                t=180.0,
                kind="sensor_partial",
                target="rf1",
                description="RF detector degraded; the friendly beacon drops out.",
            ),
            ScenarioEvent(
                t=240.0,
                kind="gps_denied",
                description="GPS denied: all sensor positions start to drift.",
            ),
        ],
        difficulty=DifficultyVector(
            swarm_size=0.15,
            approach_axes=0.25,
            time_pressure=0.3,
            sensor_degradation_level=0.45,
            decoy_ratio=0.8,
            visibility=0.6,
            night=0.0,
            ambiguity=0.7,
            simultaneity=0.3,
            roe_strictness=0.4,
        ),
        tags=["decoys", "friendly", "beacon-dropout", "trap"],
    )
)


# --------------------------------------------------------------------------- #
# 5. Radar failure mid-scenario
# --------------------------------------------------------------------------- #
add(
    Scenario(
        id="scn_radar_failure",
        name="Radar failure mid-scenario",
        description=(
            "The radar degrades at t=150 s and fails at t=260 s while two waves are "
            "inbound. The EO/IR turret and the RF detector become your only picture."
        ),
        seed=1005,
        duration_s=600,
        terrain=terrain(TerrainPreset.HILLS, 160.0, 0.6, 1005),
        environment=EnvSpec(
            time_of_day=TimeOfDay.DUSK,
            visibility_m=6000.0,
            rain=0.2,
            wind_speed_mps=7.0,
            wind_heading_deg=300.0,
        ),
        site=site(),
        sensors=default_sensors(),
        threats=[
            ThreatSpec(
                name="wave_a",
                entity_class="fpv_fast",
                label="hostile",
                count=3,
                spawn_t=40.0,
                spawn_window_s=30.0,
                spawn_bearing_deg=30.0,
                spawn_jitter_deg=20.0,
                behaviour=behaviour(BehaviourKind.ORBIT_THEN_STRIKE, 19.0),
                formation=FormationKind.LEADER_FOLLOWER,
                rf_active=True,
                hint_bias=0.2,
            ),
            ThreatSpec(
                name="wave_b",
                entity_class="quad_small",
                label="hostile",
                count=2,
                spawn_t=200.0,
                spawn_window_s=30.0,
                spawn_bearing_deg=300.0,
                spawn_jitter_deg=20.0,
                behaviour=behaviour(BehaviourKind.TERMINAL_DIVE, 13.0),
                formation=FormationKind.SCATTER,
                rf_active=True,
                hint_bias=0.25,
            ),
        ],
        decoys=[],
        effectors=default_effectors(soft_ammo=12, hard_ammo=8),
        roe=RoeSpec(rule_set="STANDARD"),
        events=[
            ScenarioEvent(
                t=150.0,
                kind="sensor_partial",
                target="radar1",
                description="Radar partially degraded",
            ),
            ScenarioEvent(
                t=260.0, kind="sensor_failure", target="radar1", description="Radar failed"
            ),
            ScenarioEvent(
                t=320.0, kind="sensor_restore", target="radar1", description="Radar restored"
            ),
        ],
        difficulty=DifficultyVector(
            swarm_size=0.45,
            approach_axes=0.55,
            time_pressure=0.5,
            sensor_degradation_level=0.8,
            decoy_ratio=0.0,
            visibility=0.55,
            night=0.0,
            ambiguity=0.25,
            simultaneity=0.5,
            roe_strictness=0.3,
        ),
        tags=["sensor-failure", "hills", "two-waves"],
    )
)


# --------------------------------------------------------------------------- #
# 6. RF-silent autonomous drones
# --------------------------------------------------------------------------- #
add(
    Scenario(
        id="scn_rf_silent_swarm",
        name="RF-silent autonomous drones",
        description=(
            "Autonomous drones with no emitter. The RF detector is blind to them, so "
            "identification must come from the camera - and soft-kill is far less "
            "effective against an RF-silent target."
        ),
        seed=1006,
        duration_s=600,
        terrain=terrain(TerrainPreset.RURAL, 75.0, 0.45, 1006),
        environment=EnvSpec(
            time_of_day=TimeOfDay.DUSK,
            visibility_m=7200.0,
            rain=0.0,
            wind_speed_mps=2.0,
            wind_heading_deg=10.0,
        ),
        site=site(),
        sensors=default_sensors(),
        threats=[
            ThreatSpec(
                name="silent_one",
                entity_class="quad_small",
                label="hostile",
                count=4,
                spawn_t=55.0,
                spawn_window_s=45.0,
                spawn_bearing_deg=150.0,
                spawn_jitter_deg=25.0,
                behaviour=behaviour(BehaviourKind.SWARM, 15.0, separation=22.0),
                formation=FormationKind.SCATTER,
                rf_active=False,
                hint_bias=0.3,
            )
        ],
        decoys=[],
        effectors=default_effectors(soft_ammo=8, hard_ammo=10),
        roe=RoeSpec(rule_set="STANDARD"),
        events=[],
        difficulty=DifficultyVector(
            swarm_size=0.4,
            approach_axes=0.35,
            time_pressure=0.35,
            sensor_degradation_level=0.1,
            decoy_ratio=0.15,
            visibility=0.65,
            night=0.0,
            ambiguity=0.4,
            simultaneity=0.45,
            roe_strictness=0.35,
        ),
        tags=["rf-silent", "autonomous", "soft-kill", "quad"],
    )
)


# --------------------------------------------------------------------------- #
# 7. Fog and rain
# --------------------------------------------------------------------------- #
add(
    Scenario(
        id="scn_fog_rain",
        name="Fog and rain",
        description=(
            "Visibility drops to a few hundred metres in heavy rain. Radar and RF still "
            "work; the optical picture is almost useless. Watch for clutter-driven "
            "false tracks."
        ),
        seed=1007,
        duration_s=660,
        terrain=terrain(TerrainPreset.RURAL, 60.0, 0.4, 1007),
        environment=EnvSpec(
            time_of_day=TimeOfDay.DAY,
            visibility_m=900.0,
            rain=0.85,
            wind_speed_mps=12.0,
            wind_heading_deg=240.0,
        ),
        site=site(),
        sensors=default_sensors(),
        threats=[
            ThreatSpec(
                name="rain_run",
                entity_class="fixed_wing",
                label="hostile",
                count=2,
                spawn_t=60.0,
                spawn_window_s=40.0,
                spawn_bearing_deg=330.0,
                spawn_jitter_deg=20.0,
                behaviour=behaviour(BehaviourKind.ORBIT_THEN_STRIKE, 30.0, terminal=60.0),
                formation=FormationKind.LINE,
                rf_active=True,
                hint_bias=0.25,
            ),
            ThreatSpec(
                name="rain_follow",
                entity_class="fpv_fast",
                label="hostile",
                count=3,
                spawn_t=170.0,
                spawn_window_s=50.0,
                spawn_bearing_deg=20.0,
                spawn_jitter_deg=25.0,
                behaviour=behaviour(BehaviourKind.SWARM, 20.0, separation=22.0),
                formation=FormationKind.WEDGE,
                rf_active=True,
                hint_bias=0.3,
            ),
        ],
        decoys=[
            ThreatSpec(
                name="rain_birds",
                entity_class="bird_flock",
                label="bird",
                count=4,
                spawn_t=140.0,
                spawn_window_s=80.0,
                spawn_bearing_deg=15.0,
                spawn_jitter_deg=50.0,
                behaviour=behaviour(BehaviourKind.SWARM, 11.0, separation=12.0),
                formation=FormationKind.SCATTER,
                rf_active=False,
                beacon_active=False,
                hint_bias=0.5,
            )
        ],
        effectors=default_effectors(soft_ammo=12, hard_ammo=8),
        roe=RoeSpec(rule_set="STANDARD"),
        events=[],
        difficulty=DifficultyVector(
            swarm_size=0.45,
            approach_axes=0.5,
            time_pressure=0.45,
            sensor_degradation_level=0.2,
            decoy_ratio=0.4,
            visibility=0.05,
            night=0.0,
            ambiguity=0.45,
            simultaneity=0.5,
            roe_strictness=0.35,
        ),
        tags=["fog", "rain", "low-visibility", "clutter"],
    )
)


# --------------------------------------------------------------------------- #
# 8. Hills, terrain-hugging approach
# --------------------------------------------------------------------------- #
add(
    Scenario(
        id="scn_hills_terrain_hug",
        name="Hills terrain-hugging approach",
        description=(
            "A group stays below the ridgeline, using the terrain as cover. Radar "
            "loses them behind the crests; only the top of the crest is visible."
        ),
        seed=1008,
        duration_s=600,
        terrain=terrain(TerrainPreset.HILLS, 230.0, 0.8, 1008),
        environment=EnvSpec(
            time_of_day=TimeOfDay.DUSK,
            visibility_m=6000.0,
            rain=0.1,
            wind_speed_mps=8.0,
            wind_heading_deg=180.0,
        ),
        site=site(),
        sensors=default_sensors(),
        threats=[
            ThreatSpec(
                name="ridge_run",
                entity_class="fpv_fast",
                label="hostile",
                count=5,
                spawn_t=50.0,
                spawn_window_s=60.0,
                spawn_bearing_deg=75.0,
                spawn_jitter_deg=30.0,
                behaviour=behaviour(
                    BehaviourKind.SWARM,
                    18.0,
                    terrain_following=True,
                    clearance=14.0,
                    separation=18.0,
                ),
                formation=FormationKind.LEADER_FOLLOWER,
                rf_active=True,
                hint_bias=0.3,
            )
        ],
        decoys=[],
        effectors=default_effectors(soft_ammo=14, hard_ammo=10),
        roe=RoeSpec(rule_set="STANDARD"),
        events=[],
        difficulty=DifficultyVector(
            swarm_size=0.55,
            approach_axes=0.3,
            time_pressure=0.4,
            sensor_degradation_level=0.25,
            decoy_ratio=0.1,
            visibility=0.6,
            night=0.0,
            ambiguity=0.35,
            simultaneity=0.4,
            roe_strictness=0.35,
        ),
        tags=["hills", "terrain-hugging", "masking"],
    )
)


# --------------------------------------------------------------------------- #
# 9. Overlapping waves
# --------------------------------------------------------------------------- #
add(
    Scenario(
        id="scn_overlapping_waves",
        name="Overlapping waves",
        description=(
            "Three waves from three bearings, deliberately overlapping so that no two "
            "contacts reach the perimeter at the same time. Prioritisation is the "
            "whole exercise here."
        ),
        seed=1009,
        duration_s=720,
        terrain=terrain(TerrainPreset.RURAL, 90.0, 0.5, 1009),
        environment=EnvSpec(
            time_of_day=TimeOfDay.DUSK,
            visibility_m=6800.0,
            rain=0.05,
            wind_speed_mps=4.0,
            wind_heading_deg=150.0,
        ),
        site=site(),
        sensors=default_sensors(),
        threats=[
            ThreatSpec(
                name="w1",
                entity_class="fpv_fast",
                label="hostile",
                count=3,
                spawn_t=40.0,
                spawn_window_s=20.0,
                spawn_bearing_deg=0.0,
                spawn_jitter_deg=12.0,
                behaviour=behaviour(BehaviourKind.TERMINAL_DIVE, 21.0),
                formation=FormationKind.WEDGE,
                rf_active=True,
                hint_bias=0.2,
            ),
            ThreatSpec(
                name="w2",
                entity_class="quad_small",
                label="hostile",
                count=3,
                spawn_t=95.0,
                spawn_window_s=20.0,
                spawn_bearing_deg=120.0,
                spawn_jitter_deg=12.0,
                behaviour=behaviour(BehaviourKind.SWARM, 14.0, separation=20.0),
                formation=FormationKind.SCATTER,
                rf_active=True,
                hint_bias=0.2,
            ),
            ThreatSpec(
                name="w3",
                entity_class="fixed_wing",
                label="hostile",
                count=2,
                spawn_t=150.0,
                spawn_window_s=20.0,
                spawn_bearing_deg=240.0,
                spawn_jitter_deg=12.0,
                behaviour=behaviour(BehaviourKind.ORBIT_THEN_STRIKE, 29.0, terminal=80.0),
                formation=FormationKind.LINE,
                rf_active=True,
                hint_bias=0.2,
            ),
        ],
        decoys=[],
        effectors=default_effectors(soft_ammo=16, hard_ammo=12),
        roe=RoeSpec(rule_set="STANDARD"),
        events=[],
        difficulty=DifficultyVector(
            swarm_size=0.5,
            approach_axes=0.7,
            time_pressure=0.55,
            sensor_degradation_level=0.2,
            decoy_ratio=0.1,
            visibility=0.6,
            night=0.0,
            ambiguity=0.25,
            simultaneity=0.85,
            roe_strictness=0.35,
        ),
        tags=["waves", "prioritisation", "saturation"],
    )
)


# --------------------------------------------------------------------------- #
# 10. Friendly look-alike trap, STRICT ROE
# --------------------------------------------------------------------------- #
add(
    Scenario(
        id="scn_friendly_lookalike",
        name="Friendly look-alike trap (STRICT ROE)",
        description=(
            "Two near-identical contacts arrive together; one is a friendly UAV with a "
            "beacon, one is hostile. STRICT ROE is in force: nothing is engaged without "
            "a positive identification."
        ),
        seed=1010,
        duration_s=600,
        terrain=terrain(TerrainPreset.RURAL, 80.0, 0.45, 1010),
        environment=EnvSpec(
            time_of_day=TimeOfDay.DUSK,
            visibility_m=7000.0,
            rain=0.0,
            wind_speed_mps=3.0,
            wind_heading_deg=60.0,
        ),
        site=site(),
        sensors=default_sensors(),
        threats=[
            ThreatSpec(
                name="impostor",
                entity_class="quad_small",
                label="hostile",
                count=2,
                spawn_t=60.0,
                spawn_window_s=10.0,
                spawn_bearing_deg=100.0,
                spawn_jitter_deg=8.0,
                behaviour=behaviour(BehaviourKind.SWARM, 14.0, separation=16.0, terminal=60.0),
                formation=FormationKind.LINE,
                rf_active=True,
                hint_bias=0.5,
            ),
            ThreatSpec(
                name="friendly_lookalike",
                entity_class="friendly_uav",
                label="friendly",
                count=2,
                spawn_t=65.0,
                spawn_window_s=10.0,
                spawn_bearing_deg=105.0,
                spawn_jitter_deg=8.0,
                behaviour=behaviour(BehaviourKind.LOITER, 8.0, terminal=70.0),
                formation=FormationKind.LINE,
                rf_active=True,
                beacon_active=True,
                hint_bias=0.35,
            ),
        ],
        decoys=[],
        effectors=default_effectors(soft_ammo=8, hard_ammo=4),
        roe=RoeSpec(
            rule_set="STRICT",
            require_id_before_engage=True,
            allowed_engage_labels=["hostile"],
            friend_or_civil_warning_required=True,
            collateral_sector_tolerance=0.0,
            min_track_quality=0.25,
            require_warning_before_engage=True,
        ),
        events=[
            ScenarioEvent(
                t=200.0,
                kind="sensor_partial",
                target="rf1",
                description="RF detector degraded; the friendly beacon drops out.",
            )
        ],
        difficulty=DifficultyVector(
            swarm_size=0.35,
            approach_axes=0.25,
            time_pressure=0.35,
            sensor_degradation_level=0.4,
            decoy_ratio=0.2,
            visibility=0.65,
            night=0.0,
            ambiguity=0.75,
            simultaneity=0.6,
            roe_strictness=0.95,
        ),
        tags=["strict-roe", "fratricide-risk", "trap", "friendly"],
    )
)


# --------------------------------------------------------------------------- #
# 11. Civil aircraft over-flight
# --------------------------------------------------------------------------- #
add(
    Scenario(
        id="scn_civil_overflight",
        name="Civil aircraft over-flight",
        description=(
            "A high, steady civil overflight crosses the defended airspace while a "
            "single hostile works underneath it. The civil contact is the loudest, "
            "brightest track on your scope and is not a target."
        ),
        seed=1011,
        duration_s=540,
        terrain=terrain(TerrainPreset.RURAL, 85.0, 0.5, 1011),
        environment=EnvSpec(
            time_of_day=TimeOfDay.DAY,
            visibility_m=8500.0,
            rain=0.0,
            wind_speed_mps=3.0,
            wind_heading_deg=210.0,
        ),
        site=site(
            civil=[
                CivilSector(
                    bearing_deg=90.0,
                    half_width_deg=25.0,
                    inner_m=0.0,
                    outer_m=6000.0,
                    label="civil airway",
                )
            ]
        ),
        sensors=default_sensors(),
        threats=[
            ThreatSpec(
                name="lone_walker",
                entity_class="fpv_fast",
                label="hostile",
                count=1,
                spawn_t=50.0,
                spawn_window_s=0.0,
                spawn_bearing_deg=200.0,
                spawn_jitter_deg=0.0,
                behaviour=behaviour(BehaviourKind.TERMINAL_DIVE, 19.0),
                formation=FormationKind.LEADER_FOLLOWER,
                rf_active=True,
                hint_bias=0.2,
            ),
            ThreatSpec(
                name="civil_traffic",
                entity_class="civil_aircraft",
                label="civil",
                count=1,
                spawn_t=70.0,
                spawn_window_s=0.0,
                spawn_bearing_deg=90.0,
                spawn_jitter_deg=0.0,
                behaviour=behaviour(BehaviourKind.TRANSIT, 46.0, terminal=950.0),
                formation=FormationKind.LINE,
                rf_active=True,
                beacon_active=True,
                hint_bias=0.1,
            ),
        ],
        decoys=[],
        effectors=default_effectors(soft_ammo=8, hard_ammo=6),
        roe=RoeSpec(rule_set="STANDARD"),
        events=[],
        difficulty=DifficultyVector(
            swarm_size=0.1,
            approach_axes=0.2,
            time_pressure=0.25,
            sensor_degradation_level=0.1,
            decoy_ratio=0.3,
            visibility=0.75,
            night=0.0,
            ambiguity=0.35,
            simultaneity=0.35,
            roe_strictness=0.4,
        ),
        tags=["civil", "overflight", "collateral", "civil-sector"],
    )
)


# --------------------------------------------------------------------------- #
# 12. Everything, at once
# --------------------------------------------------------------------------- #
add(
    Scenario(
        id="scn_everything_stress",
        name="Mixed stress test",
        description=(
            "The end-to-end test: night, rain, hills, two hostile waves from different "
            "bearings, a decoy flock, a civil overflight, a radar degradation and a "
            "GPS-denial window. Nothing here is a trick; everything is at once."
        ),
        seed=1012,
        duration_s=900,
        terrain=terrain(TerrainPreset.HILLS, 190.0, 0.75, 1012, res=224),
        environment=EnvSpec(
            time_of_day=TimeOfDay.NIGHT,
            visibility_m=2400.0,
            rain=0.6,
            wind_speed_mps=14.0,
            wind_heading_deg=285.0,
        ),
        site=site(
            radius=180.0,
            civil=[
                CivilSector(
                    bearing_deg=250.0,
                    half_width_deg=22.0,
                    inner_m=0.0,
                    outer_m=3000.0,
                    label="settlement",
                )
            ],
        ),
        sensors=default_sensors(),
        threats=[
            ThreatSpec(
                name="main_swarm",
                entity_class="fpv_fast",
                label="hostile",
                count=6,
                spawn_t=60.0,
                spawn_window_s=35.0,
                spawn_bearing_deg=20.0,
                spawn_jitter_deg=22.0,
                behaviour=behaviour(
                    BehaviourKind.SWARM,
                    23.0,
                    terrain_following=True,
                    clearance=18.0,
                    separation=22.0,
                ),
                formation=FormationKind.PINCER,
                rf_active=True,
                hint_bias=0.4,
            ),
            ThreatSpec(
                name="second_wave",
                entity_class="quad_small",
                label="hostile",
                count=4,
                spawn_t=260.0,
                spawn_window_s=45.0,
                spawn_bearing_deg=160.0,
                spawn_jitter_deg=25.0,
                behaviour=behaviour(BehaviourKind.ORBIT_THEN_STRIKE, 15.0, terminal=50.0),
                formation=FormationKind.SCATTER,
                rf_active=False,
                hint_bias=0.45,
            ),
            ThreatSpec(
                name="civil_over",
                entity_class="civil_aircraft",
                label="civil",
                count=1,
                spawn_t=300.0,
                spawn_window_s=0.0,
                spawn_bearing_deg=250.0,
                spawn_jitter_deg=0.0,
                behaviour=behaviour(BehaviourKind.TRANSIT, 45.0, terminal=900.0),
                formation=FormationKind.LINE,
                rf_active=True,
                beacon_active=True,
                hint_bias=0.1,
            ),
        ],
        decoys=[
            ThreatSpec(
                name="flock",
                entity_class="bird_flock",
                label="bird",
                count=5,
                spawn_t=150.0,
                spawn_window_s=120.0,
                spawn_bearing_deg=30.0,
                spawn_jitter_deg=60.0,
                behaviour=behaviour(BehaviourKind.SWARM, 13.0, separation=12.0),
                formation=FormationKind.SCATTER,
                rf_active=False,
                beacon_active=False,
                hint_bias=0.6,
            ),
            ThreatSpec(
                name="balloons",
                entity_class="kite_balloon",
                label="civil",
                count=2,
                spawn_t=320.0,
                spawn_window_s=90.0,
                spawn_bearing_deg=150.0,
                spawn_jitter_deg=40.0,
                behaviour=behaviour(BehaviourKind.LOITER, 2.0, terminal=160.0),
                formation=FormationKind.SCATTER,
                rf_active=False,
                beacon_active=False,
                hint_bias=0.5,
            ),
        ],
        effectors=default_effectors(soft_ammo=20, hard_ammo=14),
        roe=RoeSpec(rule_set="STANDARD"),
        events=[
            ScenarioEvent(
                t=220.0,
                kind="sensor_partial",
                target="radar1",
                description="Radar degraded by rain",
            ),
            ScenarioEvent(
                t=340.0, kind="sensor_failure", target="ac1", description="Acoustic array lost"
            ),
            ScenarioEvent(
                t=480.0, kind="gps_denied", description="GPS denied: sensor positions drift"
            ),
            ScenarioEvent(t=560.0, kind="gps_restored", description="GPS restored"),
            ScenarioEvent(
                t=600.0, kind="sensor_restore", target="radar1", description="Radar restored"
            ),
        ],
        difficulty=DifficultyVector(
            swarm_size=0.8,
            approach_axes=0.8,
            time_pressure=0.7,
            sensor_degradation_level=0.85,
            decoy_ratio=0.6,
            visibility=0.15,
            night=1.0,
            ambiguity=0.65,
            simultaneity=0.8,
            roe_strictness=0.5,
        ),
        tags=["stress", "night", "rain", "hills", "swarm", "decoys", "civil"],
    )
)


def main() -> int:
    """Write every scenario, validating as we go."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for scenario in SCENARIOS:
        validated = Scenario.model_validate(scenario.model_dump(mode="python"))
        path = OUT_DIR / f"{validated.id}.json"
        path.write_text(
            json.dumps(validated.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    print(f"{len(SCENARIOS)} scenarios written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
