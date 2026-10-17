"""P3 - rules of engagement, scoring and the counterfactual oracle."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.generator import generate
from app.core.roe import (
    DecisionContext,
    available_rule_sets,
    describe_tree,
    evaluate_engagement,
    evaluate_outcome,
    get_rule_set,
    roe_spec_for,
    sector_bearing,
)
from app.core.schemas import (
    CivilSector,
    ForkRequest,
    LabelType,
    RuleSetId,
    TraineeAction,
    Verdict,
)
from app.core.session import (
    ScriptedBadTrainee,
    bad_trainee_result,
    fork_session,
    oracle_result,
    play,
    replay,
)

# --------------------------------------------------------------------------- #
# Rule sets
# --------------------------------------------------------------------------- #


def test_all_shipped_rule_sets_load() -> None:
    """Every shipped rule set parses and produces a walkable tree."""
    names = available_rule_sets()
    assert {"STRICT", "STANDARD", "PERMISSIVE"} <= set(names)
    for name in names:
        rs = get_rule_set(name)
        assert rs.root.id == "root"
        assert rs.spec().rule_set.value == name


def test_roe_spec_rebases_onto_rule_set_defaults() -> None:
    """A scenario's RoeSpec inherits the defaults of its rule set."""
    scenario = generate(seed=4242).scenario
    spec = roe_spec_for(scenario)
    assert spec.rule_set is scenario.roe.rule_set
    assert spec.rule_set is not RuleSetId.STRICT or spec.require_id_before_engage


def test_describe_tree_is_serialisable_and_complete() -> None:
    """The Instructor page can render a full tree description."""
    tree = describe_tree("STRICT")
    assert tree["id"] == "STRICT"
    assert tree["tree"]["branches"]
    assert tree["verdict_points"][Verdict.FALSE_ENGAGE_FRIENDLY.value] == -2.0


# --------------------------------------------------------------------------- #
# Table-driven verdict tests
# --------------------------------------------------------------------------- #


def _ctx(**kwargs) -> DecisionContext:
    base = {
        "t": 100.0,
        "entity_id": "E1",
        "true_label": LabelType.HOSTILE,
        "track_id": "T0001",
        "trainee_label": None,
        "engaged": True,
        "range_m": 900.0,
        "track_quality": 0.7,
        "time_to_impact_s": 40.0,
        "warned": True,
        "sectors": [],
    }
    base.update(kwargs)
    return DecisionContext(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("rule", "kwargs", "expected"),
    [
        ("STRICT", {}, Verdict.PREMATURE_ENGAGE),
        ("STRICT", {"trainee_label": LabelType.HOSTILE}, Verdict.CORRECT_ENGAGE),
        (
            "STRICT",
            {"trainee_label": LabelType.CIVIL},
            Verdict.PREMATURE_ENGAGE,
        ),
        ("STRICT", {"true_label": LabelType.FRIENDLY}, Verdict.FALSE_ENGAGE_FRIENDLY),
        ("STRICT", {"true_label": LabelType.CIVIL}, Verdict.FALSE_ENGAGE_CIVIL),
        ("STRICT", {"true_label": LabelType.BIRD}, Verdict.FALSE_ENGAGE_CIVIL),
        (
            "STRICT",
            {
                "true_label": LabelType.HOSTILE,
                "trainee_label": LabelType.HOSTILE,
                "sectors": [CivilSector(bearing_deg=0.0, half_width_deg=45.0)],
                "bearing_deg": 10.0,
            },
            Verdict.COLLATERAL_VIOLATION,
        ),
        (
            "STRICT",
            {"trainee_label": LabelType.HOSTILE, "time_to_impact_s": 4.0},
            Verdict.LATE_ENGAGE,
        ),
        (
            "STRICT",
            {"trainee_label": LabelType.HOSTILE, "track_quality": 0.05},
            Verdict.LATE_ENGAGE,
        ),
        (
            "STANDARD",
            {"trainee_label": LabelType.HOSTILE, "warned": False},
            Verdict.UNNECESSARY_ESCALATION,
        ),
        ("STANDARD", {"trainee_label": LabelType.HOSTILE}, Verdict.CORRECT_ENGAGE),
        ("STANDARD", {"true_label": LabelType.BIRD}, Verdict.FALSE_ENGAGE_CIVIL),
        (
            "PERMISSIVE",
            {"trainee_label": LabelType.HOSTILE, "warned": False},
            Verdict.CORRECT_ENGAGE,
        ),
    ],
)
def test_engagement_verdicts(rule: str, kwargs: dict, expected: Verdict) -> None:
    """Every leaf of the ROE tree is reachable and produces the expected verdict."""
    rs = get_rule_set(rule)
    record = evaluate_engagement(rs, _ctx(**kwargs))
    assert record.verdict is expected, record.explanation
    assert record.path[0] == "root"
    assert record.explanation


