"""Constant-velocity Kalman tracking, gated data association and multi-sensor fusion.

The trainee never sees ground truth. They see `Track` objects, which carry an
estimated state, an uncertainty, the list of contributing sensors and a *class
suggestion* that is deliberately allowed to be wrong or low confidence
(``DifficultyVector.ambiguity``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.core.schemas import EntityClass, LabelType

__all__ = [
    "Detection",
    "KalmanFilter",
    "Track",
    "TrackerConfig",
    "TrackerState",
    "TrackTracker",
]

#: chi-square 0.99 gate for a 3-D position innovation (3 degrees of freedom).
CHI2_GATE_3D = 11.3448664301447


@dataclass(slots=True)
class Detection:
    """One sensor report, already reduced to a world-frame measurement."""

    sensor_id: str
    sensor_type: str
    entity_id: str
    """Ground-truth id, used only inside `core` (scoring / oracle / fusion)."""
    x: float
    y: float
    z: float
    t: float
    range_m: float
    bearing_deg: float
    altitude_m: float
    radial_mps: float = 0.0
    speed_sigma_mps: float = 2.0
    class_evidence: dict[EntityClass, float] = field(default_factory=dict)
    label_evidence: dict[LabelType, float] = field(default_factory=dict)
    has_range_rate: bool = False
    strength: float = 1.0
    hint_bias: float = 0.0

    def as_measurement(self) -> NDArray[np.float64]:
        """Position measurement vector."""
        return np.array([self.x, self.y, self.z], dtype=np.float64)


class KalmanFilter:
    """A constant-velocity Kalman filter over ``[p, v]`` in 3-D."""

    __slots__ = ("x", "P", "dt", "_q_scale")

    def __init__(self, dt: float, process_noise: float = 1.2) -> None:
        self.dt = float(dt)
        self._q_scale = float(process_noise)
        self.x: NDArray[np.float64] = np.zeros(6, dtype=np.float64)
        self.P: NDArray[np.float64] = np.diag([400.0, 400.0, 400.0, 64.0, 64.0, 64.0])

    # ------------------------------------------------------------------ #
    def transition(self) -> NDArray[np.float64]:
        """State transition matrix for the configured `dt`."""
        f: NDArray[np.float64] = np.eye(6, dtype=np.float64)
        f[0, 3] = self.dt
        f[1, 4] = self.dt
        f[2, 5] = self.dt
        return f

    def process_covariance(self) -> NDArray[np.float64]:
        """Discrete white-noise-acceleration process covariance."""
        dt = self.dt
        q = self._q_scale
        dt2, dt3, dt4 = dt * dt, dt**3, dt**4
        out = np.array(
            [
                [dt4 / 4.0, 0.0, 0.0, dt3 / 2.0, 0.0, 0.0],
                [0.0, dt4 / 4.0, 0.0, 0.0, dt3 / 2.0, 0.0],
                [0.0, 0.0, dt4 / 4.0, 0.0, 0.0, dt3 / 2.0],
                [dt3 / 2.0, 0.0, 0.0, dt2, 0.0, 0.0],
                [0.0, dt3 / 2.0, 0.0, 0.0, dt2, 0.0],
                [0.0, 0.0, dt3 / 2.0, 0.0, 0.0, dt2],
            ],
            dtype=np.float64,
        )
        out *= q
        return out

    def predict(self, accel_hint: NDArray[np.float64] | None = None) -> NDArray[np.float64]:
        """Propagate the state one `dt` and return the predicted measurement."""
        f = self.transition()
        self.x = np.asarray(f @ self.x, dtype=np.float64)
        self.P = np.asarray(f @ self.P @ f.T + self.process_covariance(), dtype=np.float64)
        if accel_hint is not None:
            self.x[:3] += 0.5 * self.dt * self.dt * accel_hint
        return self.x[:3].copy()

    def update_position(self, z: NDArray[np.float64], r: NDArray[np.float64]) -> None:
        """Fuse a 3-D position measurement."""
        h = np.zeros((3, 6), dtype=np.float64)
        h[0, 0] = h[1, 1] = h[2, 2] = 1.0
        y = z - h @ self.x
        s = h @ self.P @ h.T + r
        try:
            k = self.P @ h.T @ np.linalg.inv(s)
        except np.linalg.LinAlgError:  # pragma: no cover - r is PD by construction
            return
        self.x = np.asarray(self.x + k @ y, dtype=np.float64)
        ident = np.eye(6, dtype=np.float64)
        self.P = np.asarray((ident - k @ h) @ self.P, dtype=np.float64)
        self.P = np.asarray(0.5 * (self.P + self.P.T), dtype=np.float64)

    def update_radial_rate(self, rate: float, sigma: float) -> None:
        """Fuse a scalar range-rate measurement along the current line of sight."""
        pos = self.x[:3]
        norm = float(np.linalg.norm(pos))
        if norm < 1e-6:
            return
        los = pos / norm
        h = np.concatenate([np.zeros(3), los])[None, :]
        r = np.array([[max(1e-6, sigma * sigma)]], dtype=np.float64)
        innovation = rate - float((h @ self.x)[0])
        s = float((h @ self.P @ h.T)[0, 0] + r[0, 0])
        if s <= 1e-9:
            return
        k = (self.P @ h.T) / s
        self.x = np.asarray(self.x + (k.flatten() * innovation), dtype=np.float64)
        ident = np.eye(6, dtype=np.float64)
        self.P = np.asarray((ident - k @ h) @ self.P, dtype=np.float64)
        self.P = np.asarray(0.5 * (self.P + self.P.T), dtype=np.float64)

    # ------------------------------------------------------------------ #
    @property
    def position(self) -> NDArray[np.float64]:
        """Estimated position."""
        return self.x[:3].copy()

    @property
    def velocity(self) -> NDArray[np.float64]:
        """Estimated velocity."""
        return self.x[3:].copy()

    @property
    def position_sigma(self) -> float:
        """Root-mean-square position error (metres)."""
        return float(math.sqrt(max(1e-6, np.trace(self.P[:3, :3]) / 3.0)))

    @property
    def velocity_sigma(self) -> float:
        """Root-mean-square velocity error (m/s)."""
        return float(math.sqrt(max(1e-6, np.trace(self.P[3:, 3:]) / 3.0)))

    def position_covariance(self) -> NDArray[np.float64]:
        """3x3 position covariance block (for the UI ellipse)."""
        return self.P[:3, :3].copy()

    def to_dict(self) -> dict[str, Any]:
        """Serialise filter state."""
        return {"x": self.x.tolist(), "P": self.P.tolist(), "dt": self.dt, "q": self._q_scale}

    def load_dict(self, data: dict[str, Any]) -> None:
        """Inverse of `to_dict`."""
        self.x = np.asarray(data["x"], dtype=np.float64)
        self.P = np.asarray(data["P"], dtype=np.float64)


@dataclass(slots=True)
class TrackerConfig:
    """Tunable association / initiation parameters.

    `init_m` of the last `init_n` hits confirms a track (classic M-of-N).
    Unconfirmed tracks are cheap and short-lived; only confirmed tracks are ever
    shown to the operator, which is what keeps the false-track rate bounded.
    """

    init_m: int = 3
    init_n: int = 5
    min_confirm_hits: int = 3
    tentative_max_misses: int = 6
    max_misses: int = 10
    max_age_s: float = 20.0
    """Maximum time since a track's last update before it is dropped."""
    gate_m2: float = CHI2_GATE_3D
    max_assoc_distance_m: float = 320.0
    assoc_noise_floor_m: float = 30.0
    merge_distance_m: float = 45.0
    base_range_sigma_m: float = 8.0
    position_process_noise: float = 4.0


