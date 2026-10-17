"""Mapping helpers from a normalised `DifficultyVector` to concrete magnitudes.

Kept separate from `app.core.generator` so the pure numeric inversion used by
the skill model (DECISIONS D012) can be unit tested on its own.
"""

from __future__ import annotations

import math

from app.core.schemas import DifficultyVector, ResolvedDifficulty, RuleSetId

__all__ = ["resolve_difficulty", "difficulty_for_success"]


def resolve_difficulty(vec: DifficultyVector) -> ResolvedDifficulty:
    """Turn normalised 0..1 axes into the concrete scenario magnitudes they imply."""
    swarm_size = int(round(1 + vec.swarm_size * 11))  # 1..12 entities per wave
    approach_axes = int(round(1 + vec.approach_axes * 3))  # 1..4 simultaneous bearings
    spawn_compression = round(90.0 * (1.0 - vec.time_pressure) + 12.0, 2)  # seconds
    degraded = int(round(vec.sensor_degradation_level * 3))  # 0..3 degradations
    decoys = int(round(vec.decoy_ratio * 6))
    visibility = float(9000.0 - 6600.0 * vec.visibility)
    is_night = vec.night >= 0.5
    rule_set = RuleSetId.STRICT if vec.roe_strictness >= 0.65 else RuleSetId.STANDARD
    if vec.roe_strictness <= 0.18:
        rule_set = RuleSetId.PERMISSIVE
    return ResolvedDifficulty(
        swarm_size=swarm_size,
        approach_axes=approach_axes,
        spawn_compression_s=spawn_compression,
        degraded_sensor_count=degraded,
        decoy_count=decoys,
        visibility_m=max(600.0, visibility),
        is_night=is_night,
        ambiguity=float(vec.ambiguity),
        simultaneity=float(vec.simultaneity),
        roe_rule_set=rule_set,
        roe_strictness=float(vec.roe_strictness),
    )


# Logistic response coefficients per skill: p(success) = sigmoid(k * (x - theta)).
# `x` is the skill rating in [0, 100]; a well-prepared trainee should pass even a
# hard scenario, a weak one should fail it.
_SKILL_CURVES: dict[str, tuple[float, float]] = {
    "detection": (0.075, 38.0),
    "classification": (0.080, 42.0),
    "prioritisation": (0.070, 36.0),
    "engagement": (0.085, 45.0),
    "roe": (0.070, 40.0),
}

#: How much each difficulty axis loads onto each skill, in the `AXES` order.
_SKILL_LOADING: dict[str, dict[str, float]] = {
    "detection": {
        "sensor_degradation_level": 0.30,
        "visibility": 0.30,
        "night": 0.20,
        "swarm_size": 0.10,
        "simultaneity": 0.10,
    },
    "classification": {
        "ambiguity": 0.35,
        "decoy_ratio": 0.25,
        "visibility": 0.20,
        "night": 0.20,
    },
    "prioritisation": {
        "swarm_size": 0.25,
        "approach_axes": 0.25,
        "simultaneity": 0.25,
        "time_pressure": 0.25,
    },
    "engagement": {
        "time_pressure": 0.30,
        "swarm_size": 0.25,
        "approach_axes": 0.20,
        "sensor_degradation_level": 0.25,
    },
    "roe": {
        "roe_strictness": 0.45,
        "decoy_ratio": 0.20,
        "simultaneity": 0.20,
        "ambiguity": 0.15,
    },
}


def _difficulty_scalar(vec: DifficultyVector, skill: str) -> float:
    """Weighted 0..1 difficulty experienced on one skill."""
    loading = _SKILL_LOADING[skill]
    total = 0.0
    weight = 0.0
    for axis, w in loading.items():
        value = float(getattr(vec, axis))
        # `visibility` is an ease axis: more visibility is easier.
        if axis == "visibility":
            value = 1.0 - value
        total += w * value
        weight += w
    return total / max(1e-9, weight)


def predicted_success(skill_rating: float, difficulty: float, skill: str) -> float:
    """Predicted probability the trainee succeeds on `skill` at `difficulty`."""
    k, theta = _SKILL_CURVES[skill]
    z = k * (float(skill_rating) - theta) - 3.2 * float(difficulty)
    return 1.0 / (1.0 + math.exp(-z))


def difficulty_for_success(
    skill_rating: float, target_p: float = 0.70, skill: str = "detection"
) -> float:
    """Invert the logistic curve: the difficulty giving `target_p` success.

    Uses 60 bisection steps, which is more than enough for a 1e-9 tolerance and
    keeps the function deterministic and dependency free.
    """
    k, theta = _SKILL_CURVES[skill]
    lo, hi = 0.0, 1.0
    # Bisect on `p(difficulty) - target_p`; 60 steps is far past double precision.
    for _ in range(60):
        mid = (lo + hi) / 2.0
        z = k * (float(skill_rating) - theta) - 3.2 * mid
        p = 1.0 / (1.0 + math.exp(-z))
        if p > target_p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0
