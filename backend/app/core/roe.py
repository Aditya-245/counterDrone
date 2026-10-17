"""The rules-of-engagement decision tree.

The ROE is data, not code: each rule set is a JSON document under
``app/core/rulesets/`` describing a tree of conditions and leaves. Every
evaluation returns the *path* it walked plus a plain-English explanation, so
the Instructor page can render the tree and the AAR can highlight the branch
that fired.

Supported condition operators
-----------------------------
``always``               always true
``engagement``           the decision being evaluated is an engagement
``no_engagement``        the decision being evaluated is a hold
``label_is``             ground-truth label equals ``value``
``label_in``             ground-truth label is one of ``value``
``trainee_label_is``     the label the trainee chose equals ``value``
``no_identification``    the trainee never classified this track
``identification_wrong`` the trainee classified it, and got it wrong
``in_civil_sector``      the target bearing falls in a declared civil sector
``in_civil_sector_strict`` ... beyond the rule set's tolerated wedge
``tti_below``            time to the perimeter is under ``value`` seconds
``track_quality_below``  track quality is under ``value``
``no_warning``           no warning was issued before the engagement
``engagement_out_of_range`` the effector could not reach the target
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from app.core.schemas import (
    CivilSector,
    LabelType,
    RoeSpec,
    RuleSetId,
    Scenario,
    Verdict,
    VerdictRecord,
)

__all__ = [
    "DecisionContext",
    "RoeRuleSet",
    "RoeTreeNode",
    "VERDICT_POINTS",
    "available_rule_sets",
    "describe_tree",
    "evaluate_engagement",
    "evaluate_outcome",
    "get_rule_set",
    "roe_spec_for",
    "sector_bearing",
]

RULESET_DIR: Final[Path] = Path(__file__).resolve().parent / "rulesets"

#: Nominal score contribution of each verdict, used for UI colour and for the
#: engagement sub-score normalisation.
VERDICT_POINTS: Final[dict[Verdict, float]] = {
    Verdict.CORRECT_ENGAGE: 1.0,
    Verdict.CORRECT_HOLD: 1.0,
    Verdict.LATE_ENGAGE: 0.35,
    Verdict.MISSED_THREAT: 0.0,
    Verdict.FALSE_ENGAGE_CIVIL: -1.2,
    Verdict.FALSE_ENGAGE_FRIENDLY: -2.0,
    Verdict.COLLATERAL_VIOLATION: -2.0,
    Verdict.PREMATURE_ENGAGE: -0.6,
    Verdict.UNNECESSARY_ESCALATION: -0.4,
}

#: Verdicts that cap the total session score, per the scoring specification.
CAP_VERDICTS: Final[tuple[Verdict, ...]] = (
    Verdict.FALSE_ENGAGE_FRIENDLY,
    Verdict.COLLATERAL_VIOLATION,
)

CAP_SCORE: Final[float] = 60.0


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class DecisionContext:
    """Everything the ROE tree is allowed to look at for one decision point.

    Ground truth (`true_label`) is present because the ROE engine is the
    post-hoc assessor. It is never part of what the trainee sees.
    """

    t: float
    entity_id: str
    true_label: LabelType
    track_id: str | None = None
    trainee_label: LabelType | None = None
    engaged: bool = False
    engagement_type: str | None = None
    effector: str | None = None
    range_m: float | None = None
    track_quality: float = 0.5
    time_to_impact_s: float | None = None
    warned: bool = False
    in_range: bool = True
    sectors: list[CivilSector] = field(default_factory=list)
    collateral_tolerance: float = 0.0
    bearing_deg: float = 0.0
    reached_perimeter: bool = False
    require_warning: bool = True

    @property
    def identified(self) -> bool:
        """True when the trainee explicitly classified this track."""
        return self.trainee_label is not None


@dataclass(slots=True)
class RoeTreeNode:
    """One node of a loaded ROE tree."""

    id: str
    condition: dict[str, Any]
    branches: list[RoeTreeNode] = field(default_factory=list)
    leaf: dict[str, Any] | None = None

    @property
    def is_leaf(self) -> bool:
        """True when the node terminates in a verdict."""
        return self.leaf is not None


@dataclass(slots=True)
class RoeRuleSet:
    """A loaded, inspectable rule set."""

    id: str
    title: str
    description: str
    defaults: dict[str, Any]
    root: RoeTreeNode

    def spec(self, overrides: dict[str, Any] | None = None) -> RoeSpec:
        """Build a `RoeSpec` from this rule set plus optional overrides."""
        data = {**self.defaults, **(overrides or {})}
        data["rule_set"] = RuleSetId(self.id)
        return RoeSpec.model_validate(data)


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _parse_node(raw: dict[str, Any]) -> RoeTreeNode:
    """Recursively turn a JSON node into a `RoeTreeNode`."""
    node = RoeTreeNode(
        id=str(raw.get("id", "node")),
        condition=dict(raw.get("condition", {"op": "always"})),
    )
    for branch in raw.get("branches", []):
        node.branches.append(_parse_node(branch))
    if "leaf" in raw:
        node.leaf = dict(raw["leaf"])
    return node


@lru_cache(maxsize=8)
def _load(rule_set_id: str) -> RoeRuleSet:
    """Load and parse one rule set JSON file."""
    path = RULESET_DIR / f"{rule_set_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"unknown rule set: {rule_set_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return RoeRuleSet(
        id=str(raw["id"]),
        title=str(raw["title"]),
        description=str(raw["description"]),
        defaults=dict(raw["defaults"]),
        root=_parse_node(raw["tree"]),
    )


def get_rule_set(rule_set_id: RuleSetId | str) -> RoeRuleSet:
    """Return the loaded rule set for `rule_set_id`."""
    name = rule_set_id.value if isinstance(rule_set_id, RuleSetId) else str(rule_set_id)
    return _load(name)


def available_rule_sets() -> list[str]:
    """Names of every shipped rule set."""
    return sorted(p.stem for p in RULESET_DIR.glob("*.json"))


def roe_spec_for(scenario: Scenario) -> RoeSpec:
    """Resolve a scenario's `RoeSpec`, re-basing it on its rule set defaults."""
    rule_set = get_rule_set(scenario.roe.rule_set)
    overrides = scenario.roe.model_dump(mode="python")
    overrides.pop("rule_set", None)
    return rule_set.spec(overrides)