@dataclass(slots=True)
class Track:
    """A fused, trainee-visible track."""

    id: str
    kf: KalmanFilter
    born_t: float
    last_update_t: float
    hits: int = 0
    misses: int = 0
    confirmed: bool = False
    sensors: list[str] = field(default_factory=list)
    class_evidence: dict[EntityClass, float] = field(default_factory=dict)
    label_evidence: dict[LabelType, float] = field(default_factory=dict)
    recent_hits: list[int] = field(default_factory=list)
    entity_ids: list[str] = field(default_factory=list)
    """Ground-truth ids this track has ever been associated with (`core` only)."""
    hint_bias: float = 0.0
    neutralised: bool = False
    last_range_m: float = 0.0
    radial_mps: float = 0.0
    last_bearing_deg: float = 0.0

    @property
    def age_s(self) -> float:
        """Seconds since the track was created."""
        return max(0.0, self.last_update_t - self.born_t)

    @property
    def quality(self) -> float:
        """Track quality in [0, 1] from confirmation, recency and sensor diversity."""
        conf = min(1.0, self.hits / 6.0)
        diversity = min(1.0, len(self.sensors) / 3.0)
        recency = math.exp(-self.misses / 6.0)
        precision = math.exp(-self.kf.position_sigma / 320.0)
        return float(
            np.clip(0.35 * conf + 0.2 * diversity + 0.2 * recency + 0.25 * precision, 0, 1)
        )

    @property
    def position(self) -> NDArray[np.float64]:
        """Estimated position."""
        return self.kf.position

    @property
    def velocity(self) -> NDArray[np.float64]:
        """Estimated velocity."""
        return self.kf.velocity

    def suggested(self) -> tuple[EntityClass | None, LabelType | None, float]:
        """Class / label suggestion and its confidence, ambiguity included.

        Weak evidence is skewed toward ``hostile`` by the scenario's
        ``ambiguity`` axis, so a low-confidence suggestion can be wrong on
        purpose (DECISIONS D014).
        """
        if not self.class_evidence:
            return (None, None, 0.0)
        total = float(sum(self.class_evidence.values()))
        if total <= 1e-9:
            return (None, None, 0.0)
        best = max(self.class_evidence.items(), key=lambda kv: (kv[1], kv[0].value))
        confidence = best[1] / total
        label = self._biased_label(confidence)
        return (best[0], label, float(np.clip(confidence, 0.0, 1.0)))

    def _biased_label(self, confidence: float) -> LabelType | None:
        """Apply the ambiguity skew to the accumulated label evidence."""
        if not self.label_evidence:
            return None
        skewed = dict(self.label_evidence)
        total = float(sum(skewed.values()))
        if total <= 1e-9:
            return None
        skew = self.hint_bias * (1.0 - float(np.clip(confidence, 0.0, 1.0))) * total
        skewed[LabelType.HOSTILE] = skewed.get(LabelType.HOSTILE, 0.0) + skew
        return max(skewed.items(), key=lambda kv: (kv[1], kv[0].value))[0]

    def entity_hint(self) -> str | None:
        """Most frequently associated ground-truth id. `core` internal only.

        Ties break on the smallest id. This has to be explicit: `set` iteration
        order depends on the process hash seed, and the hint feeds a *decision*
        (the oracle's notion of which entity a track is), so a hash-ordered
        tie-break makes the same scenario seed score differently from one run to
        the next. A track that breaks and re-forms across several entities is
        exactly the case that hits the tie.
        """
        if not self.entity_ids:
            return None
        counts: dict[str, int] = {}
        for eid in self.entity_ids:
            if eid:
                counts[eid] = counts.get(eid, 0) + 1
        if not counts:
            return None
        return min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]

    def to_dict(self) -> dict[str, Any]:
        """Serialise."""
        return {
            "id": self.id,
            "kf": self.kf.to_dict(),
            "born_t": self.born_t,
            "last_update_t": self.last_update_t,
            "hits": self.hits,
            "misses": self.misses,
            "confirmed": self.confirmed,
            "sensors": list(self.sensors),
            "class_evidence": {k.value: v for k, v in self.class_evidence.items()},
            "label_evidence": {k.value: v for k, v in self.label_evidence.items()},
            "recent_hits": list(self.recent_hits),
            "entity_ids": list(self.entity_ids),
            "hint_bias": self.hint_bias,
            "neutralised": self.neutralised,
            "last_range_m": self.last_range_m,
            "radial_mps": self.radial_mps,
            "last_bearing_deg": self.last_bearing_deg,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], config: TrackerConfig) -> Track:
        """Inverse of `to_dict`."""
        del config  # a restored track carries its own filter state
        kf = KalmanFilter(float(data["kf"]["dt"]), float(data["kf"].get("q", 1.2)))
        kf.load_dict(data["kf"])
        return cls(
            id=str(data["id"]),
            kf=kf,
            born_t=float(data["born_t"]),
            last_update_t=float(data["last_update_t"]),
            hits=int(data["hits"]),
            misses=int(data["misses"]),
            confirmed=bool(data["confirmed"]),
            sensors=list(data.get("sensors", [])),
            class_evidence={
                EntityClass(k): float(v) for k, v in dict(data.get("class_evidence", {})).items()
            },
            label_evidence={
                LabelType(k): float(v) for k, v in dict(data.get("label_evidence", {})).items()
            },
            recent_hits=list(data.get("recent_hits", [])),
            entity_ids=list(data.get("entity_ids", [])),
            hint_bias=float(data.get("hint_bias", 0.0)),
            neutralised=bool(data.get("neutralised", False)),
            last_range_m=float(data.get("last_range_m", 0.0)),
            radial_mps=float(data.get("radial_mps", 0.0)),
            last_bearing_deg=float(data.get("last_bearing_deg", 0.0)),
        )