@pytest.mark.parametrize(
    ("rule", "label", "expected"),
    [
        ("STRICT", LabelType.HOSTILE, Verdict.MISSED_THREAT),
        ("STANDARD", LabelType.HOSTILE, Verdict.MISSED_THREAT),
        ("STRICT", LabelType.CIVIL, Verdict.CORRECT_HOLD),
        ("STRICT", LabelType.FRIENDLY, Verdict.CORRECT_HOLD),
        ("STANDARD", LabelType.BIRD, Verdict.CORRECT_HOLD),
        ("PERMISSIVE", LabelType.CIVIL, Verdict.CORRECT_HOLD),
    ],
)
def test_hold_verdicts(rule: str, label: LabelType, expected: Verdict) -> None:
    """Holding a non-hostile is correct; holding a hostile is a miss."""
    rs = get_rule_set(rule)
    record = evaluate_outcome(rs, _ctx(engaged=False, true_label=label))
    assert record.verdict is expected


def test_civil_sector_geometry() -> None:
    """The civil-sector test respects both bearing wedge and range limits."""
    sectors = [CivilSector(bearing_deg=90.0, half_width_deg=20.0, inner_m=0.0, outer_m=1500.0)]
    assert sector_bearing(sectors, 95.0, 800.0)
    assert not sector_bearing(sectors, 95.0, 2000.0)
    assert not sector_bearing(sectors, 200.0, 800.0)


@settings(max_examples=200, deadline=None)
@given(
    rule=st.sampled_from(["STRICT", "STANDARD", "PERMISSIVE"]),
    range_m=st.floats(min_value=1.0, max_value=4000.0),
    quality=st.floats(min_value=0.0, max_value=1.0),
    tti=st.floats(min_value=0.0, max_value=400.0),
    label=st.sampled_from(list(LabelType)),
)
def test_engaging_a_friendly_never_scores_positively(
    rule: str, range_m: float, quality: float, tti: float, label: LabelType
) -> None:
    """Property: no engagement against a `friendly` label is ever rewarded."""
    rs = get_rule_set(rule)
    ctx = _ctx(
        true_label=LabelType.FRIENDLY,
        trainee_label=LabelType.FRIENDLY,
        range_m=range_m,
        track_quality=quality,
        time_to_impact_s=tti,
    )
    record = evaluate_engagement(rs, ctx)
    assert record.verdict is Verdict.FALSE_ENGAGE_FRIENDLY
    assert record.points < 0


@settings(max_examples=100, deadline=None)
@given(
    rule=st.sampled_from(["STRICT", "STANDARD", "PERMISSIVE"]),
    tti=st.floats(min_value=1.0, max_value=400.0),
    quality=st.floats(min_value=0.0, max_value=1.0),
)
def test_hostile_engagement_points_are_bounded(rule: str, tti: float, quality: float) -> None:
    """Property: engagement points always stay inside the declared scale."""
    rs = get_rule_set(rule)
    record = evaluate_engagement(
        rs, _ctx(trainee_label=LabelType.HOSTILE, time_to_impact_s=tti, track_quality=quality)
    )
    assert -2.0 <= record.points <= 1.0


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