# --------------------------------------------------------------------------- #
# Conditions
# --------------------------------------------------------------------------- #


def sector_bearing(sectors: list[CivilSector], bearing_deg: float, distance_m: float) -> bool:
    """True when `(bearing, distance)` falls inside any declared civil sector."""
    for sec in sectors:
        if not (sec.inner_m <= distance_m <= sec.outer_m):
            continue
        delta = ((bearing_deg - sec.bearing_deg + 180.0) % 360.0) - 180.0
        if abs(delta) <= sec.half_width_deg:
            return True
    return False


def _in_civil_sector(ctx: DecisionContext, strict: bool) -> bool:
    """Civil-sector test; `strict` ignores the tolerated wedge."""
    if not ctx.sectors:
        return False
    if sector_bearing(ctx.sectors, ctx.bearing_deg, ctx.range_m or 0.0):
        return True
    if strict:
        return False
    for sec in ctx.sectors:
        delta = ((ctx.bearing_deg - sec.bearing_deg + 180.0) % 360.0) - 180.0
        if abs(delta) <= sec.half_width_deg * (1.0 + max(0.0, ctx.collateral_tolerance)):
            return True
    return False


def _numeric(raw: Any, op: str) -> float:
    """Coerce a numeric condition's threshold.

    ROE documents are data (authored, stored or generated), so a missing or
    non-numeric threshold is a data error and is reported as one instead of
    surfacing as a bare `TypeError`.
    """
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ROE condition {op!r} needs a numeric value, got {raw!r}") from exc


