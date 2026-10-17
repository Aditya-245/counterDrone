"""Build the trainee-visible `StateFrame` from an `EngineState`.

This module is the **only** structural boundary between ground truth and the
client. Anything that reaches the browser is constructed here, from tracker
output, and nothing else. Keeping it in one file means a leak is a single
reviewable function rather than a per-field filtering convention.
"""

from __future__ import annotations

import math

import numpy as np

from app.core.engine import EngineState, TrackAnnotation, sector_of_bearing
from app.core.schemas import (
    DegradationState,
    EffectorView,
    SensorHealthView,
    SimEvent,
    StateFrame,
    TrackView,
)
from app.core.tracking import Track

__all__ = ["build_state_frame", "sector_of_track", "track_view"]


def track_view(state: EngineState, trk: Track, ann: TrackAnnotation | None) -> TrackView:
    """Project one track into the client-facing view (no ground truth)."""
    p = trk.position
    v = trk.velocity
    x, y, z = float(p[0]), float(p[1]), float(p[2])
    site = state.tracker_engine.site
    rng_m = math.hypot(x - site[0], y - site[1])
    bearing = math.degrees(math.atan2(y - site[1], x - site[0])) % 360.0
    speed = float(np.linalg.norm(v))
    cls, label, conf = trk.suggested()
    return TrackView(
        id=trk.id,
        x=x,
        y=y,
        z=z,
        vx=float(v[0]),
        vy=float(v[1]),
        vz=float(v[2]),
        position_sigma_m=round(trk.kf.position_sigma, 2),
        velocity_sigma_mps=round(trk.kf.velocity_sigma, 3),
        quality=round(trk.quality, 4),
        age_s=round(max(0.0, state.world.t - trk.born_t), 2),
        sensors=list(trk.sensors),
        suggested_class=cls,
        suggested_label=label,
        class_confidence=round(conf, 4),
        range_m=round(rng_m, 1),
        bearing_deg=round(bearing, 2),
        altitude_m=round(z, 1),
        speed_mps=round(speed, 2),
        radial_mps=round(trk.radial_mps, 2),
        time_to_perimeter_s=_tti(state, trk),
        acknowledged=bool(ann.acknowledged) if ann else False,
        trainee_class=ann.trainee_class if ann else None,
        trainee_priority=ann.priority if ann else None,
        neutralised=bool(ann.neutralised) if ann else False,
        missed=bool(ann.missed) if ann else False,
        confirmed=trk.confirmed,
    )


def _tti(state: EngineState, trk: Track) -> float | None:
    """Rounded time-to-perimeter or None."""
    value = state.tracker_engine.time_to_perimeter(trk)
    return None if value is None else round(float(value), 2)


def build_state_frame(
    state: EngineState,
    session_id: str,
    step_events: list[SimEvent] | None = None,
) -> StateFrame:
    """Assemble the authoritative 10 Hz frame the client renders.

    Only *confirmed* tracks are emitted: an operator is never shown an
    unconfirmed plot, which is also what keeps the false-track rate bounded in
    the UI. Ground truth never leaves this module.
    """
    world = state.world
    tracks = [
        track_view(state, trk, state.annotations.get(trk.id))
        for trk in sorted(state.tracker.tracks.values(), key=lambda t: t.id)
        if trk.confirmed
    ]
    sensors = []
    for sid, run in sorted(world.sensors.items()):
        sensors.append(
            SensorHealthView(
                id=sid,
                type=run.spec.type,
                state=run.state,
                azimuth_deg=round(run.azimuth_deg, 1),
                fov_deg=run.spec.fov_deg,
                note=run.note
                or ("operational" if run.state is DegradationState.NONE else run.state.value),
            )
        )
    effectors = [
        EffectorView(
            id=eid,
            kind=eff.kind,
            ready=eff.cooldown_remaining <= 0.0 and eff.ammo > 0,
            cooldown_remaining_s=round(eff.cooldown_remaining, 2),
            ammunition=eff.ammo,
            max_range_m=eff.max_range_m,
        )
        for eid, eff in sorted(world.effectors.items())
    ]
    for trk in state.tracker.tracks.values():
        det_count = len(trk.sensors)
        for view in sensors:
            if view.id == trk.sensors[0]:
                view.detections_last_step = det_count
    return StateFrame(
        session_id=session_id,
        t=round(world.t, 3),
        step=world.step,
        duration_s=world.duration_s,
        status=world.status,
        tracks=tracks,
        sensors=sensors,
        effectors=effectors,
        events=list(step_events or []),
        ammo_remaining={eid: eff.ammo for eid, eff in sorted(world.effectors.items())},
        paused=world.paused,
        allow_pause=state.allow_pause,
    )


def sector_of_track(trk: Track) -> int:
    """Sector index a track currently sits in (used by the attention heatmap)."""
    p = trk.position
    return sector_of_bearing(math.degrees(math.atan2(float(p[1]), float(p[0]))) % 360.0)
