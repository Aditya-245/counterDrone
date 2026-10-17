"""The Red Brain: an adaptive adversary that learns a trainee's habits.

Layer A (always available): a **LinUCB contextual bandit** over a discrete set
of attacker tactics. The context is the trainee's habit vector (DECISIONS
D013), the arm is a tactic, and the reward is how effectively the tactic got
close before being handled plus a bonus for exploiting a measured blind spot.

Layer B (optional): a PPO-trained swarm policy, loaded lazily. If
`stable_baselines3` is not installed or the policy file is missing, the
``learned-swarm`` tactic simply does not exist and everything else works.

Determinism: tactic selection is seeded from the scenario seed plus a snapshot
hash of the stored bandit state, so a scenario built from a given bandit state
always contains the same tactic.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from app.core.schemas import (
    BehaviourKind,
    BehaviourSpec,
    DifficultyVector,
    EntityClass,
    FormationKind,
    HabitExploit,
    HabitFeatures,
    LabelType,
    RedBrainReport,
    Tactic,
    ThreatSpec,
)
from app.core.world import EntityState

__all__ = [
    "LinUCB",
    "RedBrain",
    "TacticSpace",
    "bandit_state_digest",
    "describe_policy_availability",
    "load_learned_policy",
]

#: LinUCB exploration coefficient.
ALPHA: Final[float] = 0.45

#: Reward shaping constants.
REWARD_CLOSE: Final[float] = 1.0
REWARD_EXPLOIT: Final[float] = 1.6
REWARD_HELD: Final[float] = 0.4
PENALTY_NEUTRALISED_EARLY: Final[float] = 0.5


# --------------------------------------------------------------------------- #
# Tactic space
# --------------------------------------------------------------------------- #


def build_tactics() -> list[Tactic]:
    """The shipped tactic space: eight bearings x profile combinations."""
    out: list[Tactic] = []
    for bearing in (45, 135, 225, 315):
        out.append(
            Tactic(
                id=f"axis_{bearing}",
                name=f"Direct approach from {bearing} deg",
                approach_bearings=[bearing],
                altitude_profile="high",
                speed_profile="fast",
                timing="staggered",
                decoy="none",
                formation=FormationKind.LEADER_FOLLOWER,
                description=f"Single-axis run in on the {bearing} deg bearing at speed.",
            )
        )
    for bearing in (0, 90, 180, 270):
        out.append(
            Tactic(
                id=f"hug_{bearing}",
                name=f"Terrain-hugging from {bearing} deg",
                approach_bearings=[bearing],
                altitude_profile="terrain_hugging",
                speed_profile="slow",
                timing="staggered",
                decoy="bird_mimic",
                formation=FormationKind.SCATTER,
                description=(
                    f"Low, slow, terrain-masked approach on {bearing} deg with bird-like decoys."
                ),
            )
        )
    out.append(
        Tactic(
            id="pincer_wedge",
            name="Two-axis pincer in wedge",
            approach_bearings=[45, 225],
            altitude_profile="high",
            speed_profile="medium",
            timing="simultaneous",
            decoy="none",
            formation=FormationKind.PINCER,
            description="Two wedges arrive together from opposite bearings.",
        )
    )
    out.append(
        Tactic(
            id="pincer_scatter",
            name="Two-axis pincer, scattered",
            approach_bearings=[135, 315],
            altitude_profile="terrain_hugging",
            speed_profile="medium",
            timing="simultaneous",
            decoy="bird_mimic",
            formation=FormationKind.SCATTER,
            description="Two loose groups arrive together, low and masked.",
        )
    )
    out.append(
        Tactic(
            id="lure_line",
            name="Slow lure line with kites",
            approach_bearings=[90],
            altitude_profile="high",
            speed_profile="slow",
            timing="staggered",
            decoy="lure",
            formation=FormationKind.LINE,
            description="A slow, obvious line behind a curtain of kites to bait a premature shot.",
        )
    )
    out.append(
        Tactic(
            id="learned-swarm",
            name="Learned swarm policy (PPO)",
            approach_bearings=[0, 180],
            altitude_profile="high",
            speed_profile="medium",
            timing="staggered",
            decoy="none",
            formation=FormationKind.LEADER_FOLLOWER,
            description="Coordinated approach from the optional reinforcement-learned policy.",
        )
    )
    return out


class TacticSpace:
    """Tactic list plus the mapping from a tactic to scenario parameters."""

    def __init__(self, tactics: list[Tactic] | None = None) -> None:
        self.tactics = list(tactics if tactics is not None else build_tactics())

    def available(self, learned_policy_available: bool) -> list[Tactic]:
        """Tactics usable right now (drops `learned-swarm` without the policy)."""
        if learned_policy_available:
            return list(self.tactics)
        return [t for t in self.tactics if t.id != "learned-swarm"]

    def get(self, tactic_id: str) -> Tactic:
        """Look up one tactic."""
        for t in self.tactics:
            if t.id == tactic_id:
                return t
        raise KeyError(tactic_id)

    def index(self, tactic_id: str) -> int:
        """Arm index of a tactic."""
        return [t.id for t in self.tactics].index(tactic_id)


# --------------------------------------------------------------------------- #
# LinUCB
# --------------------------------------------------------------------------- #


@dataclass(slots=True, init=False)
class LinUCB:
    """Disjoint LinUCB: one ridge-regression head per arm.

    Written with an explicit `__init__` rather than dataclass defaults because
    every array's shape depends on `dim` and `n_arms`.
    """

    dim: int
    n_arms: int
    alpha: float
    A: NDArray[np.float64]
    b: NDArray[np.float64]
    pulls: NDArray[np.int64]
    reward_sum: NDArray[np.float64]

    def __init__(self, dim: int, n_arms: int, alpha: float = ALPHA) -> None:
        """Allocate one `dim`-dimensional ridge head per arm."""
        self.dim = int(dim)
        self.n_arms = int(n_arms)
        self.alpha = float(alpha)
        self.A = np.tile(np.eye(self.dim, dtype=np.float64), (self.n_arms, 1, 1))
        self.b = np.zeros((self.n_arms, self.dim), dtype=np.float64)
        self.pulls = np.zeros(self.n_arms, dtype=np.int64)
        self.reward_sum = np.zeros(self.n_arms, dtype=np.float64)

    # ------------------------------------------------------------------ #
    def scores(self, context: np.ndarray) -> np.ndarray:
        """UCB score for every arm given `context`."""
        x = np.asarray(context, dtype=np.float64).reshape(self.dim)
        out = np.zeros(self.n_arms, dtype=np.float64)
        for a in range(self.n_arms):
            a_mat = self.A[a]
            theta = np.linalg.solve(a_mat, self.b[a])
            mean = float(x @ theta)
            try:
                inv = np.linalg.inv(a_mat)
                var = float(x @ inv @ x)
            except np.linalg.LinAlgError:  # pragma: no cover - ridge keeps A PD
                var = 1.0
            out[a] = mean + self.alpha * math.sqrt(max(var, 1e-9))
        return out

    def select(self, context: np.ndarray, forced: int | None = None) -> int:
        """Pick an arm: the forced one, or the argmax of the UCB score."""
        if forced is not None:
            return int(forced)
        return int(np.argmax(self.scores(context)))

    def update(self, arm: int, context: np.ndarray, reward: float) -> None:
        """Ridge update of the chosen arm with the observed reward."""
        x = np.asarray(context, dtype=np.float64).reshape(self.dim)
        self.A[arm] += np.outer(x, x)
        self.b[arm] += reward * x
        self.pulls[arm] += 1
        self.reward_sum[arm] += float(reward)

    @property
    def total_pulls(self) -> int:
        """How many times any arm has been pulled."""
        return int(self.pulls.sum())

    def exploration_pulls(self, known: set[int]) -> int:
        """Pulls spent on arms outside the shipped baseline."""
        total = 0
        for idx, count in enumerate(self.pulls):
            if idx not in known:
                total += int(count)
        return total

    def mean_reward(self) -> np.ndarray:
        """Per-arm mean reward."""
        return np.divide(
            self.reward_sum,
            np.maximum(1, self.pulls),
            dtype=np.float64,
        )

    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        """Serialise for the database."""
        return {
            "dim": self.dim,
            "n_arms": self.n_arms,
            "alpha": self.alpha,
            "A": self.A.tolist(),
            "b": self.b.tolist(),
            "pulls": self.pulls.tolist(),
            "reward_sum": self.reward_sum.tolist(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LinUCB:
        """Inverse of `to_dict`."""
        out = cls(
            dim=int(data["dim"]),
            n_arms=int(data["n_arms"]),
            alpha=float(data.get("alpha", ALPHA)),
        )
        out.A = np.asarray(data["A"], dtype=np.float64)
        out.b = np.asarray(data["b"], dtype=np.float64)
        out.pulls = np.asarray(data["pulls"], dtype=np.int64)
        out.reward_sum = np.asarray(data["reward_sum"], dtype=np.float64)
        return out


# --------------------------------------------------------------------------- #
# The Red Brain
# --------------------------------------------------------------------------- #


def habit_context(habits: HabitFeatures, difficulty: DifficultyVector) -> np.ndarray:
    """Flat, fixed-order context vector consumed by the bandit."""
    return np.asarray(habits.context_vector(difficulty), dtype=np.float64)


def bandit_state_digest(state: dict[str, Any] | None) -> str:
    """Stable digest of a bandit snapshot, used to seed tactic selection."""
    if not state:
        return "empty"
    payload = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()


@dataclass(slots=True)
class RedBrain:
    """Adaptive adversary: bandit state plus the mapping to scenario content."""

    space: TacticSpace = field(default_factory=TacticSpace)
    learned_policy_available: bool = False
    bandit: LinUCB | None = None
    last_tactic_id: str = ""
    last_exploits: list[HabitExploit] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    def context_dim(self) -> int:
        """Dimension of the context vector (8+8+6+10)."""
        return 32

    def ensure_bandit(self) -> LinUCB:
        """Create the bandit on first use."""
        if self.bandit is None:
            self.bandit = LinUCB(dim=self.context_dim(), n_arms=len(self.space.tactics))
        return self.bandit

    # ------------------------------------------------------------------ #
    def choose_tactic(
        self,
        habits: HabitFeatures,
        difficulty: DifficultyVector,
        seed: int,
        forced_tactic: str | None = None,
    ) -> tuple[Tactic, dict[str, Any]]:
        """Pick the tactic to attack with, seeded for reproducibility.

        The seed is mixed with a digest of the current bandit state, so the
        same (seed, bandit state) pair always yields the same tactic.
        """
        bandit = self.ensure_bandit()
        context = habit_context(habits, difficulty)
        available = self.space.available(self.learned_policy_available)
        allowed = {self.space.index(t.id) for t in available}
        digest = bandit_state_digest(bandit.to_dict())
        rng = np.random.default_rng((int(seed) + int(digest, 16)) % (2**63))
        forced_idx = self.space.index(forced_tactic) if forced_tactic else None

        if self.bandit is not None and self.bandit.total_pulls < len(self.space.tactics):
            # Bootstrap: cover every tactic once before exploiting.
            unpulled = [a for a in sorted(allowed) if int(bandit.pulls[a]) == 0]
            chosen = int(rng.choice(unpulled)) if unpulled else bandit.select(context, forced_idx)
        else:
            chosen = bandit.select(context, forced_idx)
        if chosen not in allowed:
            chosen = int(sorted(allowed)[0])

        tactic = self.space.tactics[chosen]
        self.last_tactic_id = tactic.id
        self.last_exploits = self.identify_exploits(tactic, habits, context)
        return tactic, {
            "arm": chosen,
            "context_digest": digest,
            "exploration": bool(bandit.pulls[chosen] <= 1),
            "exploits": self.last_exploits,
        }

    # ------------------------------------------------------------------ #
    def identify_exploits(
        self, tactic: Tactic, habits: HabitFeatures, context: np.ndarray
    ) -> list[HabitExploit]:
        """Return only claims that are backed by the logged habit statistics."""
        del context
        out: list[HabitExploit] = []
        shares = habits.sector_scan_share
        bearings = tactic.approach_bearings
        if bearings and habits.total_actions > 0:
            for bearing in bearings:
                sector = int(bearing % 360) // (360 // len(shares))
                share = shares[sector]
                if share < min(shares) + 0.04:
                    out.append(
                        HabitExploit(
                            habit="sector_scan_bias",
                            description=(
                                f"You scan sector {_sector_name(sector)} only "
                                f"{share * 100:.1f}% of the time; the attack axis was shifted "
                                f"to {bearing} deg."
                            ),
                            evidence={
                                "sector_share": round(share, 5),
                                "all_sector_shares": [round(s, 5) for s in shares],
                                "attack_bearing_deg": bearing,
                                "sessions_observed": habits.sessions_observed,
                                "actions_logged": habits.total_actions,
                            },
                            confidence=round(
                                min(1.0, 0.4 + 0.6 * share / max(1e-6, min(shares))), 4
                            ),
                        )
                    )
        if habits.over_engage_rate > 0.05 and tactic.decoy in ("bird_mimic", "lure"):
            out.append(
                HabitExploit(
                    habit="over_engagement",
                    description=(
                        "You classify a high fraction of contacts as hostile "
                        f"({habits.classify_early_rate * 100:.0f}%), so decoys were paired "
                        "with this approach."
                    ),
                    evidence={
                        "over_engage_rate": habits.over_engage_rate,
                        "classify_early_rate": habits.classify_early_rate,
                        "decoy": tactic.decoy,
                    },
                    confidence=round(min(1.0, habits.over_engage_rate * 4.0), 4),
                )
            )
        if habits.saturation_reaction_ms > 120.0 and tactic.timing == "simultaneous":
            out.append(
                HabitExploit(
                    habit="saturation_reaction",
                    description=(
                        f"Your reaction time degrades by {habits.saturation_reaction_ms:.0f} ms "
                        "under load, so this wave arrives simultaneously."
                    ),
                    evidence={
                        "saturation_reaction_ms": habits.saturation_reaction_ms,
                        "mean_actions_per_10s": habits.mean_actions_per_10s,
                    },
                    confidence=round(min(1.0, habits.saturation_reaction_ms / 1000.0), 4),
                )
            )
        if habits.under_engage_rate > 0.05 and tactic.speed_profile == "fast":
            out.append(
                HabitExploit(
                    habit="under_engagement",
                    description=(
                        "You rarely commit to a hostile call, so this approach is fast "
                        "enough to reach the perimeter before you decide."
                    ),
                    evidence={
                        "under_engage_rate": habits.under_engage_rate,
                        "speed_profile": tactic.speed_profile,
                    },
                    confidence=round(min(1.0, habits.under_engage_rate * 4.0), 4),
                )
            )
        if habits.hold_fire_rate > 0.15 and tactic.decoy == "lure":
            out.append(
                HabitExploit(
                    habit="hold_fire_bias",
                    description=(
                        f"You hold fire on {habits.hold_fire_rate * 100:.0f}% of your actions; "
                        "a visible lure line was sent ahead of the attack."
                    ),
                    evidence={"hold_fire_rate": habits.hold_fire_rate},
                    confidence=round(min(1.0, habits.hold_fire_rate * 2.0), 4),
                )
            )
        return out

    # ------------------------------------------------------------------ #
    def observe_outcome(
        self,
        context: np.ndarray,
        entities: list[EntityState],
        handled_entity_ids: set[str],
        protected_radius_m: float,
    ) -> float:
        """Reward the Red Brain for this scenario, in [0, ~3]."""
        bandit = self.ensure_bandit()
        arm = self.space.index(self.last_tactic_id)
        attackers = [e for e in entities if e.label is LabelType.HOSTILE]
        if not attackers:
            return 0.0

        # How close did they get before being handled?
        worst = min(
            (min(e.min_range_m, protected_radius_m) for e in attackers), default=protected_radius_m
        )
        closeness = float(np.clip(1.0 - worst / max(1.0, protected_radius_m * 4.0), 0.0, 1.0))
        reward = REWARD_CLOSE * closeness

        reached = sum(
            1
            for e in attackers
            if e.reached_perimeter_t is not None and e.id not in handled_entity_ids
        )
        reward += REWARD_HELD * min(1.0, reached / max(1, len(attackers)))

        handled = sum(1 for e in attackers if e.id in handled_entity_ids)
        if handled:
            reward -= PENALTY_NEUTRALISED_EARLY * (handled / len(attackers))

        exploits = self.last_exploits
        if exploits:
            reward += REWARD_EXPLOIT * float(
                np.clip(max(float(e.confidence) for e in exploits), 0.0, 1.0)
            )
        bandit.update(arm, context, float(reward))
        return float(reward)

    # ------------------------------------------------------------------ #
    def report(
        self, trainee_id: str, habits: HabitFeatures, difficulty: DifficultyVector
    ) -> RedBrainReport:
        """Build the 'adversary adapted to you' panel payload."""
        bandit = self.ensure_bandit()
        available = self.space.available(self.learned_policy_available)
        return RedBrainReport(
            trainee_id=trainee_id,
            tactic_id=self.last_tactic_id,
            tactic_name=self.space.get(self.last_tactic_id).name if self.last_tactic_id else "",
            tactics_considered=len(available),
            exploration_pulls=bandit.exploration_pulls(set()),
            total_pulls=bandit.total_pulls,
            exploits=list(self.last_exploits),
            available_tactics=[t.id for t in available],
            learned_policy_available=self.learned_policy_available,
        )

    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        """Serialise."""
        return {
            "last_tactic_id": self.last_tactic_id,
            "last_exploits": [e.model_dump(mode="json") for e in self.last_exploits],
            "bandit": self.bandit.to_dict() if self.bandit else None,
        }

    def load_dict(self, data: dict[str, Any]) -> None:
        """Inverse of `to_dict`."""
        self.last_tactic_id = str(data.get("last_tactic_id", ""))
        self.last_exploits = [HabitExploit.model_validate(e) for e in data.get("last_exploits", [])]
        if data.get("bandit"):
            self.bandit = LinUCB.from_dict(data["bandit"])

    # ------------------------------------------------------------------ #
    def apply_tactic(
        self,
        tactic: Tactic,
        difficulty: DifficultyVector,
        rng: np.random.Generator,
        entity_count: int,
        index: int,
    ) -> ThreatSpec:
        """Turn a chosen tactic into a concrete hostile wave."""
        from app.core.generator_support import resolve_difficulty

        resolved = resolve_difficulty(difficulty)
        bearings = list(tactic.approach_bearings) or [45]
        bearing = float(bearings[index % len(bearings)])
        count = max(1, min(entity_count, resolved.swarm_size))
        behaviour = BehaviourSpec(
            kind=BehaviourKind.SWARM if count > 1 else BehaviourKind.TERMINAL_DIVE,
            separation_m=float(rng.uniform(14.0, 30.0)),
            terrain_following=tactic.altitude_profile == "terrain_hugging",
            min_clearance_m=float(rng.uniform(12.0, 35.0)),
            speed_mps=float(
                {"slow": 12.0, "medium": 18.0, "fast": 26.0}[tactic.speed_profile]
                * float(rng.uniform(0.9, 1.1))
            ),
            terminal_altitude_m=20.0 if tactic.altitude_profile == "terrain_hugging" else 70.0,
        )
        return ThreatSpec(
            name=f"rb{index}_{tactic.id}",
            entity_class=EntityClass.QUAD_SMALL,
            label=LabelType.HOSTILE,
            count=count,
            spawn_t=0.0,
            spawn_window_s=0.0
            if tactic.timing == "simultaneous"
            else float(rng.uniform(8.0, 30.0)),
            spawn_bearing_deg=bearing,
            spawn_jitter_deg=4.0 if tactic.timing == "simultaneous" else 18.0,
            behaviour=behaviour,
            formation=tactic.formation,
            rf_active=None,
            hint_bias=float(difficulty.ambiguity),
        )

    def suggest_trajectory(
        self, tactic: Tactic, position: np.ndarray, index: int, member_count: int
    ) -> np.ndarray:
        """Optional Layer-B steering vector for a swarm member.

        With no learned policy this returns the formation's cohesion vector so
        the learned policy can be dropped in without touching call sites.
        """
        del tactic
        if member_count <= 1:
            return np.zeros(3, dtype=np.float64)
        offset = (index % member_count) - (member_count / 2.0)
        direction = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        return (direction * offset * 20.0).astype(np.float64)


def _sector_name(index: int) -> str:
    """Compass name of one of the eight 45-degree sectors."""
    names = ("E", "NE", "N", "NW", "W", "SW", "S", "SE")
    return names[index % len(names)]


# --------------------------------------------------------------------------- #
# Layer B (optional)
# --------------------------------------------------------------------------- #


def load_learned_policy(path: Any) -> Any | None:
    """Load the optional PPO swarm policy, returning None when unavailable."""
    try:
        from stable_baselines3 import PPO
    except Exception:
        return None
    try:
        from pathlib import Path

        policy_path = Path(path)
        if not policy_path.is_file():
            return None
        return PPO.load(str(policy_path), device="cpu")
    except Exception:  # pragma: no cover - corrupt or mismatched policy
        return None


def describe_policy_availability() -> dict[str, Any]:
    """Report whether Layer B is usable. Used by `/api/health`."""
    from app.config import get_settings

    settings = get_settings()
    available = False
    reason = "disabled"
    if not settings.swarm_policy_enabled:
        reason = "disabled by configuration"
    elif not settings.swarm_policy_path.is_file():
        reason = f"no policy file at {settings.swarm_policy_path}"
    else:
        try:
            import stable_baselines3  # noqa: F401

            available = True
            reason = "policy loaded"
        except Exception:
            reason = "stable-baselines3 not installed"
    return {"available": available, "reason": reason, "path": str(settings.swarm_policy_path)}
