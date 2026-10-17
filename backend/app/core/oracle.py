"""The counterfactual oracle: the "what an expert would have done" ghost.

The oracle is *not* clairvoyant in a way that makes the AAR meaningless. It:

* only ever sees **confirmed tracks**, exactly as the trainee does;
* suffers a realistic perception delay before it can act on a track, and a
  longer delay before it commits an effector;
* resolves track -> entity through the tracker's association history, so it
  only ever "knows" about objects the sensors actually found;
* obeys the same rules of engagement, the same effector cooldowns, ammunition
  and ranges as the trainee.

What it *does* get for free is ground truth about identity and intent, which is
exactly the advantage a well-briefed, well-trained operator has and a trainee
does not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Final

import numpy as np

from app.core.engine import EngineState
from app.core.roe import sector_bearing
from app.core.schemas import (
    ActionType,
    LabelType,
    Scenario,
    TraineeAction,
)
from app.core.world import EntityState, WorldState

__all__ = ["OraclePolicy", "OracleDecision", "oracle_policy"]

#: Seconds after a track is confirmed before the oracle can identify it.
PERCEPTION_DELAY_S: Final[float] = 2.0

#: Additional seconds of "human" reaction time before committing an effector.
REACTION_DELAY_S: Final[float] = 2.5

#: Time-to-impact below which the oracle stops hesitating and fires.
PANIC_TTI_S: Final[float] = 14.0

#: Track quality below which the oracle will not engage a hostile.
MIN_ENGAGE_QUALITY: Final[float] = 0.25

#: Probability the oracle mis-classifies under heavy ambiguity, per decision.
#: The oracle models a *good but human* operator, so it is fallible - but a
#: well-briefed expert is right far more often than a trainee.
_CLASSIFICATION_ERROR_RATE: Final[float] = 0.015

#: How many times the oracle will commit an effector to the same track. A real
#: operator re-engages a shot that missed, up to a point.
MAX_SHOTS_PER_TARGET: Final[int] = 3


@dataclass(slots=True)
class OracleDecision:
    """One oracle action, with the reasoning the AAR displays."""

    action: TraineeAction
    rationale: str
    entity_id: str | None = None


@dataclass(slots=True)
class OraclePolicy:
    """Stateful oracle that emits one batch of actions per simulation step."""

    scenario: Scenario
    perception_delay_s: float = PERCEPTION_DELAY_S
    reaction_delay_s: float = REACTION_DELAY_S
    seen_at: dict[str, float] = field(default_factory=dict)
    decided_at: dict[str, float] = field(default_factory=dict)
    classified: set[str] = field(default_factory=set)
    warned: set[str] = field(default_factory=set)
    engaged: dict[str, int] = field(default_factory=dict)
    """Track id -> number of effector commits already spent on it."""
    rng: np.random.Generator = field(default_factory=lambda: np.random.default_rng(0))
    _order: list[float] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    def reset(self, seed: int) -> None:
        """Clear all per-session state and reseed the (tiny) RNG."""
        self.seen_at.clear()
        self.decided_at.clear()
        self.classified.clear()
        self.warned.clear()
        self.engaged.clear()
        self._order.clear()
        self.rng = np.random.default_rng(seed)

    def act(self, state: EngineState, world: WorldState, t: float) -> list[OracleDecision]:
        """Return the oracle's actions for the instant `t`."""
        track_entities = state.track_entities
        tracks = state.tracker.tracks
        out: list[OracleDecision] = []

        candidates: list[tuple[float, str, EntityState]] = []
        for tid, trk in sorted(tracks.items()):
            if not trk.confirmed:
                continue
            eid = track_entities.get(tid) or trk.entity_hint()
            ent = world.entities.get(eid) if eid else None
            if ent is None or not ent.active or ent.neutralised:
                continue
            self.seen_at.setdefault(tid, t)
            if t - self.seen_at[tid] < self.perception_delay_s:
                continue
            self.decided_at.setdefault(tid, t)
            candidates.append((self._urgency(ent), tid, ent))

        candidates.sort(key=lambda item: item[0])
        for _, tid, ent in candidates:
            out.extend(self._handle_track(world, t, tid, ent))
        return out

    # ------------------------------------------------------------------ #
    def _urgency(self, ent: EntityState) -> float:
        """Lower is more urgent: time-to-impact, then range."""
        v = ent.velocity
        p = ent.position
        d = math.hypot(p.x, p.y)
        if d < 1e-3:
            return 0.0
        closing = -(p.x * v.x + p.y * v.y) / d
        if closing <= 0.1:
            return 1000.0 + d
        return max(0.0, (d - 150.0) / closing)

    def _handle_track(
        self, world: WorldState, t: float, tid: str, ent: EntityState
    ) -> list[OracleDecision]:
        """Identify, warn and (when authorised) engage one track."""
        out: list[OracleDecision] = []

        if tid not in self.classified:
            label = self._true_label(ent)
            out.append(
                OracleDecision(
                    action=TraineeAction(
                        t=round(t, 2),
                        type=ActionType.ACK_TRACK,
                        track_id=tid,
                        params={},
                        source="oracle",
                    ),
                    rationale=f"Acknowledged {tid} on confirmation.",
                    entity_id=ent.id,
                )
            )
            self.classified.add(tid)
            out.append(
                OracleDecision(
                    action=TraineeAction(
                        t=round(t, 2),
                        type=ActionType.CLASSIFY,
                        track_id=tid,
                        params={"class": label.value, "confidence": 0.9},
                        source="oracle",
                    ),
                    rationale=(
                        f"Identified {tid} as {label.value.upper()} "
                        f"({ent.entity_class.value.replace('_', ' ')}) and set its priority."
                    ),
                    entity_id=ent.id,
                )
            )
            self._order.append(t)

        if ent.label is LabelType.HOSTILE and ent.label in self.scenario.roe.allowed_engage_labels:
            if tid not in self.warned and self.scenario.roe.require_warning_before_engage:
                self.warned.add(tid)
                out.append(
                    OracleDecision(
                        action=TraineeAction(
                            t=round(t, 2),
                            type=ActionType.WARN,
                            track_id=tid,
                            params={},
                            source="oracle",
                        ),
                        rationale="Issued the warning required before engagement.",
                        entity_id=ent.id,
                    )
                )
            if self.engaged.get(tid, 0) >= MAX_SHOTS_PER_TARGET:
                return out
            if t - self.decided_at.get(tid, t) < self.reaction_delay_s:
                return out
            shot = self._plan_engagement(world, t, tid, ent)
            if shot is not None:
                self.engaged[tid] = self.engaged.get(tid, 0) + 1
                out.append(shot)
        elif tid not in self.warned and ent.label is not LabelType.BIRD:
            self.warned.add(tid)
            out.append(
                OracleDecision(
                    action=TraineeAction(
                        t=round(t, 2),
                        type=ActionType.HOLD_FIRE,
                        track_id=tid,
                        params={"reason": f"true label {ent.label.value}"},
                        source="oracle",
                    ),
                    rationale=(f"{tid} is not an authorised target; holding fire and reporting."),
                    entity_id=ent.id,
                )
            )
        return out

    def _true_label(self, ent: EntityState) -> LabelType:
        """Ground-truth label, with a small calibrated error rate.

        The oracle is a model of a *good but human* operator, so it occasionally
        commits the same classification error the trainee would.
        """
        ambiguity = float(self.scenario.difficulty.ambiguity)
        rate = _CLASSIFICATION_ERROR_RATE * (1.0 + ambiguity)
        if float(self.rng.random()) < rate:
            alternatives = [x for x in LabelType if x is not ent.label]
            return alternatives[int(self.rng.integers(0, len(alternatives)))]
        return ent.label

    def _plan_engagement(
        self, world: WorldState, t: float, tid: str, ent: EntityState
    ) -> OracleDecision | None:
        """Choose an effector and decide whether the shot is worth taking."""
        trk_urgency = self._urgency(ent)
        in_civil = sector_bearing(
            list(self.scenario.site.civil_sectors),
            math.degrees(math.atan2(ent.position.y, ent.position.x)) % 360.0,
            ent.distance_to_site(),
        )
        if in_civil:
            return None

        target_pos = ent.position
        best: tuple[float, str, ActionType] | None = None
        for spec in self.scenario.effectors:
            runtime = world.effectors.get(spec.id)
            if runtime is None or runtime.ammo <= 0:
                continue
            if runtime.cooldown_remaining > 0.0 or runtime.reload_remaining > 0.0:
                continue
            rng_m = math.dist((target_pos.x, target_pos.y, target_pos.z), (spec.x, spec.y, spec.z))
            if not (spec.min_range_m <= rng_m <= spec.max_range_m):
                continue
            if spec.kind == "hard" and trk_urgency > PANIC_TTI_S:
                continue
            if spec.kind == "soft" and not spec.susceptible_to_rf_silence and not ent.rf_active:
                continue
            score = rng_m / max(1.0, spec.max_range_m) + (0.25 if spec.kind == "soft" else 0.0)
            if best is None or score < best[0]:
                best = (
                    score,
                    spec.id,
                    ActionType.ENGAGE_S if spec.kind == "soft" else ActionType.ENGAGE_H,
                )
        if best is None:
            return None

        _, effector_id, action_type = best
        return OracleDecision(
            action=TraineeAction(
                t=round(t, 2),
                type=action_type,
                track_id=tid,
                params={"effector": effector_id},
                source="oracle",
            ),
            rationale=(
                f"Engaged {tid} ({ent.entity_class.value}) with {effector_id} at "
                f"{ent.distance_to_site():.0f} m, {trk_urgency:.0f} s to the perimeter."
            ),
            entity_id=ent.id,
        )


def oracle_policy(scenario: Scenario, seed: int | None = None) -> OraclePolicy:
    """Create a fresh oracle policy for `scenario`."""
    policy = OraclePolicy(scenario=scenario)
    policy.reset(int(scenario.seed if seed is None else seed))
    return policy