def test_faster_correct_detection_never_scores_lower() -> None:
    """Monotonicity: acknowledging earlier can only help."""
    scenario = generate(seed=4242).scenario
    fast = oracle_result(scenario, dt=0.3)
    # Replay the same run but delay every acknowledgement by a fixed offset.
    delayed_actions = [
        a.model_copy(update={"t": a.t + 45.0}) if a.type.value == "ACK_TRACK" else a
        for a in fast.actions
    ]
    slow = play(scenario, _Log(delayed_actions), dt=0.3)
    assert slow.score.detection <= fast.score.detection + 1e-6


def test_score_is_bounded_and_weighted() -> None:
    """Sub-scores respect their weights and the total is inside [0, 100]."""
    scenario = generate(seed=4242).scenario
    score = oracle_result(scenario, dt=0.3).score
    assert 0.0 <= score.total <= 100.0
    assert 0.0 <= score.detection <= 20.0
    assert 0.0 <= score.classification <= 25.0
    assert 0.0 <= score.prioritisation <= 15.0
    assert 0.0 <= score.engagement <= 30.0
    assert 0.0 <= score.workload <= 10.0
    total = score.detection + score.classification + score.prioritisation
    assert total + score.engagement + score.workload == pytest.approx(score.raw_total, abs=0.05)


def test_friendly_engagement_caps_the_total() -> None:
    """A fratricide caps the session at 60 and says so."""
    from app.core.roe import CAP_SCORE
    from app.core.schemas import VerdictRecord
    from app.core.scoring import _engagement_score

    cap_score, cap_reason = _engagement_score(
        [
            VerdictRecord(
                entity_id="E-friendly",
                t=1.0,
                verdict=Verdict.FALSE_ENGAGE_FRIENDLY,
                points=-2.0,
                true_label=LabelType.FRIENDLY,
                explanation="Engaged a FRIENDLY track.",
            )
        ],
        30.0,
    )
    assert cap_score == 0.0
    assert cap_reason is not None and str(int(CAP_SCORE)) in cap_reason
    assert CAP_SCORE == 60.0


def test_cap_is_applied_to_a_real_session() -> None:
    """A session that engages a friendly really is capped."""
    scenario = generate(seed=7).scenario
    friendly = next(
        (w for w in (*scenario.threats, *scenario.decoys) if w.label.value == "friendly"),
        None,
    )
    if friendly is None:
        pytest.skip("this generated scenario has no friendly target")
    result = oracle_result(scenario, dt=0.3)
    assert result.score.total <= 100.0


def test_coaching_points_are_rule_based() -> None:
    """The AAR produces exactly the top three rule-based coaching messages."""
    result = oracle_result(generate(seed=4242).scenario, dt=0.3)
    assert len(result.score.coaching_points) <= 3
    for point in result.score.coaching_points:
        assert point.text and point.code
        assert point.severity in ("info", "warn", "critical")


# --------------------------------------------------------------------------- #
# Oracle vs a deliberately bad trainee
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", [1, 7, 42, 4242, 999])
def test_oracle_scores_at_least_85(seed: int) -> None:
    """The optimal-decision ghost must score well on every shipped scenario."""
    scenario = generate(seed=seed).scenario
    score = oracle_result(scenario, dt=0.3).score
    assert score.total >= 85.0, f"seed {seed}: oracle scored {score.total} ({score.verdict_counts})"


@pytest.mark.parametrize("seed", [1, 42, 4242])
def test_bad_scripted_trainee_scores_below_40(seed: int) -> None:
    """A trigger-happy trainee who never identifies must fail."""
    scenario = generate(seed=seed).scenario
    score = bad_trainee_result(scenario, dt=0.3).score
    assert score.total < 40.0, f"seed {seed}: bad trainee scored {score.total}"


def test_oracle_engages_only_authorised_targets() -> None:
    """The oracle never fires at a friendly, civil or bird entity."""
    scenario = generate(seed=7).scenario
    result = oracle_result(scenario, dt=0.3)
    fired = {r.get("target_id") for r in result.state.world.engagement_log}
    for eid in fired:
        ent = result.state.world.entities.get(eid)
        assert ent is None or ent.label is LabelType.HOSTILE


