"""The per-trainee skill model and the habit features the Red Brain consumes.

Skills are five ratings in [0, 100] (`detection`, `classification`,
`prioritisation`, `engagement`, `roe`). After every session they are updated
with an Elo-style step that is *scaled by the difficulty of the scenario*: a
good result on a hard scenario raises a rating more than the same result on an
easy one, so the rating tracks capability rather than scenario choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

import numpy as np

from app.core.generator_support import difficulty_for_success
from app.core.schemas import (
    ActionType,
    DifficultyVector,
    HabitFeatures,
    LabelType,
    Scenario,
    ScoreBreakdown,
    SkillVector,
    TraineeAction,
)

__all__ = [
    "HabitAccumulator",
    "SkillModel",
    "habits_from_actions",
    "predict_success",
    "update_skills",
]

#: Elo-style K factor. Large enough to move visibly in a handful of sessions,
#: small enough that one bad run does not destroy a rating.
K_FACTOR: Final[float] = 18.0

#: Floor applied to every difficulty multiplier so an easy scenario can never
#: drive a rating negative.
MIN_DIFFICULTY_WEIGHT: Final[float] = 0.35

#: Upper bound on the difficulty weight.
MAX_DIFFICULTY_WEIGHT: Final[float] = 2.4

#: Sectors used by the habit features, matching `core.engine.NUM_SECTORS`.
NUM_SECTORS: Final[int] = 8

#: Keys of the flat context vector the contextual bandit consumes.
CONTEXT_SECTOR_SHARE: Final[int] = 8
CONTEXT_SECTOR_LATENCY: Final[int] = 8
CONTEXT_SCALARS: Final[int] = 6
CONTEXT_DIFFICULTY: Final[int] = 10
CONTEXT_DIM: Final[int] = (
    CONTEXT_SECTOR_SHARE + CONTEXT_SECTOR_LATENCY + CONTEXT_SCALARS + CONTEXT_DIFFICULTY
)


def difficulty_weight(scenario_difficulty: DifficultyVector, skill: str) -> float:
    """How much a result on this scenario should move the `skill` rating."""
    from app.core.generator_support import _difficulty_scalar

    scalar = _difficulty_scalar(scenario_difficulty, skill)
    return float(np.clip(1.0 + 1.4 * scalar, MIN_DIFFICULTY_WEIGHT, MAX_DIFFICULTY_WEIGHT))


def update_skills(
    skills: SkillVector,
    observed: dict[str, float],
    scenario_difficulty: DifficultyVector,
    k: float = K_FACTOR,
) -> SkillVector:
    """Elo-style update of every skill from one session's sub-scores.

    `observed` maps a skill name to a 0-100 sub-score.
    """
    current = skills.to_array()
    updated = list(current)
    for name, value in observed.items():
        if name not in SkillVector.NAMES:
            continue
        idx = SkillVector.NAMES.index(name)
        performance = float(np.clip(value, 0.0, 100.0)) / 100.0
        expected = current[idx] / 100.0
        gain = k * difficulty_weight(scenario_difficulty, name) * (performance - expected)
        updated[idx] = float(np.clip(current[idx] + gain, 0.0, 100.0))
    return SkillVector.from_array(updated)


def predict_success(skill: SkillVector, difficulty: DifficultyVector, skill_name: str) -> float:
    """Predicted probability the trainee succeeds on `skill_name`."""
    from app.core.generator_support import _difficulty_scalar, predicted_success

    rating = float(getattr(skill, skill_name))
    return predicted_success(rating, _difficulty_scalar(difficulty, skill_name), skill_name)


def target_difficulty(skills: SkillVector, skill_name: str, target_p: float = 0.70) -> float:
    """Difficulty that would put this trainee at `target_p` success on `skill_name`."""
    return difficulty_for_success(float(getattr(skills, skill_name)), target_p, skill_name)


# --------------------------------------------------------------------------- #
# Habit features
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class HabitAccumulator:
    """Streaming accumulator for the sector-scan and reaction-time habits."""

    sector_ack_counts: list[int] = field(default_factory=lambda: [0] * NUM_SECTORS)
    sector_action_counts: list[int] = field(default_factory=lambda: [0] * NUM_SECTORS)
    sector_latency_sums: list[float] = field(default_factory=lambda: [0.0] * NUM_SECTORS)
    total_actions: int = 0
    classify_early: int = 0
    hold_fire: int = 0
    engagements: int = 0
    hostile_guessed: int = 0
    total_classified: int = 0
    false_positive_guesses: int = 0
    reaction_times: list[float] = field(default_factory=list)
    sessions_observed: int = 0

    def add_session(
        self,
        actions: list[TraineeAction],
        sector_of_track: dict[str, int],
        latency_by_track: dict[str, float],
        duration_s: float,
    ) -> None:
        """Fold one session's action log into the accumulator."""
        self.sessions_observed += 1
        for action in actions:
            self.total_actions += 1
            sector = None
            if action.track_id and action.track_id in sector_of_track:
                sector = sector_of_track[action.track_id]
            elif action.type is ActionType.STEER_SENSOR:
                sector = int(float(action.params.get("az", 0.0)) % 360.0) // (360 // NUM_SECTORS)
            if sector is not None and 0 <= sector < NUM_SECTORS:
                self.sector_action_counts[sector] += 1
            if action.type is ActionType.ACK_TRACK and sector is not None:
                self.sector_ack_counts[sector] += 1
            if action.type is ActionType.CLASSIFY:
                self.total_classified += 1
                label = str(action.params.get("class", "unknown"))
                if label == LabelType.HOSTILE.value:
                    self.hostile_guessed += 1
                    self.classify_early += 1
                elif label == LabelType.FRIENDLY.value:
                    self.false_positive_guesses += 1
            if action.type is ActionType.HOLD_FIRE:
                self.hold_fire += 1
            if action.is_engagement():
                self.engagements += 1
            if action.track_id and action.track_id in latency_by_track:
                lat = float(latency_by_track[action.track_id])
                if sector is not None and 0 <= sector < NUM_SECTORS:
                    self.sector_latency_sums[sector] += lat
        if duration_s > 0:
            self.reaction_times.append(self.total_actions / (duration_s / 10.0))
            self.reaction_times = self.reaction_times[-20:]

    def to_habits(self, base_sector_share: list[float] | None = None) -> HabitFeatures:
        """Materialise the flat habit vector."""
        total_ack = sum(self.sector_ack_counts)
        if total_ack > 0:
            share = [c / total_ack for c in self.sector_ack_counts]
        elif base_sector_share:
            share = list(base_sector_share)
        else:
            share = [1.0 / NUM_SECTORS] * NUM_SECTORS
        latency = [
            (self.sector_latency_sums[i] / self.sector_ack_counts[i])
            if self.sector_ack_counts[i] > 0
            else 60.0
            for i in range(NUM_SECTORS)
        ]
        over = 0.0
        under = 0.0
        if self.total_classified > 0:
            hostile_rate = self.hostile_guessed / self.total_classified
            over = max(0.0, hostile_rate - 0.55)
            under = max(0.0, 0.55 - hostile_rate)
        saturation = 0.0
        if len(self.reaction_times) >= 2:
            recent = float(np.mean(self.reaction_times[-3:]))
            early = float(np.mean(self.reaction_times[:3]))
            saturation = max(0.0, (recent - early) / max(1e-6, early)) * 1000.0
        return HabitFeatures(
            sector_scan_share=[round(s, 5) for s in share],
            mean_detection_latency_by_sector=[round(v, 3) for v in latency],
            over_engage_rate=round(over, 5),
            under_engage_rate=round(under, 5),
            saturation_reaction_ms=round(min(3000.0, saturation), 3),
            mean_actions_per_10s=round(
                float(np.mean(self.reaction_times[-5:])) if self.reaction_times else 1.0, 4
            ),
            classify_early_rate=round(self.classify_early / max(1, self.total_classified), 5),
            hold_fire_rate=round(self.hold_fire / max(1, self.total_actions), 5),
            least_scanned_sector=int(np.argmin(share)),
            most_scanned_sector=int(np.argmax(share)),
            sessions_observed=self.sessions_observed,
            total_actions=self.total_actions,
        )


