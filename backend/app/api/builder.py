"""Natural-language and structured scenario building."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import unprocessable
from app.core.generator import sanity_check
from app.core.nl_builder import build_from_text, ollama_available
from app.core.schemas import NlBuildResult, Scenario
from app.db.session import session_scope

router = APIRouter(prefix="/api/builder", tags=["builder"])


class NlRequest(BaseModel):
    """Body for `POST /api/builder/natural-language`."""

    text: str = Field(min_length=3, max_length=4000)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    use_llm: bool = True
    persist: bool = False
    template_scenario_id: str | None = None


class TemplateRequest(BaseModel):
    """Save a hand-edited scenario as a reusable template."""

    scenario: Scenario
    persist: bool = True


@router.get("/status")
def status() -> dict[str, Any]:
    """Whether the optional local LLM is reachable."""
    return {
        "llm_available": ollama_available(),
        "fallback": "deterministic rule-based parser",
        "schema_version": Scenario.model_fields["schema_version"].default,
    }


@router.post("/natural-language", response_model=NlBuildResult)
def natural_language(body: NlRequest) -> NlBuildResult:
    """Build a scenario from free text.

    The response always says which path produced it: `llm`, `rule_based`, or
    `llm_fallback_rule_based`. Whatever the path, the scenario has passed
    Pydantic validation and a headless sanity simulation.
    """
    template = None
    if body.template_scenario_id:
        from app.api.scenarios import load_scenario_row
        from app.db.models import ScenarioRow

        with session_scope() as db:
            row = db.get(ScenarioRow, body.template_scenario_id)
            if row is not None:
                template = load_scenario_row(row)
    result = build_from_text(
        body.text,
        seed=body.seed,
        use_llm=body.use_llm,
        base=template,
    )
    if body.persist and result.scenario is not None:
        from app.api.scenarios import upsert_scenario

        upsert_scenario(result.scenario, source="nlu")
    return result


@router.post("/validate", response_model=dict[str, Any])
def validate(body: TemplateRequest) -> dict[str, Any]:
    """Validate (and sanity-simulate) a hand-edited scenario document."""
    ok, message = sanity_check(body.scenario)
    return {
        "valid": ok,
        "sanity_ok": ok,
        "sanity_message": message,
        "errors": [] if ok else [message],
        "entity_count": body.scenario.threat_count(),
        "hostile_count": body.scenario.hostile_count(),
    }


@router.post("/template", response_model=Scenario, status_code=201)
def save_template(body: TemplateRequest) -> Scenario:
    """Persist a scenario edited in the site editor."""
    if not body.persist:
        raise unprocessable("persist must be true to save a template")
    ok, message = sanity_check(body.scenario)
    if not ok:
        raise unprocessable("scenario failed the sanity simulation", sanity_message=message)
    from app.api.scenarios import upsert_scenario

    scenario = body.scenario.model_copy(update={"source": "template"})
    upsert_scenario(scenario, source="template")
    return scenario