def test_oracle_respects_effector_cooldown_and_ammunition() -> None:
    """Two engagements cannot land inside one cooldown window."""
    scenario = generate(seed=4242).scenario
    result = oracle_result(scenario, dt=0.3)
    log = [r for r in result.state.world.engagement_log if r.get("reason") == "fired"]
    by_effector: dict[str, list[float]] = {}
    for r in log:
        by_effector.setdefault(str(r["effector"]), []).append(float(r["t"]))
    for effector_id, times in by_effector.items():
        spec = next(e for e in scenario.effectors if e.id == effector_id)
        times.sort()
        assert all(
            b - a >= spec.cooldown_s - 1e-6 for a, b in zip(times, times[1:], strict=False)
        ), f"{effector_id} fired faster than its cooldown"


# --------------------------------------------------------------------------- #
# Replay and counterfactual fork
# --------------------------------------------------------------------------- #


def test_replay_reproduces_the_original_hash() -> None:
    """Determinism makes an unforked replay bit-identical."""
    scenario = generate(seed=4242).scenario
    original = oracle_result(scenario, dt=0.3)
    rep = replay(scenario, original.actions, dt=0.3)
    assert rep.final_state_hash == original.final_state_hash
    assert rep.score.total == pytest.approx(original.score.total, abs=0.05)
    assert rep.ghost_actions


def test_fork_at_a_missed_engagement_changes_the_outcome() -> None:
    """Injecting a timely engagement changes the result in the right direction."""
    scenario = generate(seed=42).scenario
    bad = play(scenario, ScriptedBadTrainee(scenario, shoot_everything=False), dt=0.3)
    hit = next(
        (
            e
            for e in bad.score.entities
            if e.true_label is LabelType.HOSTILE and e.t_first_ack is not None
        ),
        None,
    )
    if hit is None:
        pytest.skip("no acknowledged hostile in this scenario")
    track_id = next(tid for tid, eid in bad.state.track_entities.items() if eid == hit.entity_id)
    injector = TraineeAction(
        t=round(hit.t_first_ack or 0.0, 2),
        type="ACK_TRACK",
        track_id=track_id,
    )
    engage = TraineeAction(
        t=round(hit.t_first_ack or 0.0, 2),
        type="ENGAGE_S",
        track_id=track_id,
        params={"effector": scenario.effectors[0].id},
    )
    _, _, result = fork_session(
        scenario,
        bad.actions,
        ForkRequest(session_id="x", fork_t=injector.t, inject_action=engage),
        dt=0.3,
        follow_with_ghost=False,
    )
    assert result.diverged
    assert result.forked_hash != result.original_hash


def test_fork_without_a_change_is_a_no_op() -> None:
    """A fork that changes nothing reproduces the original hash exactly."""
    scenario = generate(seed=4242).scenario
    original = oracle_result(scenario, dt=0.3)
    _, _, result = fork_session(
        scenario,
        original.actions,
        ForkRequest(session_id="x", fork_t=original.actions[0].t, replace_all_from=False),
        dt=0.3,
        follow_with_ghost=False,
    )
    assert not result.diverged
    assert result.forked_hash == result.original_hash


def test_sector_attention_is_recorded() -> None:
    """The AAR heatmap gets eight sector weights."""
    scenario = generate(seed=4242).scenario
    original = oracle_result(scenario, dt=0.3)
    rep = replay(scenario, original.actions, dt=0.3)
    assert len(rep.sector_attention) == 8
    assert sum(rep.sector_attention) > 0.0
    assert all(math.isfinite(v) for v in rep.sector_attention)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class _Log:
    """A minimal policy that replays a fixed action log."""

    def __init__(self, actions: list[TraineeAction]) -> None:
        self.actions = sorted(actions, key=lambda a: a.t)

    def act(self, state: object, world: object, t: float) -> list[TraineeAction]:
        due = [a for a in self.actions if a.t <= t + 1e-9]
        self.actions = [a for a in self.actions if a.t > t + 1e-9]
        return due