@dataclass(slots=True)
class TrackerState:
    """Serialisable tracker state, embedded in the engine state."""

    next_id: int = 1
    tracks: dict[str, Track] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise."""
        return {
            "next_id": self.next_id,
            "tracks": [self.tracks[k].to_dict() for k in sorted(self.tracks)],
            "deleted": list(self.deleted),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], config: TrackerConfig) -> TrackerState:
        """Inverse of `to_dict`."""
        state = cls(next_id=int(data["next_id"]))
        for item in data.get("tracks", []):
            trk = Track.from_dict(item, config)
            state.tracks[trk.id] = trk
        state.deleted = list(data.get("deleted", []))
        return state


class TrackTracker:
    """Multi-sensor tracker: association, M-of-N initiation, deletion, fusion."""

    def __init__(self, config: TrackerConfig | None = None, dt: float = 0.1) -> None:
        self.config = config or TrackerConfig()
        self.dt = float(dt)
        self.state = TrackerState()
        self.site: tuple[float, float] = (0.0, 0.0)
        self.protected_radius_m: float = 150.0
        self.ring_distances_m: tuple[float, ...] = ()

    # ------------------------------------------------------------------ #
    def update(
        self,
        detections: list[Detection],
        t: float,
        site: tuple[float, float] = (0.0, 0.0),
        protected_radius_m: float = 150.0,
        ring_distances_m: tuple[float, ...] = (),
    ) -> list[Track]:
        """Fuse `detections` into tracks and return the live track list."""
        self.site = site
        self.protected_radius_m = protected_radius_m
        self.ring_distances_m = ring_distances_m
        self._predict_all(t)
        matches, unmatched_dets, unmatched_tracks = self._associate(detections)

        for ti, di in matches:
            self._correct(self.state.tracks[ti], detections[di], t)

        for di in unmatched_dets:
            self._initiate(detections[di], t)

        for ti in unmatched_tracks:
            trk = self.state.tracks.get(ti)
            if trk is None:
                continue
            limit = self.config.max_misses if trk.confirmed else self.config.tentative_max_misses
            if trk.misses > limit:
                del self.state.tracks[ti]
                self.state.deleted.append(ti)

        if len(self.state.deleted) > 256:
            self.state.deleted = self.state.deleted[-128:]

        self._prune_duplicates()
        return list(self.state.tracks.values())

    def _prune_duplicates(self) -> None:
        """Standard track-manager step: drop a younger twin of a confirmed track.

        Two confirmed tracks whose estimates sit within `merge_distance_m` of
        each other are the same object under a noisy measurement; the one with
        more hits is kept. No ground truth is consulted.
        """
        limit = float(self.config.merge_distance_m)
        confirmed = sorted(
            (t for t in self.state.tracks.values() if t.confirmed),
            key=lambda t: (-t.hits, t.born_t, t.id),
        )
        for i, older in enumerate(confirmed):
            if older.id not in self.state.tracks:
                continue
            for younger in confirmed[i + 1 :]:
                if younger.id not in self.state.tracks:
                    continue
                d = float(np.linalg.norm(older.position - younger.position))
                if d <= limit:
                    older.hits += younger.hits // 2
                    for sid in younger.sensors:
                        if sid not in older.sensors:
                            older.sensors.append(sid)
                    for eid in younger.entity_ids:
                        if eid and eid not in older.entity_ids:
                            older.entity_ids.append(eid)
                    del self.state.tracks[younger.id]
                    self.state.deleted.append(younger.id)
                    limit = d + 1e-6
        self._drop_fragments(confirmed)

    def _drop_fragments(self, confirmed: list[Track]) -> None:
        """Discard tentative tracks sitting on top of a confirmed track.

        These are measurement fragments of an object the tracker already holds;
        without this, a manoeuvring target shreds into a dozen tentative plots
        every scan.
        """
        if not confirmed:
            return
        limit = float(self.config.merge_distance_m)
        positions = np.array([t.position for t in confirmed if t.id in self.state.tracks])
        if positions.size == 0:
            return
        for tid, trk in list(self.state.tracks.items()):
            if trk.confirmed:
                continue
            d = np.linalg.norm(positions - trk.position, axis=1)
            if float(d.min()) <= limit:
                del self.state.tracks[tid]
                self.state.deleted.append(tid)

    # ------------------------------------------------------------------ #
    def _predict_all(self, t: float) -> None:
        for trk in self.state.tracks.values():
            trk.kf.predict()
            trk.misses += 1
        cfg = self.config
        for tid, trk in list(self.state.tracks.items()):
            limit = cfg.max_misses if trk.confirmed else cfg.tentative_max_misses
            stale = (t - trk.last_update_t) > cfg.max_age_s
            if trk.misses > limit or stale:
                del self.state.tracks[tid]
                self.state.deleted.append(tid)

    def _associate(
        self, detections: list[Detection]
    ) -> tuple[list[tuple[str, int]], list[int], list[str]]:
        """Gated nearest-neighbour association via Hungarian assignment on a cost matrix."""
        track_ids = sorted(self.state.tracks)
        if not track_ids or not detections:
            return ([], list(range(len(detections))), track_ids)

        cost = np.full((len(track_ids), len(detections)), np.inf, dtype=np.float64)
        gate = self.config.gate_m2
        max_d = float(self.config.max_assoc_distance_m)
        floor = float(self.config.assoc_noise_floor_m) ** 2
        for i, tid in enumerate(track_ids):
            trk = self.state.tracks[tid]
            p = trk.kf.position
            s = trk.kf.P[:3, :3] + np.eye(3) * max(16.0, floor)
            try:
                inv = np.linalg.inv(s)
            except np.linalg.LinAlgError:  # pragma: no cover
                continue
            for j, det in enumerate(detections):
                d = det.as_measurement() - p
                if float(np.dot(d, d)) > max_d * max_d:
                    continue
                m2 = float(d @ inv @ d)
                if m2 <= gate:
                    cost[i, j] = m2
        if not np.isfinite(cost).any():
            return ([], list(range(len(detections))), track_ids)

        from scipy.optimize import linear_sum_assignment

        padded = np.where(np.isfinite(cost), cost, 1e6)
        rows, cols = linear_sum_assignment(padded)

        matches: list[tuple[str, int]] = []
        used_tracks: set[str] = set()
        used_dets: set[int] = set()
        for i, j in zip(rows, cols, strict=True):
            if not np.isfinite(cost[i, j]):
                continue
            tid = track_ids[i]
            matches.append((tid, int(j)))
            used_tracks.add(tid)
            used_dets.add(int(j))

        unmatched_tracks = [t for t in track_ids if t not in used_tracks]
        unmatched_dets = [j for j in range(len(detections)) if j not in used_dets]
        return matches, unmatched_dets, unmatched_tracks

    def _correct(self, trk: Track, det: Detection, t: float) -> None:
        base = self.config.base_range_sigma_m
        sigma = base * (1.0 + det.range_m / 2500.0) / max(0.2, det.strength)
        sigma = float(np.clip(sigma, 4.0, 400.0))
        trk.kf.update_position(
            det.as_measurement(), np.asarray(np.eye(3) * sigma * sigma, dtype=np.float64)
        )
        if det.has_range_rate:
            trk.kf.update_radial_rate(det.radial_mps, det.speed_sigma_mps)
            trk.radial_mps = det.radial_mps
        trk.hits += 1
        trk.misses = 0
        trk.last_update_t = t
        trk.last_range_m = det.range_m
        trk.last_bearing_deg = det.bearing_deg
        if det.sensor_id not in trk.sensors:
            trk.sensors.append(det.sensor_id)
        # A detection with no entity id is a clutter return. Recording the empty
        # string here would let it *tie* with the real entity in `entity_hint`,
        # and the hint is what resolves an engagement onto a target - so a
        # single clutter return could make every shot on the track miss.
        if det.entity_id and det.entity_id not in trk.entity_ids:
            trk.entity_ids.append(det.entity_id)
        if det.hint_bias > trk.hint_bias:
            trk.hint_bias = det.hint_bias
        for cls, weight in det.class_evidence.items():
            trk.class_evidence[cls] = trk.class_evidence.get(cls, 0.0) + weight
        for lab, weight in det.label_evidence.items():
            trk.label_evidence[lab] = trk.label_evidence.get(lab, 0.0) + weight
        trk.recent_hits.append(1)
        if len(trk.recent_hits) > self.config.init_n:
            trk.recent_hits = trk.recent_hits[-self.config.init_n :]
        if trk.hits >= self.config.min_confirm_hits and not trk.confirmed:
            trk.confirmed = True

    def _initiate(self, det: Detection, t: float) -> None:
        cfg = self.config
        tid = f"T{self.state.next_id:04d}"
        self.state.next_id += 1
        kf = KalmanFilter(self.dt, cfg.position_process_noise)
        kf.x = np.concatenate([det.as_measurement(), np.zeros(3)])
        sigma = cfg.base_range_sigma_m * (1.0 + det.range_m / 2500.0)
        sigma = float(np.clip(sigma, 8.0, 400.0))
        kf.P = np.diag([sigma**2, sigma**2, sigma**2, 36.0, 36.0, 36.0])
        if det.has_range_rate:
            los = det.as_measurement() / max(1e-6, float(np.linalg.norm(det.as_measurement())))
            kf.x[3:] = los * det.radial_mps
        trk = Track(id=tid, kf=kf, born_t=t, last_update_t=t)
        self.state.tracks[tid] = trk
        self._correct(trk, det, t)
        trk.recent_hits = [0] * (cfg.init_n - 1) + [1]

    def confirm_pending(self, t: float) -> list[str]:
        """Promote M-of-N tentative tracks to confirmed; returns newly confirmed ids."""
        cfg = self.config
        newly: list[str] = []
        for tid in sorted(self.state.tracks):
            trk = self.state.tracks[tid]
            if trk.confirmed:
                continue
            window = trk.recent_hits[-cfg.init_n :]
            window = window + [0] * (cfg.init_n - len(window))
            if sum(window) >= cfg.init_m:
                trk.confirmed = True
                newly.append(tid)
        _ = t
        return newly

    def time_to_perimeter(self, trk: Track) -> float | None:
        """Estimated seconds until the track crosses the protected zone boundary."""
        p = trk.kf.position
        v = trk.kf.velocity
        sx, sy = self.site
        radius = self.protected_radius_m
        dx, dy = float(p[0]) - sx, float(p[1]) - sy
        a = float(v[0] ** 2 + v[1] ** 2)
        b = 2.0 * (dx * float(v[0]) + dy * float(v[1]))
        c = dx * dx + dy * dy - radius * radius
        if abs(a) < 1e-6:
            if abs(b) < 1e-9:
                return None
            tau = -c / b
            return tau if tau >= 0.0 else None
        disc = b * b - 4.0 * a * c
        if disc < 0.0:
            return None
        sq = math.sqrt(disc)
        roots = ((-b - sq) / (2 * a), (-b + sq) / (2 * a))
        taus = [t for t in roots if t >= -0.5]
        if not taus:
            return None
        return float(max(0.0, min(taus)))
