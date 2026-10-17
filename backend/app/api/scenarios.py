"""Scenario storage, the library, and recommended-for-you selection."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import not_found, unprocessable
from app.core.generator import generate, sanity_check
from app.core.roe import available_rule_sets, describe_tree
from app.core.schemas import (
    DifficultyVector,
    OkResponse,
    Scenario,
    TerrainPreset,
)
from app.db.models import ScenarioRow
from app.db.session import session_scope

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


class ScenarioSummary(BaseModel):
    """Lightweight row used by the Library page."""

    id: str
    name: str
    description: str
    seed: int
    duration_s: int
    difficulty: float
    tags: list[str]
    source: str
    threat_count: int
    hostile_count: int
    decoy_count: int
    terrain: str
    time_of_day: str
    rule_set: str
    oracle_score: float | None = None
    recommended: bool = False
    recommended_reason: str = ""


class ScenarioList(BaseModel):
    """Library response."""

    items: list[ScenarioSummary]
    total: int
    tags: list[str]
    rule_sets: list[str]


class GenerateRequest(BaseModel):
    """Body for `POST /api/scenarios/generate`."""

    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    difficulty: DifficultyVector | None = None
    tags: list[str] = Field(default_factory=list)
    terrain_preset: TerrainPreset | None = None
    duration_s: int | None = Field(default=None, ge=60, le=3600)
    trainee_id: str | None = None
    persist: bool = True


class ValidateResponse(BaseModel):
    """Result of validating a scenario document."""

    valid: bool
    errors: list[str]
    sanity_ok: bool
    sanity_message: str


def _summary(
    row: ScenarioRow, scenario: Scenario, recommended: bool, reason: str
) -> ScenarioSummary:
    return ScenarioSummary(
        id=row.id,
        name=row.name,
        description=scenario.description,
        seed=row.seed,
        duration_s=scenario.duration_s,
        difficulty=round(scenario.difficulty.mean, 4),
        tags=list(row.tags_json or []),
        source=row.source,
        threat_count=scenario.threat_count(),
        hostile_count=scenario.hostile_count(),
        decoy_count=sum(w.count for w in scenario.decoys),
        terrain=scenario.terrain.preset.value,
        time_of_day=scenario.environment.time_of_day.value,
        rule_set=scenario.roe.rule_set.value,
        oracle_score=row.oracle_score,
        recommended=recommended,
        recommended_reason=reason,
    )


def load_scenario_row(row: ScenarioRow) -> Scenario:
    """Parse a stored scenario body back into the model."""
    return Scenario.model_validate(row.body_json)


def upsert_scenario(
    scenario: Scenario, source: str = "generated", oracle_score: float | None = None
) -> ScenarioRow:
    """Insert or update a scenario row."""
    with session_scope() as db:
        row = db.get(ScenarioRow, scenario.id)
        if row is None:
            row = ScenarioRow(id=scenario.id, created_at=None)  # type: ignore[arg-type]
            db.add(row)
        row.name = scenario.name
        row.schema_version = scenario.schema_version
        row.seed = scenario.seed
        row.difficulty_json = scenario.difficulty.model_dump(mode="json")
        row.tags_json = scenario.tags
        row.body_json = scenario.model_dump(mode="json")
        row.source = source
        if oracle_score is not None:
            row.oracle_score = oracle_score
        return row


def recommended_ids(trainee_id: str | None) -> tuple[dict[str, str], str]:
    """Scenario ids recommended for a trainee, with the reason shown in the UI.

    The recommendation is *materialised*: a matching scenario is generated and
    stored, so the Library page has a concrete thing to offer.
    """
    if not trainee_id:
        return ({}, "")
    from app.db.repositories import load_skill_model_by_id

    model = load_skill_model_by_id(trainee_id)
    if model is None or model.sessions == 0:
        return ({}, "")
    difficulty, weakest = model.recommend_difficulty()
    seed = _stable_seed(trainee_id)
    result = generate(
        seed=seed,
        difficulty=difficulty,
        tags=["recommended", weakest],
        name=f"Recommended: train {weakest}",
    )
    if result.sanity_ok:
        upsert_scenario(result.scenario, source="generated")
    reason = (
        f"Your weakest skill is {weakest} (rating "
        f"{float(getattr(model.skills, weakest)):.0f}/100). This scenario targets roughly "
        "70% predicted success on that skill."
    )
    return ({result.scenario.id: reason}, reason)


def _stable_seed(trainee_id: str) -> int:
    """Deterministic per-trainee seed so the recommendation is reproducible."""
    import hashlib

    return int.from_bytes(
        hashlib.blake2b(trainee_id.encode("utf-8"), digest_size=4).digest(), "big"
    ) % (2**31 - 1)


@router.get("", response_model=ScenarioList)
def list_scenarios(
    tag: str | None = Query(default=None, description="Filter by a single tag"),
    source: str | None = Query(default=None, description="shipped | generated | builder | nlu"),
    trainee_id: str | None = Query(default=None),
) -> ScenarioList:
    """The scenario library, with tags and the recommendation reasons."""
    rec_ids, _reason = recommended_ids(trainee_id)
    with session_scope() as db:
        stmt = select(ScenarioRow).order_by(ScenarioRow.source, ScenarioRow.name)
        if source:
            stmt = stmt.where(ScenarioRow.source == source)
        rows = list(db.execute(stmt).scalars())
    items: list[ScenarioSummary] = []
    all_tags: set[str] = set()
    for row in rows:
        try:
            scenario = load_scenario_row(row)
        except Exception:  # pragma: no cover - defensive against a corrupt row
            continue
        all_tags.update(row.tags_json or [])
        if tag and tag not in (row.tags_json or []):
            continue
        items.append(_summary(row, scenario, row.id in rec_ids, rec_ids.get(row.id, "")))
    return ScenarioList(
        items=items,
        total=len(items),
        tags=sorted(all_tags),
        rule_sets=available_rule_sets(),
    )


@router.get("/rulesets/{rule_set_id}")
def read_rule_set(rule_set_id: str) -> dict[str, Any]:
    """Full ROE tree description for the Instructor page."""
    try:
        return describe_tree(rule_set_id)
    except (FileNotFoundError, KeyError) as exc:
        raise not_found("rule set", rule_set_id) from exc


@router.get("/recommended", response_model=ScenarioList)
def list_recommended(trainee_id: str = Query(...)) -> ScenarioList:
    """Scenarios the skill model recommends for this trainee."""
    rec_ids, _ = recommended_ids(trainee_id)
    with session_scope() as db:
        rows = list(db.execute(select(ScenarioRow)).scalars())
    items = []
    for row in rows:
        try:
            scenario = load_scenario_row(row)
        except Exception:  # pragma: no cover
            continue
        items.append(_summary(row, scenario, row.id in rec_ids, rec_ids.get(row.id, "")))
    items.sort(key=lambda s: (not s.recommended, s.name))
    return ScenarioList(items=items, total=len(items), tags=[], rule_sets=available_rule_sets())


@router.get("/{scenario_id}", response_model=Scenario)
def read_scenario(scenario_id: str) -> Scenario:
    """One scenario document."""
    with session_scope() as db:
        row = db.get(ScenarioRow, scenario_id)
        if row is None:
            raise not_found("scenario", scenario_id)
        return load_scenario_row(row)


@router.get("/{scenario_id}/summary", response_model=ScenarioSummary)
def read_scenario_summary(scenario_id: str, trainee_id: str | None = None) -> ScenarioSummary:
    """Library-row view of one scenario."""
    rec_ids, _ = recommended_ids(trainee_id)
    with session_scope() as db:
        row = db.get(ScenarioRow, scenario_id)
        if row is None:
            raise not_found("scenario", scenario_id)
        scenario = load_scenario_row(row)
        return _summary(row, scenario, row.id in rec_ids, rec_ids.get(row.id, ""))


@router.post("/validate", response_model=ValidateResponse)
def validate(body: Scenario) -> ValidateResponse:
    """Structural + sanity validation of a scenario document."""
    ok, message = sanity_check(body)
    errors: list[str] = []
    if not ok:
        errors.append(message)
    return ValidateResponse(valid=not errors, errors=errors, sanity_ok=ok, sanity_message=message)


@router.post("/generate", response_model=Scenario)
def create_generated(body: GenerateRequest) -> Scenario:
    """Generate a scenario from a seed and a difficulty vector."""
    difficulty = body.difficulty
    if difficulty is None and body.trainee_id:
        from app.db.repositories import load_skill_model_by_id

        model = load_skill_model_by_id(body.trainee_id)
        if model is not None and model.sessions > 0:
            difficulty, _ = model.recommend_difficulty()
    seed = int(body.seed if body.seed is not None else _seed_from_request(body))
    result = generate(
        seed=seed,
        difficulty=difficulty,
        tags=body.tags,
        terrain_preset=body.terrain_preset,
        duration_s=body.duration_s,
    )
    if not result.sanity_ok:
        raise unprocessable(
            "generated scenario failed the sanity simulation",
            sanity_message=result.sanity_message,
        )
    if body.persist:
        upsert_scenario(result.scenario, source="generated")
    return result.scenario


@router.delete("/{scenario_id}", response_model=OkResponse)
def delete_scenario(scenario_id: str) -> OkResponse:
    """Remove a stored scenario (shipped scenarios are protected)."""
    with session_scope() as db:
        row = db.get(ScenarioRow, scenario_id)
        if row is None:
            raise not_found("scenario", scenario_id)
        if row.source == "shipped":
            raise unprocessable("shipped scenarios cannot be deleted")
        db.delete(row)
    return OkResponse(ok=True, message=f"deleted {scenario_id}")


def _seed_from_request(body: GenerateRequest) -> int:
    """Deterministic seed derived from the request when none was supplied."""
    import hashlib

    payload = json.dumps(
        {
            "difficulty": body.difficulty.model_dump(mode="json") if body.difficulty else None,
            "tags": body.tags,
            "terrain": body.terrain_preset.value if body.terrain_preset else None,
            "duration_s": body.duration_s,
        },
        sort_keys=True,
    )
    return int.from_bytes(hashlib.blake2b(payload.encode(), digest_size=4).digest(), "big")
