"""Per-session scoring.

The score is a weighted sum of five sub-scores, each computed from the
ground-truth timeline (what actually happened) measured against the trainee's
recorded decisions (what the trainee believed and did).

Nothing in this module talks to the trainee: it is a post-hoc assessor and is
allowed to see ground truth.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Final, TypedDict

import numpy as np

from app.core.engine import TrackAnnotation
from app.core.roe import (
    CAP_SCORE,
    DecisionContext,
    RoeRuleSet,
    evaluate_engagement,
    evaluate_outcome,
    roe_spec_for,
)
from app.core.schemas import (
    CoachingPoint,
    EntityScore,
    LabelType,
    Scenario,
    ScoreBreakdown,
    ScoreWeights,
    TraineeAction,
    Verdict,
    VerdictRecord,
)
from app.core.world import EntityState, WorldState

__all__ = ["score_session", "SCORING_SPEC"]


class _EntityFirsts(TypedDict):
    """Earliest-of-each-event summary for one entity, plus what the trainee did."""

    t_ack: float | None
    t_classified: float | None
    t_priority: float | None
    trainee_label: LabelType | None
    calibration: str
    priorities: list[int]


#: Time constant (seconds) of the detection-latency decay. A trainee who
#: acknowledges at exactly `t_first_detectable` scores full marks; 20 s late
#: scores e^-1.
DETECTION_TAU_S: Final[float] = 20.0

#: Grace period before latency starts to cost anything: acknowledging a track
#: within a second of it appearing is instant recognition.
DETECTION_GRACE_S: Final[float] = 1.0

#: Confusion-matrix penalty weights: the cost of each kind of wrong call.
CLASSIFICATION_PENALTY: Final[dict[tuple[str, str], float]] = {
    ("hostile", "friendly"): 1.0,
    ("hostile", "civil"): 0.85,
    ("hostile", "bird"): 0.8,
    ("hostile", "unknown"): 0.45,
    ("friendly", "hostile"): 0.35,
    ("civil", "hostile"): 0.3,
    ("bird", "hostile"): 0.25,
    ("friendly", "civil"): 0.15,
    ("civil", "friendly"): 0.15,
    ("bird", "civil"): 0.1,
    ("civil", "bird"): 0.1,
    ("friendly", "bird"): 0.1,
    ("bird", "friendly"): 0.1,
}

#: Labels grouped for the "confusion-matrix-aware" normalisation.
_LABEL_ORDER: Final[tuple[str, ...]] = ("hostile", "civil", "friendly", "bird", "unknown")

#: Human-readable scoring specification, mirrored into docs/scoring.md.
SCORING_SPEC: Final[dict[str, object]] = {
    "weights": {
        "detection": 20,
        "classification": 25,
        "prioritisation": 15,
        "engagement_roe": 30,
        "workload": 10,
    },
    "detection": "20 * mean over entities of exp(-max(0, latency - grace)/20s)",
    "classification": (
        "25 * mean over hostile-plus-decoy entities of "
        "(1 - confusion_penalty(true_label, chosen_label)), "
        "unknown counts as 0.45 against a hostile target; "
        "entities never classified score 0 for the classification sub-score."
    ),
    "prioritisation": (
        "15 * Spearman rank correlation between the order the trainee first acted "
        "on each entity and the ground-truth order of time-to-impact."
    ),
    "engagement_roe": (
        "30 * clamp(mean verdict points across all verdicts, 0, 1); "
        "any FALSE_ENGAGE_FRIENDLY or COLLATERAL_VIOLATION caps the total at 60."
    ),
    "workload": (
        "10 * mean of (share of confirmed tracks acknowledged) and "
        "(1 - share of confirmed tracks never acted on) and reaction speed under saturation."
    ),
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _entities_by_track(track_entities: dict[str, str]) -> dict[str, set[str]]:
    """Invert `track_entities` into `entity_id -> {track ids}`."""
    out: dict[str, set[str]] = {}
    for tid, eid in track_entities.items():
        out.setdefault(eid, set()).add(tid)
    return out


def _entity_firsts(
    annotations: dict[str, TrackAnnotation], tracks_by_entity: dict[str, set[str]]
) -> dict[str, _EntityFirsts]:
    """Earliest acknowledge/classify/priority time per entity."""
    out: dict[str, _EntityFirsts] = {}
    for eid, tids in tracks_by_entity.items():
        row: _EntityFirsts = {
            "t_ack": None,
            "t_classified": None,
            "t_priority": None,
            "trainee_label": None,
            "calibration": "none",
            "priorities": [],
        }
        for tid in sorted(tids):
            ann = annotations.get(tid)
            if ann is None:
                continue
            for key in ("t_ack", "t_classified", "t_priority"):
                value: float | None = getattr(ann, key)
                current: float | None = row[key]
                if value is not None and (current is None or value < current):
                    row[key] = value
            if ann.trainee_class is not None and row["trainee_label"] is None:
                row["trainee_label"] = ann.trainee_class
            if ann.warned:
                row["calibration"] = "warned"
            if ann.id_requested:
                row["calibration"] = "requested_id"
            if ann.priority is not None:
                row["priorities"].append(int(ann.priority))
        out[eid] = row
    return out


def _spearman(a: list[float], b: list[float]) -> float:
    """Spearman rank correlation, mapped from [-1, 1] into [0, 1]."""
    if len(a) < 2:
        return 0.5
    ra = np.argsort(np.argsort(np.asarray(a, dtype=float)))
    rb = np.argsort(np.argsort(np.asarray(b, dtype=float)))
    ra = ra.astype(float)
    rb = rb.astype(float)
    n = len(ra)
    d2 = float(np.sum((ra - rb) ** 2))
    rho = 1.0 - 6.0 * d2 / (n * (n * n - 1.0))
    return float(np.clip((rho + 1.0) / 2.0, 0.0, 1.0))


def _nearest_ring_time(entity: EntityState) -> float | None:
    """Ground-truth time at which the threat became urgent (innermost ring)."""
    if not entity.ring_crossings:
        return entity.reached_perimeter_t
    return min(entity.ring_crossings.values())


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def score_session(
    scenario: Scenario,
    world: WorldState,
    actions: list[TraineeAction],
    rule_set: RoeRuleSet,
    track_entities: dict[str, str],
    annotations: dict[str, TrackAnnotation],
    weights: ScoreWeights | None = None,
) -> ScoreBreakdown:
    """Score a finished session.

    Parameters
    ----------
    world:
        The final `WorldState` (ground truth timeline, engagement log, events).
    actions:
        The ordered trainee action log.
    track_entities:
        `EngineState.track_entities`, mapping track ids to ground-truth entities.
    annotations:
        `EngineState.annotations`, keyed by track id.
    """
    weights = weights or ScoreWeights()
    roe_spec = roe_spec_for(scenario)
    tracks_by_entity = _entities_by_track(track_entities)
    firsts = _entity_firsts(annotations, tracks_by_entity)

    engagement_by_entity: dict[str, list[dict[str, Any]]] = {}
    for record in world.engagement_log:
        eid = str(record.get("target_id", ""))
        if eid:
            engagement_by_entity.setdefault(eid, []).append(record)

    entities: list[EntityScore] = []
    verdicts: list[VerdictRecord] = []

    default_firsts: _EntityFirsts = {
        "t_ack": None,
        "t_classified": None,
        "t_priority": None,
        "trainee_label": None,
        "calibration": "none",
        "priorities": [],
    }
    for eid, ent in sorted(world.entities.items()):
        info = firsts.get(eid) or default_firsts
        tacks = info["t_ack"]
        t_first_det = ent.t_first_detectable
        latency = None
        if tacks is not None and t_first_det is not None:
            latency = max(0.0, float(tacks) - float(t_first_det))

        label = info["trainee_label"]
        correct = None
        if label is not None:
            correct = label == ent.label
        calibration = _calibration(
            scenario=scenario,
            ent=ent,
            t_classified=info["t_classified"],
            t_first_det=t_first_det,
            track_quality=_track_quality_for(annotations, tracks_by_entity.get(eid, set())),
        )

        engagements = sorted(engagement_by_entity.get(eid, []), key=lambda r: float(r["t"]))
        first_eng = engagements[0] if engagements else None

        entity_verdicts: list[VerdictRecord] = []
        for record in engagements:
            track_id = str(record.get("track_id", "")) or None
            ann = annotations.get(track_id) if track_id else None
            ctx = DecisionContext(
                t=float(record["t"]),
                entity_id=eid,
                true_label=ent.label,
                track_id=track_id,
                trainee_label=ann.trainee_class if ann else label,
                engaged=True,
                engagement_type=str(record.get("action", "")),
                effector=str(record.get("effector", "")) or None,
                range_m=float(record.get("range_m", 0.0)),
                track_quality=_track_quality_for(annotations, {track_id} if track_id else set()),
                time_to_impact_s=_tti_for(ent, float(record.get("range_m", 0.0))),
                warned=ann.warned if ann else False,
                in_range=str(record.get("reason", "fired")) == "fired",
                sectors=list(scenario.site.civil_sectors),
                collateral_tolerance=roe_spec.collateral_sector_tolerance,
                bearing_deg=_bearing_for(ent),
                require_warning=roe_spec.require_warning_before_engage,
            )
            entity_verdicts.append(evaluate_engagement(rule_set, ctx))

        if not engagements:
            track_id = next(iter(sorted(tracks_by_entity.get(eid, set()))), None)
            ann = annotations.get(track_id) if track_id else None
            ctx = DecisionContext(
                t=float(ent.reached_perimeter_t or world.duration_s),
                entity_id=eid,
                true_label=ent.label,
                track_id=track_id,
                trainee_label=ann.trainee_class if ann else label,
                engaged=False,
                track_quality=_track_quality_for(annotations, tracks_by_entity.get(eid, set())),
                time_to_impact_s=_tti_for(ent, ent.min_range_m),
                warned=ann.warned if ann else False,
                sectors=list(scenario.site.civil_sectors),
                collateral_tolerance=roe_spec.collateral_sector_tolerance,
                bearing_deg=_bearing_for(ent),
                reached_perimeter=ent.reached_perimeter_t is not None,
            )
            entity_verdicts.append(evaluate_outcome(rule_set, ctx))

        verdicts.extend(entity_verdicts)
        entity_score = _entity_points(
            ent,
            latency,
            info["t_ack"],
            info["t_classified"],
            label,
            correct,
            calibration,
            first_eng,
            entity_verdicts,
        )
        entities.append(entity_score)

    detection = _detection_score(entities, weights.detection)
    classification = _classification_score(entities, weights.classification)
    prioritisation = _prioritisation_score(entities, actions, weights.prioritisation)
    engagement, cap = _engagement_score(verdicts, weights.engagement)
    workload = _workload_score(
        entities,
        track_entities,
        annotations,
        actions,
        world.duration_s,
        weights.workload,
    )

    raw_total = detection + classification + prioritisation + engagement + workload
    capped = cap is not None
    total = min(CAP_SCORE, raw_total) if capped else raw_total
    total = float(np.clip(total, 0.0, 100.0))
    raw_total = float(np.clip(raw_total, 0.0, 100.0))

    counts = Counter(v.verdict.value for v in verdicts)
    return ScoreBreakdown(
        total=round(total, 2),
        raw_total=round(raw_total, 2),
        detection=round(detection, 2),
        classification=round(classification, 2),
        prioritisation=round(prioritisation, 2),
        engagement=round(engagement, 2),
        workload=round(workload, 2),
        weights=weights,
        entities=entities,
        verdicts=verdicts,
        verdict_counts=dict(sorted(counts.items())),
        coaching_points=coaching_points(scenario, entities, verdicts, actions),
        capped=capped,
        cap_reason=cap,
        stats=_stats(entities, verdicts, actions),
    )


# --------------------------------------------------------------------------- #
# Sub-scores
# --------------------------------------------------------------------------- #


def _calibration(
    scenario: Scenario,
    ent: EntityState,
    t_classified: float | None,
    t_first_det: float | None,
    track_quality: float,
) -> str:
    """Was the classification made with enough evidence behind it?"""
    del scenario, ent
    if t_classified is None:
        return "never_classified"
    if t_first_det is None:
        return "no_reference"
    lag = float(t_classified) - float(t_first_det)
    if lag < 0.0:
        return "impossible"
    if lag <= 3.0 and track_quality >= 0.5:
        return "well_calibrated"
    if lag <= 12.0:
        return "acceptable"
    return "early_on_weak_evidence"


def _track_quality_for(annotations: dict[str, TrackAnnotation], track_ids: set[str]) -> float:
    """Best track quality among the trainee's annotations for this entity.

    `TrackAnnotation` does not carry a live quality (the track may be gone by the
    time we score), so this is a bounded proxy built from what the trainee did:
    acknowledging and classifying a track is evidence of a good track.
    """
    best = 0.0
    for tid in track_ids:
        ann = annotations.get(tid)
        if ann is None:
            continue
        q = 0.0
        if ann.acknowledged:
            q += 0.35
        if ann.trainee_class is not None:
            q += 0.3
        if ann.warned:
            q += 0.15
        if ann.priority is not None:
            q += 0.1
        best = max(best, q)
    return best


def _tti_for(ent: EntityState, range_m: float | None) -> float | None:
    """Seconds until the entity reaches the protected zone, at engagement range.

    `range_m` is the effector-to-target range recorded at the moment of the
    decision. Using the entity's *end-of-run* closest approach instead would
    make every early engagement look late.
    """
    if range_m is None:
        return None
    speed = ent.speed_mps
    if speed < 0.5:
        return None
    return max(0.0, (float(range_m) - 150.0) / speed)


def _bearing_for(ent: EntityState) -> float:
    """Bearing of the target from the site, used for the civil-sector test."""
    return math.degrees(math.atan2(ent.position.y, ent.position.x)) % 360.0


def _entity_points(
    ent: EntityState,
    latency: float | None,
    t_ack: float | None,
    t_classified: float | None,
    label: LabelType | None,
    correct: bool | None,
    calibration: str,
    first_eng: dict[str, Any] | None,
    verdicts: list[VerdictRecord],
) -> EntityScore:
    """Assemble the per-entity detail block."""
    verdict_total = sum(v.points for v in verdicts)
    score = 50.0 * (verdict_total / max(1e-6, len(verdicts))) + 1.0
    return EntityScore(
        entity_id=ent.id,
        entity_class=ent.entity_class,
        true_label=ent.label,
        t_first_detectable=(
            None if ent.t_first_detectable is None else round(ent.t_first_detectable, 2)
        ),
        t_first_ack=None if t_ack is None else round(float(t_ack), 2),
        detection_latency_s=None if latency is None else round(latency, 2),
        trainee_label=label,
        t_classified=None if t_classified is None else round(float(t_classified), 2),
        classification_correct=correct,
        classification_calibration=calibration,
        t_engaged=None if first_eng is None else round(float(first_eng["t"]), 2),
        engage_range_m=None
        if first_eng is None
        else round(float(first_eng.get("range_m", 0.0)), 1),
        effector=None if first_eng is None else str(first_eng.get("effector", "")),
        neutralised=bool(ent.neutralised),
        reached_perimeter=ent.reached_perimeter_t is not None,
        t_perimeter=None if ent.reached_perimeter_t is None else round(ent.reached_perimeter_t, 2),
        t_closest_approach=None
        if ent.t_closest_approach is None
        else round(ent.t_closest_approach, 2),
        ring_crossings={str(k): round(float(v), 2) for k, v in sorted(ent.ring_crossings.items())},
        verdicts=verdicts,
        score=round(float(np.clip(score, 0.0, 100.0)), 2),
    )


def _detection_score(entities: list[EntityScore], weight: float) -> float:
    """Exponential decay of acknowledgement latency."""
    latencies = [e.detection_latency_s for e in entities if e.detection_latency_s is not None]
    if not entities:
        return 0.0
    if not latencies:
        return 0.0
    values = [
        math.exp(-max(0.0, float(v) - DETECTION_GRACE_S) / DETECTION_TAU_S) for v in latencies
    ]
    return float(weight * float(np.mean(values)))


def _classification_score(entities: list[EntityScore], weight: float) -> float:
    """Confusion-matrix-aware classification accuracy."""
    scored = [e for e in entities if e.trainee_label is not None]
    if not entities:
        return 0.0
    if not scored:
        return 0.0
    total = 0.0
    for e in scored:
        true = e.true_label.value
        chosen = e.trainee_label.value if e.trainee_label else "unknown"
        if true == chosen:
            total += 1.0
        else:
            total += 1.0 - CLASSIFICATION_PENALTY.get((true, chosen), 0.5)
    return float(weight * (total / len(scored)))


def _prioritisation_score(
    entities: list[EntityScore], actions: list[TraineeAction], weight: float
) -> float:
    """Rank correlation between the trainee's action order and time-to-impact.

    The metric is the order in which the trainee *committed* to each entity -
    the engagement if there was one, otherwise the first acknowledgement -
    compared against the ground-truth order in which each entity became urgent.
    Engaging in the right order is the skill being measured; acknowledging
    everything simultaneously is not.
    """
    del actions
    engaged_pairs: list[tuple[float, float]] = []
    ack_pairs: list[tuple[float, float]] = []
    for e in entities:
        t_urgent = _nearest_ring_time_entity(e)
        if t_urgent is None:
            continue
        if e.t_engaged is not None:
            engaged_pairs.append((float(e.t_engaged), t_urgent))
        if e.t_first_ack is not None:
            ack_pairs.append((float(e.t_first_ack), t_urgent))

    pairs = engaged_pairs if len(engaged_pairs) >= 2 else ack_pairs
    if len(pairs) < 2:
        # Ordering cannot be wrong when there is only one actionable entity.
        return float(weight) if pairs else float(weight * 0.5)
    actions_order = [p[0] for p in pairs]
    truth_order = [p[1] for p in pairs]
    return float(weight * _spearman(actions_order, truth_order))


def _nearest_ring_time_entity(e: EntityScore) -> float | None:
    if e.ring_crossings:
        return min(float(v) for v in e.ring_crossings.values())
    return e.t_perimeter


def _engagement_score(verdicts: list[VerdictRecord], weight: float) -> tuple[float, str | None]:
    """Mean verdict points, plus the cap reason when a fatal verdict occurred."""
    if not verdicts:
        return (0.0, None)
    points = [v.points for v in verdicts]
    mean = float(np.mean(points))
    normalised = float(np.clip(mean, 0.0, 1.0))
    cap: str | None = None
    for verdict in verdicts:
        if verdict.verdict in (Verdict.FALSE_ENGAGE_FRIENDLY, Verdict.COLLATERAL_VIOLATION):
            cap = (
                "Total capped at 60: a friendly target was engaged."
                if verdict.verdict is Verdict.FALSE_ENGAGE_FRIENDLY
                else "Total capped at 60: an engagement took place over a declared civil sector."
            )
            break
    return (weight * normalised, cap)


def _workload_score(
    entities: list[EntityScore],
    track_entities: dict[str, str],
    annotations: dict[str, TrackAnnotation],
    actions: list[TraineeAction],
    duration_s: float,
    weight: float,
) -> float:
    """Ignored tracks and action latency under saturation."""
    if not entities:
        return 0.0
    acked = sum(1 for e in entities if e.t_first_ack is not None)
    ack_share = acked / len(entities)

    acted_ids = {a.track_id for a in actions if a.track_id}
    never = sum(1 for tid in track_entities if tid not in acted_ids)
    ignore_share = never / max(1, len(track_entities))

    per_10s = len(actions) / max(1.0, duration_s / 10.0)
    if per_10s <= 4.0:
        tempo = 1.0
    else:
        tempo = float(np.clip(1.0 - (per_10s - 4.0) / 12.0, 0.0, 1.0))

    return float(weight * (0.5 * ack_share + 0.3 * (1.0 - ignore_share) + 0.2 * tempo))


def _stats(
    entities: list[EntityScore], verdicts: list[VerdictRecord], actions: list[TraineeAction]
) -> dict[str, object]:
    """Extra numbers the dashboard charts need."""
    latencies = [e.detection_latency_s for e in entities if e.detection_latency_s is not None]
    matrix: dict[str, dict[str, int]] = {a: {b: 0 for b in _LABEL_ORDER} for a in _LABEL_ORDER}
    for e in entities:
        chosen = e.trainee_label.value if e.trainee_label else "unknown"
        matrix[e.true_label.value][chosen] += 1
    return {
        "entity_count": len(entities),
        "hostile_count": sum(1 for e in entities if e.true_label is LabelType.HOSTILE),
        "neutralised_count": sum(1 for e in entities if e.neutralised),
        "breached_count": sum(1 for e in entities if e.reached_perimeter),
        "action_count": len(actions),
        "mean_detection_latency_s": round(float(np.mean(latencies)), 2) if latencies else None,
        "median_detection_latency_s": round(float(np.median(latencies)), 2) if latencies else None,
        "confusion_matrix": matrix,
        "latency_samples": [round(float(v), 2) for v in latencies],
    }


# --------------------------------------------------------------------------- #
# Coaching
# --------------------------------------------------------------------------- #


def coaching_points(
    scenario: Scenario,
    entities: list[EntityScore],
    verdicts: list[VerdictRecord],
    actions: list[TraineeAction],
) -> list[CoachingPoint]:
    """The top three rule-based coaching messages. Never LLM generated."""
    counts = Counter(v.verdict for v in verdicts)
    out: list[CoachingPoint] = []

    def add(code: str, severity: str, text: str, related: list[str] | None = None) -> None:
        out.append(
            CoachingPoint(code=code, severity=severity, text=text, related_entities=related or [])  # type: ignore[arg-type]
        )

    friendly = [v.entity_id for v in verdicts if v.verdict is Verdict.FALSE_ENGAGE_FRIENDLY]
    if friendly:
        add(
            "friendly_engaged",
            "critical",
            "You engaged a FRIENDLY track. Confirm identification (or request a beacon check) "
            "before committing an effector, and never engage on a class hint alone.",
            friendly,
        )
    collateral = [v.entity_id for v in verdicts if v.verdict is Verdict.COLLATERAL_VIOLATION]
    if collateral:
        add(
            "collateral",
            "critical",
            "You fired across a declared civil sector. Check the sector overlay before every "
            "engagement, not just the target's label.",
            collateral,
        )
    late = [v.entity_id for v in verdicts if v.verdict is Verdict.LATE_ENGAGE]
    if late:
        add(
            "late_engagement",
            "warn",
            "You engaged inside the late-engagement window. Work the time-to-impact column: "
            "the track with the smallest value gets the effector first.",
            late,
        )
    premature = [v.entity_id for v in verdicts if v.verdict is Verdict.PREMATURE_ENGAGE]
    if premature:
        add(
            "premature_engagement",
            "warn",
            "You engaged before identifying the target. Under the current rule set a positive ID "
            "is required first.",
            premature,
        )
    missed = [e.entity_id for e in entities if e.reached_perimeter and not e.neutralised]
    if missed:
        add(
            "missed_threats",
            "critical",
            f"{len(missed)} hostile(s) reached the protected zone. Acknowledge tracks as soon as "
            "they are confirmed and keep the closest-to-perimeter one at the top of your list.",
            missed,
        )
    unclassified = [
        e.entity_id
        for e in entities
        if e.trainee_label is None and e.t_first_detectable is not None
    ]
    if unclassified:
        add(
            "unclassified",
            "info",
            f"{len(unclassified)} detected track(s) were never classified. Use the keyboard "
            "classification keys (H hostile / C civil / F friendly / B bird / U unknown).",
            unclassified,
        )
    decoy_hits = [
        e.entity_id
        for e in entities
        if e.true_label in (LabelType.BIRD, LabelType.CIVIL) and e.neutralised
    ]
    if decoy_hits:
        add(
            "decoy_engaged",
            "warn",
            f"{len(decoy_hits)} non-hostile decoy(s) were engaged. Track the suggested-class "
            "confidence and watch for erratic low-altitude contacts.",
            decoy_hits,
        )
    early = [
        e.entity_id for e in entities if e.classification_calibration == "early_on_weak_evidence"
    ]
    if early:
        add(
            "early_classification",
            "info",
            f"{len(early)} classification(s) were made on weak evidence. Wait for the track to "
            "settle, or steer the EO/IR camera in for a positive ID.",
            early,
        )
    if counts.get(Verdict.CORRECT_ENGAGE, 0) and not out:
        add(
            "clean_run",
            "info",
            "Clean run: no rule-of-engagement violations recorded. Push the difficulty up or "
            "shorten your acknowledgement times to keep the curve moving.",
        )
    out.sort(key=lambda p: {"critical": 0, "warn": 1, "info": 2}[p.severity])
    return out[:3]


# --------------------------------------------------------------------------- #
# Derived helper used by the skill model
# --------------------------------------------------------------------------- #


def skill_subscores(
    breakdown: ScoreBreakdown, weights: ScoreWeights | None = None
) -> dict[str, float]:
    """Map a `ScoreBreakdown` onto the five 0-100 skill sub-scores."""
    weights = weights or breakdown.weights
    roe_map = {"correct": 1.0, "mixed": 0.6, "poor": 0.25, "none": 0.5}
    verdicts = breakdown.verdicts
    if not verdicts:
        roe_norm = roe_map["none"]
    else:
        positives = sum(1 for v in verdicts if v.points > 0)
        negatives = sum(1 for v in verdicts if v.points < 0)
        roe_norm = (
            roe_map["correct"]
            if negatives == 0
            else (roe_map["mixed"] if positives >= negatives else roe_map["poor"])
        )
    return {
        "detection": 100.0 * breakdown.detection / max(1e-6, weights.detection),
        "classification": 100.0 * breakdown.classification / max(1e-6, weights.classification),
        "prioritisation": 100.0 * breakdown.prioritisation / max(1e-6, weights.prioritisation),
        "engagement": 100.0 * breakdown.engagement / max(1e-6, weights.engagement),
        "roe": 100.0 * roe_norm,
    }