def habits_from_actions(
    actions: list[TraineeAction],
    sector_of_track: dict[str, int],
    latency_by_track: dict[str, float],
    duration_s: float,
    previous: HabitFeatures | None = None,
) -> HabitFeatures:
    """One-shot habit extraction (used by the seed script and the API)."""
    acc = HabitAccumulator()
    acc.add_session(actions, sector_of_track, latency_by_track, duration_s)
    base = list(previous.sector_scan_share) if previous else None
    return acc.to_habits(base)


# --------------------------------------------------------------------------- #
# The model object
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class SkillModel:
    """A trainee's skill state, persisted by the API layer."""

    trainee_id: str
    skills: SkillVector = field(default_factory=SkillVector)
    history: list[dict[str, float]] = field(default_factory=list)
    habits: HabitFeatures | None = None
    sessions: int = 0

    def apply(
        self,
        breakdown: ScoreBreakdown,
        scenario: Scenario,
        actions: list[TraineeAction],
        sector_of_track: dict[str, int] | None = None,
        latency_by_track: dict[str, float] | None = None,
    ) -> SkillVector:
        """Update the ratings and habit features from one finished session."""
        from app.core.scoring import skill_subscores

        observed = skill_subscores(breakdown)
        self.skills = update_skills(self.skills, observed, scenario.difficulty)
        self.sessions += 1
        record = {
            **{k: round(v, 3) for k, v in observed.items()},
            "total": round(breakdown.total, 3),
            "difficulty": round(scenario.difficulty.mean, 4),
            "session": float(self.sessions),
        }
        self.history.append(record)
        self.habits = habits_from_actions(
            actions,
            sector_of_track or {},
            latency_by_track or {},
            float(scenario.duration_s),
            self.habits,
        )
        return self.skills

    def weakest_skill(self) -> str:
        """The skill with the lowest rating."""
        return self.skills.weakest

    def recommend_difficulty(self, target_p: float = 0.70) -> tuple[DifficultyVector, str]:
        """Difficulty vector that targets ~`target_p` on the weakest skill."""
        from app.core.generator import next_difficulty_for

        weakness_bias = (
            0.05 * float(self.habits.over_engage_rate - self.habits.under_engage_rate)
            if self.habits
            else 0.0
        )
        return next_difficulty_for(self.skills, weak_axis_bias=weakness_bias)

    def decline(self) -> str:
        """Skill with the steepest recent decline (used to break ties)."""
        if len(self.history) < 4:
            return ""
        recent = self.history[-3:]
        older = self.history[-8:-3] or recent
        deltas: dict[str, float] = {}
        for name in SkillVector.NAMES:
            deltas[name] = float(np.mean([h[name] for h in recent])) - float(
                np.mean([h[name] for h in older])
            )
        if not deltas:
            return ""
        return min(deltas, key=lambda k: deltas[k])

    def trend(self) -> dict[str, float]:
        """Mean change per skill over the whole history."""
        if len(self.history) < 2:
            return {name: 0.0 for name in SkillVector.NAMES}
        out: dict[str, float] = {}
        for name in SkillVector.NAMES:
            values = [h[name] for h in self.history]
            half = max(1, len(values) // 2)
            out[name] = round(
                (float(np.mean(values[half:])) - float(np.mean(values[:half]))) / half, 5
            )
        return out