def _matches(op: str, spec: dict[str, Any], ctx: DecisionContext) -> bool:
    """Evaluate one condition against a `DecisionContext`.

    `spec["value"]` is operator-specific: a label name for `label_is`, a list of
    label names for `label_in`, and a number for the threshold operators. It is
    therefore coerced lazily, inside the branch that needs it.
    """
    raw = spec.get("value")
    match op:
        case "always":
            return True
        case "engagement":
            return ctx.engaged
        case "no_engagement":
            return not ctx.engaged
        case "label_is":
            return ctx.true_label is LabelType(str(raw))
        case "label_in":
            return ctx.true_label in {LabelType(v) for v in (raw or [])}
        case "trainee_label_is":
            return ctx.trainee_label is not None and ctx.trainee_label is LabelType(str(raw))
        case "no_identification":
            return ctx.trainee_label is None
        case "identification_wrong":
            return ctx.trainee_label is not None and ctx.trainee_label is not ctx.true_label
        case "in_civil_sector":
            return _in_civil_sector(ctx, strict=True)
        case "in_civil_sector_strict":
            return _in_civil_sector(ctx, strict=True)
        case "tti_below":
            tti = ctx.time_to_impact_s
            return tti is not None and math.isfinite(tti) and tti < _numeric(raw, op)
        case "track_quality_below":
            return ctx.track_quality < _numeric(raw, op)
        case "no_warning":
            return ctx.require_warning and not ctx.warned
        case "engagement_out_of_range":
            return not ctx.in_range
        case _:
            raise ValueError(f"unknown ROE condition operator {op!r}")


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def _walk(
    node: RoeTreeNode, ctx: DecisionContext, path: list[str]
) -> tuple[Verdict, float, str, list[str]]:
    """Depth-first walk of the tree returning `(verdict, points, text, path)`."""
    path.append(node.id)
    if node.is_leaf:
        leaf = node.leaf or {}
        verdict = Verdict(leaf["verdict"])
        points = float(leaf.get("points", VERDICT_POINTS[verdict]))
        text = str(leaf.get("explanation", "")).format(
            true_label=ctx.true_label.value,
            tti=ctx.time_to_impact_s if ctx.time_to_impact_s is not None else float("nan"),
            range_m=ctx.range_m if ctx.range_m is not None else float("nan"),
        )
        return verdict, points, text, path
    for branch in node.branches:
        if _matches(str(branch.condition.get("op", "always")), branch.condition, ctx):
            return _walk(branch, ctx, path)
    raise ValueError(
        f"ROE rule set walked off the tree at node {node.id!r}; no branch matched "
        f"(label={ctx.true_label.value}, engaged={ctx.engaged})"
    )


def evaluate(rule_set: RoeRuleSet, ctx: DecisionContext) -> VerdictRecord:
    """Run the tree once and return a full `VerdictRecord`."""
    verdict, points, text, path = _walk(rule_set.root, ctx, [])
    return VerdictRecord(
        entity_id=ctx.entity_id,
        track_id=ctx.track_id,
        t=round(ctx.t, 3),
        verdict=verdict,
        points=points,
        path=path,
        explanation=text,
        true_label=ctx.true_label,
        trainee_label=ctx.trainee_label,
        effector=ctx.effector,
        range_m=None if ctx.range_m is None else round(ctx.range_m, 1),
        collateral_risk=ctx.engaged and _in_civil_sector(ctx, strict=True),
    )


def evaluate_engagement(rule_set: RoeRuleSet, ctx: DecisionContext) -> VerdictRecord:
    """Evaluate a decision point at which the trainee engaged."""
    ctx.engaged = True
    return evaluate(rule_set, ctx)


def evaluate_outcome(rule_set: RoeRuleSet, ctx: DecisionContext) -> VerdictRecord:
    """Evaluate the end-of-scenario hold-or-engage outcome for one entity."""
    ctx.engaged = False
    return evaluate(rule_set, ctx)


def describe_tree(rule_set_id: RuleSetId | str) -> dict[str, Any]:
    """Serialisable description of a rule set for the Instructor page."""
    rs = get_rule_set(rule_set_id)

    def node_repr(node: RoeTreeNode) -> dict[str, Any]:
        out: dict[str, Any] = {"id": node.id, "condition": node.condition}
        if node.is_leaf:
            out["leaf"] = node.leaf
        else:
            out["branches"] = [node_repr(b) for b in node.branches]
        return out

    return {
        "id": rs.id,
        "title": rs.title,
        "description": rs.description,
        "defaults": rs.defaults,
        "verdict_points": {k.value: v for k, v in VERDICT_POINTS.items()},
        "tree": node_repr(rs.root),
    }
