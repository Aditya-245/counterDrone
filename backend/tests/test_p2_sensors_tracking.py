"""P2 - sensors and tracking."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.core.engine import SimEngine
from app.core.entities import params_for
from app.core.generator import generate
from app.core.schemas import (
    DegradationState,
    DifficultyVector,
    EntityClass,
    EnvSpec,
    LabelType,
    Scenario,
    SensorSpec,
    SensorType,
    TerrainPreset,
    TerrainSpec,
    ThreatSpec,
    Vec3,
)
from app.core.sensors import (
    AcousticSensor,
    EoIrSensor,
    RadarSensor,
    RfSensor,
    SensorContext,
    first_detectable,
)
from app.core.terrain import build_terrain
from app.core.tracking import Detection, TrackerConfig, TrackTracker
from app.core.world import EntityState, new_world

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _entity(
    eid: str = "E1",
    cls: EntityClass = EntityClass.FPV_FAST,
    label: LabelType = LabelType.HOSTILE,
    x: float = 1500.0,
    y: float = 0.0,
    z: float = 120.0,
    vx: float = -15.0,
    rf_active: bool = True,
) -> EntityState:
    """A single ground-truth entity placed by hand."""
    return EntityState(
        id=eid,
        entity_class=cls,
        label=label,
        wave_name="w",
        member_index=0,
        position=Vec3(x=x, y=y, z=z),
        velocity=Vec3(x=vx, y=0.0, z=0.0),
        spawn_t=0.0,
        rf_active=rf_active,
        beacon_active=False,
    )


def _world(entity: EntityState, env: EnvSpec | None = None, radar_range: float = 4000.0) -> tuple:
    """Build a throwaway one-entity world plus the sensor context that reads it."""
    scenario = Scenario(
        id="unit",
        name="unit test",
        seed=1,
        duration_s=60,
        terrain=TerrainSpec(preset=TerrainPreset.RURAL, resolution=96, scale_m=60.0, seed=3),
        environment=env or EnvSpec(time_of_day="day", visibility_m=8000.0, rain=0.0),
        sensors=[
            SensorSpec(
                id="radar1", type=SensorType.RADAR, min_range_m=0.0, max_range_m=radar_range
            ),
            SensorSpec(id="eo1", type=SensorType.EO_IR, fov_deg=90.0, max_range_m=2500.0),
            SensorSpec(id="rf1", type=SensorType.RF, max_range_m=3000.0),
            SensorSpec(id="ac1", type=SensorType.ACOUSTIC, max_range_m=700.0),
        ],
        effectors=[{"id": "e1", "kind": "soft", "max_range_m": 1000.0, "ammunition": 5}],
        threats=[
            ThreatSpec(
                name="w",
                entity_class=entity.entity_class,
                label=entity.label,
                count=1,
                spawn_t=0.0,
                spawn_bearing_deg=0.0,
                spawn_jitter_deg=0.0,
                rf_active=entity.rf_active,
            )
        ],
    )
    world = new_world(scenario, dt=0.1)
    world.entities[entity.id] = entity
    ctx = SensorContext(
        terrain=build_terrain(scenario.terrain),
        environment=scenario.environment,
        scenario=scenario,
    )
    return world, ctx, scenario


# --------------------------------------------------------------------------- #
# Radar
# --------------------------------------------------------------------------- #


def test_radar_detection_probability_falls_with_range() -> None:
    """Pd decreases monotonically as the target gets further away."""
    entity = _entity()
    world, ctx, _ = _world(entity)
    radar = RadarSensor(world.sensors["radar1"].spec, ctx)
    world.sensors["radar1"] = world.sensors["radar1"]
    prev = 1.0
    for r in (200.0, 800.0, 1600.0, 2400.0, 3200.0, 3900.0):
        entity.position = Vec3(x=r, y=0.0, z=120.0)
        pd = radar.detection_probability(entity, world)
        assert pd <= prev + 1e-9, f"Pd rose at {r} m"
        prev = pd
    assert prev < 0.15


def test_radar_detection_probability_rises_with_rcs() -> None:
    """A bigger abstract scatter parameter is easier to detect at equal range."""
    big = _entity(cls=EntityClass.CIVIL_AIRCRAFT, x=2000.0)
    small = _entity(cls=EntityClass.BIRD_FLOCK, x=2000.0)
    world, ctx, _ = _world(big)
    radar = RadarSensor(world.sensors["radar1"].spec, ctx)
    assert radar.detection_probability(big, world) > radar.detection_probability(small, world)


def test_radar_detection_probability_drops_in_fog_and_rain() -> None:
    """Visibility and rain both reduce radar Pd (radar is affected, but less)."""
    clear = _entity(x=2500.0)
    world_clear, ctx_clear, _ = _world(clear, EnvSpec(visibility_m=9000.0, rain=0.0))
    radar_clear = RadarSensor(world_clear.sensors["radar1"].spec, ctx_clear)
    pd_clear = radar_clear.detection_probability(clear, world_clear)

    bad = _entity(x=2500.0)
    world_bad, ctx_bad, _ = _world(bad, EnvSpec(visibility_m=900.0, rain=0.9))
    radar_bad = RadarSensor(world_bad.sensors["radar1"].spec, ctx_bad)
    pd_bad = radar_bad.detection_probability(bad, world_bad)

    assert pd_bad < pd_clear
    assert params_for(EntityClass.FPV_FAST).rcs_m2 < params_for(EntityClass.CIVIL_AIRCRAFT).rcs_m2


def test_radar_observation_matches_declared_probability() -> None:
    """Monte-Carlo: observed detection rate tracks the declared Pd."""
    entity = _entity(x=1200.0)
    world, ctx, _ = _world(entity)
    radar = RadarSensor(world.sensors["radar1"].spec, ctx)
    pd = radar.detection_probability(entity, world)
    rng = np.random.default_rng(11)
    hits = sum(1 for _ in range(2000) if radar.observe(world, rng))
    observed = hits / 2000.0
    assert observed == pytest.approx(pd, abs=0.06)


def test_radar_emits_false_alarms_from_clutter() -> None:
    """A clutter model produces some plots and more of them in rain."""
    entity = _entity(x=3000.0, rf_active=False)
    world, ctx, _ = _world(entity, EnvSpec(visibility_m=9000.0, rain=0.0))
    radar = RadarSensor(world.sensors["radar1"].spec, ctx)
    rng = np.random.default_rng(5)
    false_clear = sum(1 for _ in range(400) for d in radar.observe(world, rng) if d.entity_id == "")
    entity2 = _entity(x=3000.0, rf_active=False)
    world2, ctx2, _ = _world(entity2, EnvSpec(visibility_m=9000.0, rain=0.9))
    radar2 = RadarSensor(world2.sensors["radar1"].spec, ctx2)
    false_rain = sum(
        1 for _ in range(400) for d in radar2.observe(world2, rng) if d.entity_id == ""
    )
    assert false_clear > 0
    assert false_rain >= false_clear


# --------------------------------------------------------------------------- #
# EO/IR
# --------------------------------------------------------------------------- #


def test_eo_ir_needs_the_camera_pointed_at_the_target() -> None:
    """FOV steering gates detection."""
    entity = _entity(x=900.0, y=0.0, z=80.0)
    world, ctx, _ = _world(entity)
    eo = EoIrSensor(world.sensors["eo1"].spec, ctx)
    assert eo.detection_probability(entity, world) > 0.0
    world.sensors["eo1"].azimuth_deg = 180.0
    assert eo.detection_probability(entity, world) == 0.0


def test_eo_ir_is_better_at_night_than_in_heavy_rain() -> None:
    """IR helps at night; heavy rain hurts."""
    night = _entity(x=800.0, y=0.0, z=80.0)
    world_n, ctx_n, _ = _world(night, EnvSpec(time_of_day="night", visibility_m=7000.0, rain=0.0))
    pd_night = EoIrSensor(world_n.sensors["eo1"].spec, ctx_n).detection_probability(night, world_n)

    storm = _entity(x=800.0, y=0.0, z=80.0)
    world_s, ctx_s, _ = _world(storm, EnvSpec(time_of_day="night", visibility_m=700.0, rain=0.95))
    pd_storm = EoIrSensor(world_s.sensors["eo1"].spec, ctx_s).detection_probability(storm, world_s)

    assert pd_night > pd_storm


def test_eo_ir_classifies_only_at_close_range() -> None:
    """Class evidence is strong up close and vague far away."""
    rng = np.random.default_rng(2)

    def peak_evidence(x: float, z: float) -> float:
        ent = _entity(x=x, y=0.0, z=z)
        world, ctx, _ = _world(ent)
        eo = EoIrSensor(world.sensors["eo1"].spec, ctx)
        best = 0.0
        for _ in range(40):
            dets = eo.observe(world, rng)
            for d in dets:
                best = max(best, sum(d.class_evidence.values()))
        return best

    strong = peak_evidence(250.0, 90.0)
    weak = peak_evidence(1600.0, 160.0)
    assert strong > weak
    assert strong > 0.5


# --------------------------------------------------------------------------- #
# RF
# --------------------------------------------------------------------------- #


def test_rf_never_detects_an_rf_silent_entity() -> None:
    """An autonomous, RF-silent drone is invisible to the RF detector."""
    silent = _entity(x=1500.0, rf_active=False)
    world_s, ctx_s, _ = _world(silent)
    rf_s = RfSensor(world_s.sensors["rf1"].spec, ctx_s)
    assert rf_s.detection_probability(silent, world_s) == 0.0
    rng = np.random.default_rng(4)
    assert rf_s.observe(world_s, rng) == []

    loud = _entity(x=1500.0, rf_active=True)
    world_l, ctx_l, _ = _world(loud)
    rf_l = RfSensor(world_l.sensors["rf1"].spec, ctx_l)
    assert rf_l.detection_probability(loud, world_l) > 0.0
    assert any(rf_l.observe(world_l, rng) for _ in range(20))


def test_rf_reports_coarse_position_only() -> None:
    """RF fixes are bearing-limited: large position error by design."""
    entity = _entity(x=2500.0)
    world, ctx, _ = _world(entity)
    rf = RfSensor(world.sensors["rf1"].spec, ctx)
    assert rf.position_sigma_m > 100.0


# --------------------------------------------------------------------------- #
# Acoustic
# --------------------------------------------------------------------------- #


def test_acoustic_is_short_range_and_wind_limited() -> None:
    """Acoustic reach is short and shrinks as the wind picks up."""
    entity = _entity(x=500.0, z=60.0)
    world_c, ctx_c, _ = _world(entity, EnvSpec(wind_speed_mps=1.0))
    pd_calm = AcousticSensor(world_c.sensors["ac1"].spec, ctx_c).detection_probability(
        entity, world_c
    )
    world_w, ctx_w, _ = _world(entity, EnvSpec(wind_speed_mps=25.0))
    pd_windy = AcousticSensor(world_w.sensors["ac1"].spec, ctx_w).detection_probability(
        entity, world_w
    )
    assert pd_calm > pd_windy
    entity_far = _entity(x=6000.0, z=60.0)
    assert pd_calm > AcousticSensor(world_c.sensors["ac1"].spec, ctx_c).detection_probability(
        entity_far, world_c
    )


# --------------------------------------------------------------------------- #
# Degradation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("state", "expect_zero"),
    [
        (DegradationState.NONE, False),
        (DegradationState.PARTIAL, False),
        (DegradationState.FAILED, True),
    ],
)
def test_degradation_states_gate_output(state: DegradationState, expect_zero: bool) -> None:
    """A failed sensor produces nothing; a partial one produces less."""
    entity = _entity(x=1200.0)
    world, ctx, _ = _world(entity)
    radar = RadarSensor(world.sensors["radar1"].spec, ctx)
    world.sensors["radar1"].state = state
    rng = np.random.default_rng(9)
    if expect_zero:
        assert radar.observe(world, rng) == []
    else:
        assert radar.detection_probability(entity, world) > 0.0


def test_first_detectable_is_noise_free_and_monotone_in_range() -> None:
    """The ground-truth detectability timeline respects range and RF silence."""
    from app.core.sensors import FIRST_DETECTABLE_PD

    near = _entity(x=700.0)
    world_n, ctx_n, _ = _world(near)
    radar_n = RadarSensor(world_n.sensors["radar1"].spec, ctx_n)
    assert first_detectable([radar_n], world_n, near)

    edge = _entity(x=3990.0)
    world_f, ctx_f, _ = _world(edge)
    radar_f = RadarSensor(world_f.sensors["radar1"].spec, ctx_f)
    assert radar_f.detection_probability(edge, world_f) < FIRST_DETECTABLE_PD
    assert not first_detectable([radar_f], world_f, edge)

    # An RF-silent target is never detectable by the RF detector, at any range.
    silent = _entity(x=800.0, rf_active=False)
    world_s, ctx_s, _ = _world(silent)
    rf_s = RfSensor(world_s.sensors["rf1"].spec, ctx_s)
    assert not first_detectable([rf_s], world_s, silent)


# --------------------------------------------------------------------------- #
# Kalman filter
# --------------------------------------------------------------------------- #


def test_kalman_converges_on_a_straight_line_target() -> None:
    """A CV filter tracks a constant-velocity target and its error shrinks."""
    from app.core.tracking import KalmanFilter

    kf = KalmanFilter(0.1)
    kf.x[:3] = [1000.0, 0.0, 100.0]
    kf.x[3:] = [-20.0, 0.0, 0.0]
    kf.P = np.diag([2500.0, 2500.0, 2500.0, 400.0, 400.0, 400.0])
    rng = np.random.default_rng(3)
    truth = np.array([1000.0, 0.0, 100.0])
    for _ in range(400):
        truth = truth + np.array([-2.0, 0.0, 0.0])
        kf.predict()
        z = truth + rng.normal(0.0, 15.0, size=3)
        kf.update_position(z, np.eye(3) * 225.0)
    err = float(np.linalg.norm(kf.position - truth))
    assert err < 12.0, f"converged error {err:.1f} m is too large"
    assert kf.position_sigma < 15.0


def test_track_continuity_on_a_synthetic_target() -> None:
    """One track is maintained for a straight-line target over a whole run."""
    tracker = TrackTracker(TrackerConfig(), dt=0.1)
    truth = np.array([-2500.0, 0.0, 120.0])
    vel = np.array([-18.0, 0.0, 0.0])
    rng = np.random.default_rng(21)
    ids: set[str] = set()
    for i in range(600):
        truth = truth + vel * 0.1
        tracker.update(
            [
                Detection(
                    sensor_id="radar1",
                    sensor_type="radar",
                    entity_id="TGT",
                    x=float(truth[0] + rng.normal(0, 8.0)),
                    y=float(truth[1] + rng.normal(0, 8.0)),
                    z=float(truth[2] + rng.normal(0, 8.0)),
                    t=i * 0.1,
                    range_m=float(np.linalg.norm(truth)),
                    bearing_deg=0.0,
                    altitude_m=120.0,
                    strength=1.0,
                )
            ],
            t=i * 0.1,
        )
        ids |= {tid for tid, trk in tracker.state.tracks.items() if trk.entity_hint() == "TGT"}
    assert len(ids) == 1, f"the track was fragmented into {len(ids)} ids"
    final = tracker.state.tracks[ids.pop()]
    assert final.confirmed
    assert float(np.linalg.norm(final.position - truth)) < 40.0


def test_entity_hint_is_stable_and_ignores_blank_ids() -> None:
    """`entity_hint` must not depend on set iteration order.

    Every sensor reports the same object under its own ground-truth id, and a
    clutter return carries no id at all. The hint then feeds the oracle's view of
    *which entity* a track is, so a hash-ordered tie-break or a stray blank id
    makes the same seeded run score differently from one process to the next.
    """
    tracker = TrackTracker(TrackerConfig(), dt=0.1)
    truth = np.array([-2500.0, 0.0, 120.0])
    vel = np.array([-18.0, 0.0, 0.0])
    rng = np.random.default_rng(17)
    for i in range(30):
        truth = truth + vel * 0.1
        dets = [
            Detection(
                sensor_id=sid,
                sensor_type=stype,
                entity_id=eid,
                x=float(truth[0] + rng.normal(0, 4.0)),
                y=float(truth[1] + rng.normal(0, 4.0)),
                z=float(truth[2] + rng.normal(0, 4.0)),
                t=i * 0.1,
                range_m=float(np.linalg.norm(truth)),
                bearing_deg=0.0,
                altitude_m=120.0,
                strength=1.0,
            )
            for sid, stype, eid in (
                ("radar1", "radar", "zulu"),
                ("eo1", "eo_ir", "alpha"),
                ("rf1", "rf", ""),
            )
        ]
        tracker.update(dets, t=i * 0.1)

    confirmed = [trk for trk in tracker.state.tracks.values() if trk.confirmed]
    assert len(confirmed) == 1, f"expected one track, got {len(confirmed)}"
    track = confirmed[0]
    assert "" not in track.entity_ids, "a clutter return named an entity"
    assert set(track.entity_ids) == {"zulu", "alpha"}

    # A perfect tie breaks on the smallest id, whatever order the ids were
    # recorded or visited in.
    assert track.entity_hint() == "alpha"
    track.entity_ids = list(reversed(track.entity_ids))
    assert track.entity_hint() == "alpha"


def test_false_track_rate_is_bounded() -> None:
    """With only clutter and no targets, confirmed tracks stay rare."""
    tracker = TrackTracker(TrackerConfig(), dt=0.1)
    rng = np.random.default_rng(31)
    confirmed_steps = 0
    steps = 400
    for i in range(steps):
        dets = []
        for _ in range(int(rng.poisson(0.6))):
            dets.append(
                Detection(
                    sensor_id="radar1",
                    sensor_type="radar",
                    entity_id="",
                    x=float(rng.uniform(-3000, 3000)),
                    y=float(rng.uniform(-3000, 3000)),
                    z=float(rng.uniform(30, 400)),
                    t=i * 0.1,
                    range_m=1000.0,
                    bearing_deg=0.0,
                    altitude_m=100.0,
                    strength=0.25,
                )
            )
        tracker.update(dets, t=i * 0.1)
        confirmed_steps += sum(1 for trk in tracker.state.tracks.values() if trk.confirmed)
    assert confirmed_steps / steps < 1.0


def test_fusion_merges_sensors_and_records_contributors() -> None:
    """A target seen by two sensors produces one track listing both."""
    tracker = TrackTracker(TrackerConfig(), dt=0.1)
    rng = np.random.default_rng(7)
    for i in range(120):
        base_x = -2000.0 + i * 2.0
        dets = [
            Detection(
                sensor_id=sid,
                sensor_type=kind,
                entity_id="TGT",
                x=base_x + rng.normal(0, 6.0),
                y=rng.normal(0, 6.0),
                z=140.0 + rng.normal(0, 6.0),
                t=i * 0.1,
                range_m=2000.0,
                bearing_deg=0.0,
                altitude_m=140.0,
                strength=1.0,
                class_evidence={EntityClass.FPV_FAST: 0.4},
            )
            for sid, kind in (("radar1", "radar"), ("rf1", "rf"))
        ]
        tracker.update(dets, t=i * 0.1)
    tracks = [t for t in tracker.state.tracks.values() if t.entity_hint() == "TGT"]
    assert len(tracks) == 1
    assert set(tracks[0].sensors) == {"radar1", "rf1"}
    assert tracks[0].quality > 0.4


def test_ambiguity_biases_weak_suggestions_toward_hostile() -> None:
    """A high hint bias pushes a low-confidence label guess toward 'hostile'."""
    tracker = TrackTracker(TrackerConfig(), dt=0.1)
    for i in range(60):
        tracker.update(
            [
                Detection(
                    sensor_id="radar1",
                    sensor_type="radar",
                    entity_id="TGT",
                    x=-2000.0 + i * 2.0,
                    y=0.0,
                    z=140.0,
                    t=i * 0.1,
                    range_m=2000.0,
                    bearing_deg=0.0,
                    altitude_m=140.0,
                    strength=1.0,
                    hint_bias=0.9,
                    class_evidence={
                        EntityClass.KITE_BALLOON: 0.40,
                        EntityClass.BIRD_FLOCK: 0.35,
                        EntityClass.FPV_FAST: 0.25,
                    },
                    label_evidence={LabelType.FRIENDLY: 0.5, LabelType.CIVIL: 0.45},
                )
            ],
            t=i * 0.1,
        )
    trk = next(t for t in tracker.state.tracks.values() if t.confirmed)
    _, label, confidence = trk.suggested()
    assert label is LabelType.HOSTILE
    assert confidence < 0.5


def test_engine_run_produces_tracks_with_false_alarms() -> None:
    """A full headless run yields confirmed tracks, most of them real."""
    scenario = generate(seed=4242, difficulty=DifficultyVector(decoy_ratio=0.6)).scenario
    engine = SimEngine(scenario, dt=0.2)
    saw_track = False
    false_tracks = 0
    total_tracks = 0
    while not engine.finished and engine.step_index < 1500:
        engine.tick()
        for trk in engine.state.tracker.tracks.values():
            if trk.confirmed:
                total_tracks += 1
                if not trk.entity_hint():
                    false_tracks += 1
                    saw_track = True
        if engine.step_index == 200:
            saw_track = saw_track or any(t.confirmed for t in engine.state.tracker.tracks.values())
    assert saw_track
    assert total_tracks > 0
    assert false_tracks / max(1, total_tracks) < 0.6


def test_time_to_perimeter_is_sane() -> None:
    """An inbound track reports a positive, finite time-to-perimeter."""
    tracker = TrackTracker(TrackerConfig(), dt=0.1)
    rng = np.random.default_rng(17)
    truth = np.array([2500.0, 0.0, 150.0])
    for i in range(300):
        truth = truth + np.array([-2.0, 0.0, 0.0])
        tracker.update(
            [
                Detection(
                    sensor_id="radar1",
                    sensor_type="radar",
                    entity_id="TGT",
                    x=float(truth[0] + rng.normal(0, 5.0)),
                    y=float(truth[1] + rng.normal(0, 5.0)),
                    z=float(truth[2] + rng.normal(0, 5.0)),
                    t=i * 0.1,
                    range_m=3000.0,
                    bearing_deg=180.0,
                    altitude_m=150.0,
                    strength=1.0,
                )
            ],
            t=i * 0.1,
            site=(0.0, 0.0),
            protected_radius_m=150.0,
        )
    trk = next(t for t in tracker.state.tracks.values() if t.confirmed)
    tti = tracker.time_to_perimeter(trk)
    assert tti is not None and 0.0 < tti < 400.0
    assert math.isfinite(tti)
    expected = (trk.position[0] - 150.0) / abs(trk.velocity[0])
    assert tti == pytest.approx(expected, rel=0.25)
