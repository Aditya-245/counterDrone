"""Natural-language scenario builder.

Three-stage pipeline (spec 7.6):

1. Ask the local LLM (Ollama) for a `Scenario` JSON, constrained by the
   Pydantic JSON Schema and a strict system prompt.
2. Validate with Pydantic; on failure, retry at most twice with the validation
   error appended to the prompt.
3. If the LLM is unavailable or still failing, fall back to a deterministic
   rule-based parser (keyword + number extraction -> difficulty -> generator).

Model output is **never** executed: it is parsed with ``json.loads`` only. There
is no ``eval``, ``exec``, ``pickle`` or ``yaml.load`` anywhere on this path.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Final

from app.core.generator import generate
from app.core.schemas import (
    NlBuildResult,
    Scenario,
    TerrainPreset,
    TimeOfDay,
)

__all__ = [
    "SYSTEM_PROMPT",
    "build_from_text",
    "extract_difficulty",
    "ollama_available",
    "parse_json_object",
    "resolve_ollama_model",
    "rule_based_scenario",
    "scenario_json_schema",
]

MAX_LLM_ATTEMPTS: Final[int] = 3  # 1 initial + at most 2 retries

#: Format failures (the model answered in prose) get one retry. Re-prompting a
#: model that cannot emit JSON a second time costs another full inference round
#: and, empirically, does not change the answer.
_MAX_PROSE_RETRIES: Final[int] = 1

_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"\b(\d+(?:\.\d+)?)\b")


# --------------------------------------------------------------------------- #
# Ollama
# --------------------------------------------------------------------------- #


def _ollama_url(base: str, model: str, prompt: str, system: str, timeout: float) -> str:
    """Build the Ollama `/api/generate` request body."""
    _ = model
    return json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2, "num_predict": 2048},
        }
    )


#: Models never worth trying for structured JSON on modest hardware: vision-only,
#: or below ~1B parameters where schema adherence is unreliable.
_UNUSABLE_MODEL_MARKERS: Final[tuple[str, ...]] = ("moondream", ":0.5b", ":0.5t")

#: Fallback order when the configured model is absent. Small first: on CPU-only
#: hardware a small instruct model still emits schema-correct JSON, just faster.
_FALLBACK_MODEL_HINTS: Final[tuple[str, ...]] = (
    "qwen",
    "llama",
    "gemma",
    "mistral",
    "phi",
    "granular",
)


def _ollama_settings() -> tuple[str, str, float, bool]:
    from app.config import get_settings

    s = get_settings()
    return s.ollama_url, s.ollama_model, s.ollama_timeout_s, s.ollama_enabled


def _installed_models(base: str, timeout: float) -> list[str]:
    """Model names the local server reports as installed. Empty when unreachable."""
    try:
        req = urllib.request.Request(f"{base.rstrip('/')}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=min(5.0, timeout)) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    names = payload.get("models", []) if isinstance(payload, dict) else []
    return [str(m.get("name")) for m in names if isinstance(m, dict) and m.get("name")]


def _looks_like_instruct(name: str) -> bool:
    """True for a text model capable of following a system prompt."""
    low = name.lower()
    return not any(marker in low for marker in _UNUSABLE_MODEL_MARKERS)


def _score_model(name: str, preferred: str) -> tuple[int, int, int]:
    """Sort key: exact match first, then smallest plausible instruct model.

    Smallest wins deliberately - this is a CPU-only build with a 30 s default
    timeout, so a 7B model is more likely to time out than a 1.5B one.
    """
    low = name.lower()
    exact = 0 if low == preferred.lower() else 1
    hinted = 0 if any(h in low for h in _FALLBACK_MODEL_HINTS) else 1
    size = sum(c.isdigit() for c in low) * 10 + len(low)
    return (exact, hinted, size)


def resolve_ollama_model(url: str | None = None) -> str | None:
    """Pick a usable installed model, or None when the server is unavailable.

    Follows the spec's "fall back to any installed instruct model": the
    configured model is tried first, then the smallest installed instruct model
    that is not vision-only or sub-1B.
    """
    base, model, timeout, enabled = _ollama_settings()
    base = url or base
    if not enabled:
        return None
    installed = _installed_models(base, timeout)
    if not installed:
        return None
    usable = [m for m in installed if _looks_like_instruct(m)]
    if not usable:
        return None
    return min(usable, key=lambda m: _score_model(m, model))


def ollama_available(url: str | None = None) -> bool:
    """True only when a *usable model* is installed, not merely when the daemon is up."""
    return resolve_ollama_model(url) is not None


def _call_ollama(prompt: str, system: str) -> str | None:
    """One Ollama round trip; returns the raw text or None on any failure."""
    base, configured, timeout, enabled = _ollama_settings()
    if not enabled:
        return None
    model = resolve_ollama_model(base) or configured
    body = _ollama_url(base, model, prompt, system, timeout).encode("utf-8")
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    response = payload.get("response")
    return response if isinstance(response, str) else None


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Parse a JSON object out of model output, defensively.

    Handles a fenced ```json block and leading/trailing prose, but never
    evaluates anything.
    """
    if not text:
        return None
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


# --------------------------------------------------------------------------- #
# Prompting
# --------------------------------------------------------------------------- #


def scenario_json_schema() -> dict[str, Any]:
    """The JSON Schema of `Scenario`, embedded in the system prompt."""
    return Scenario.model_json_schema()


def _system_prompt() -> str:
    schema = json.dumps(scenario_json_schema())
    return (
        "You are the scenario generator for SKYGYM, a counter-drone decision-training "
        "simulator. Produce ONE scenario as a single JSON object that validates against "
        "the JSON Schema below.\n"
        "Hard requirements:\n"
        "- Output JSON only. No prose, no markdown fences, no commentary.\n"
        "- Use only generic, abstract, synthetic parameters. Never invent real radar "
        "frequencies, real RCS values, or real weapon performance.\n"
        "- `seed` must be a non-negative integer; keep the trainee's rules of engagement "
        "consistent with `roe.rule_set`.\n"
        "- `id` and `name` must be short and slug-like.\n"
        f"JSON Schema:\n{schema}"
    )


def _user_prompt(text: str, seed: int, error: str | None) -> str:
    parts = [
        "Create a SKYGYM scenario from this description.",
        "",
        f"TRAINEE BRIEFING: {text}",
        "",
        f"Use seed {seed} for any randomness you choose.",
    ]
    if error:
        parts += [
            "",
            "Your previous attempt failed validation with this error:",
            error[:800],
            "Fix it and return the corrected JSON object only.",
        ]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Rule-based fallback
# --------------------------------------------------------------------------- #

_TERRAIN_WORDS: Final[dict[str, TerrainPreset]] = {
    "urban": TerrainPreset.URBAN,
    "city": TerrainPreset.URBAN,
    "town": TerrainPreset.URBAN,
    "village": TerrainPreset.URBAN,
    "rural": TerrainPreset.RURAL,
    "field": TerrainPreset.RURAL,
    "farm": TerrainPreset.RURAL,
    "countryside": TerrainPreset.RURAL,
    "hill": TerrainPreset.HILLS,
    "hills": TerrainPreset.HILLS,
    "mountain": TerrainPreset.HILLS,
    "ridge": TerrainPreset.HILLS,
    "valley": TerrainPreset.HILLS,
}

_TIME_WORDS: Final[dict[str, TimeOfDay]] = {
    "night": TimeOfDay.NIGHT,
    "dusk": TimeOfDay.DUSK,
    "twilight": TimeOfDay.DUSK,
    "evening": TimeOfDay.DUSK,
    "day": TimeOfDay.DAY,
    "daylight": TimeOfDay.DAY,
    "noon": TimeOfDay.DAY,
}

_SWARM_WORDS: Final[tuple[str, ...]] = ("swarm", "wave", "waves", "group", "multiple", "many")
_DECOY_WORDS: Final[tuple[str, ...]] = (
    "decoy",
    "decoys",
    "bird",
    "birds",
    "kite",
    "kites",
    "flock",
)
_FAILURE_WORDS: Final[tuple[str, ...]] = (
    "fail",
    "fails",
    "failure",
    "jammed",
    "jam",
    "degraded",
    "knocked out",
    "loss",
)
_SILENT_WORDS: Final[tuple[str, ...]] = ("rf-silent", "rf silent", "autonomous", "silent", "no rf")
_STRICT_WORDS: Final[tuple[str, ...]] = ("strict", "pid", "positive id", "roewatcher")


@dataclass(slots=True)
class ExtractedBrief:
    """What the deterministic parser could pull out of a free-text brief."""

    seed: int
    count: int
    decoys: int
    duration_s: int
    night: bool
    time_of_day: TimeOfDay
    terrain: TerrainPreset | None
    rain: float
    fog: float
    degradation_seconds: float | None
    rf_silent: bool
    strict: bool
    tags: list[str]
    swarm: bool


def extract_difficulty(text: str) -> tuple[Any, list[str]]:
    """Parse a brief into a difficulty vector plus tags (rule-based path)."""
    low = text.lower()
    numbers = [float(n) for n in _NUMBER_RE.findall(text)]
    brief = _parse_brief(text, low, numbers)

    swarm = any(w in low for w in _SWARM_WORDS) or brief.count > 3
    decoy_ratio = min(0.9, brief.decoys / max(3.0, brief.count))
    simultaneity = 0.7 if swarm and brief.count >= 6 else (0.45 if swarm else 0.2)
    degradation = 0.75 if brief.degradation_seconds is not None else 0.15
    visibility = max(0.05, min(0.95, 0.9 - brief.fog * 0.6 - brief.rain * 0.3))
    night = 1.0 if brief.night else 0.0
    ambiguity = 0.7 if brief.decoys >= 3 else (0.45 if brief.decoys else 0.2)
    roe = 0.85 if brief.strict else 0.4
    pressure = 0.8 if brief.duration_s <= 300 else (0.5 if brief.duration_s <= 480 else 0.25)

    from app.core.schemas import DifficultyVector

    vec = DifficultyVector(
        swarm_size=max(0.1, min(0.95, brief.count / 12.0)),
        approach_axes=0.55 if swarm else 0.25,
        time_pressure=pressure,
        sensor_degradation_level=degradation,
        decoy_ratio=decoy_ratio,
        visibility=visibility,
        night=night,
        ambiguity=ambiguity,
        simultaneity=simultaneity,
        roe_strictness=roe,
    )
    return vec, brief.tags


def _parse_brief(text: str, low: str, numbers: list[float]) -> ExtractedBrief:
    """Keyword and number extraction over a free-text brief."""
    count = 1
    for n in numbers:
        if 1.0 <= n <= 48.0:
            count = int(n)
            break

    decoys = 0
    for n in numbers:
        if re.search(rf"\b{int(n)}\s+(?:decoy|bird|birds|kite|kites|flock)", low):
            decoys = int(n)
            break
    if decoys == 0 and any(w in low for w in _DECOY_WORDS):
        decoys = 2

    duration = 600
    m = re.search(r"(\d+)\s*(?:minute|minutes|min)", low)
    if m:
        duration = int(m.group(1)) * 60
    else:
        m = re.search(r"(\d+)\s*(?:second|seconds|sec)\b", low)
        if m:
            duration = int(m.group(1))

    failure_seconds: float | None = None
    m = re.search(
        r"(?:after|past|within|in)\s+(\d+(?:\.\d+)?)\s*(minute|minutes|min|second|seconds|sec)",
        low,
    )
    if m and any(w in low for w in _FAILURE_WORDS):
        value = float(m.group(1))
        failure_seconds = value * 60.0 if m.group(2).startswith("min") else value

    rain = 1.0 if any(w in low for w in ("rain", "rainy", "storm", "downpour")) else 0.0
    fog = 0.8 if any(w in low for w in ("fog", "foggy", "mist", "haze")) else 0.0

    tod = TimeOfDay.DAY
    for word, tod_preset in _TIME_WORDS.items():
        if re.search(rf"\b{word}\b", low):
            tod = tod_preset
            break

    terrain: TerrainPreset | None = None
    for word, terrain_preset in _TERRAIN_WORDS.items():
        if re.search(rf"\b{word}\b", low):
            terrain = terrain_preset
            break

    tags: list[str] = []
    if terrain is not None:
        tags.append(terrain.value)
    if tod is TimeOfDay.NIGHT:
        tags.append("night")
    elif tod is TimeOfDay.DUSK:
        tags.append("dusk")
    if rain:
        tags.append("rain")
    if fog:
        tags.append("fog")
    if failure_seconds is not None:
        tags.append("sensor-failure")
    if any(w in low for w in _SILENT_WORDS):
        tags.append("rf-silent")
    if any(w in low for w in _DECOY_WORDS):
        tags.append("decoys")
    if count > 3:
        tags.append("swarm")
    if brief_strict := any(w in low for w in _STRICT_WORDS):
        tags.append("strict-roe")

    seed = int(abs(hash(text)) % 1_000_000) if not numbers else int(numbers[-1]) * 7919
    return ExtractedBrief(
        seed=seed % (2**31 - 1),
        count=max(1, count),
        decoys=decoys,
        duration_s=max(180, min(3600, duration)),
        night=tod is TimeOfDay.NIGHT,
        time_of_day=tod,
        terrain=terrain,
        rain=rain,
        fog=fog,
        degradation_seconds=failure_seconds,
        rf_silent=any(w in low for w in _SILENT_WORDS),
        strict=brief_strict,
        tags=list(dict.fromkeys(tags)),
        swarm=count > 3 or any(w in low for w in _SWARM_WORDS),
    )


def rule_based_scenario(text: str, seed: int | None = None) -> tuple[Scenario, list[str]]:
    """Deterministic parser: free text -> difficulty vector -> generator."""
    vec, tags = extract_difficulty(text)
    brief = _parse_brief(text, text.lower(), [float(n) for n in _NUMBER_RE.findall(text)])
    use_seed = int(seed if seed is not None else brief.seed)
    result = generate(
        seed=use_seed,
        difficulty=vec,
        tags=tags,
        terrain_preset=brief.terrain,
        duration_s=brief.duration_s,
        name=f"NL scenario ({', '.join(tags) or 'standard'})",
        source="nlu",
    )
    scenario = result.scenario.model_copy(deep=True)
    scenario.environment.time_of_day = brief.time_of_day
    if brief.degradation_seconds is not None:
        from app.core.schemas import ScenarioEvent

        for sensor in scenario.sensors:
            if sensor.type.value in ("radar", "rf"):
                scenario.events.append(
                    ScenarioEvent(
                        t=round(
                            min(brief.degradation_seconds, scenario.duration_s - 5.0),
                            2,
                        ),
                        kind="sensor_partial",
                        target=sensor.id,
                        description=f"{sensor.id} degraded mid-approach",
                    )
                )
                break
    scenario.events.sort(key=lambda e: e.t)
    scenario = scenario.model_validate(scenario.model_dump(mode="python"))
    return scenario, tags


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


def _seeded_payload(payload: dict[str, Any], seed: int, text: str) -> dict[str, Any]:
    """Inject the caller's seed (and a slug id/name if absent) into LLM output.

    The trainee's ``seed`` comes from the caller, never from the model: a
    randomly invented number would break reproduction of the exact same
    scenario, which is the whole point of a seeded generator. ``id`` and
    ``name`` get the same treatment for the same reason - the model's chosen
    slug is arbitrary and repeated calls should stay stable.
    """
    out = dict(payload)
    if not isinstance(out.get("seed"), int) or isinstance(out.get("seed"), bool):
        out["seed"] = seed
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "nl-brief"
    for field, fallback in (("id", slug), ("name", text.strip()[:60] or "NL brief")):
        value = out.get(field)
        if not isinstance(value, str) or not value.strip():
            out[field] = fallback
    return out


def build_from_text(
    text: str,
    seed: int | None = None,
    use_llm: bool = True,
    llm_caller: Any = None,
    base: Scenario | None = None,
) -> NlBuildResult:
    """Full NL builder pipeline. Never raises; always returns a result."""
    use_seed = int(seed if seed is not None else 4242)
    errors: list[str] = []

    if use_llm:
        caller = llm_caller or _call_ollama
        available = llm_caller is not None or ollama_available()
        if available:
            prompt = _user_prompt(text, use_seed, None)
            for attempt in range(MAX_LLM_ATTEMPTS):
                try:
                    raw = caller(prompt, _system_prompt())
                except Exception as exc:  # pragma: no cover - defensive
                    errors.append(f"llm call failed: {exc}")
                    break
                payload = parse_json_object(raw or "")
                if payload is None:
                    errors.append(f"attempt {attempt + 1}: output was not a JSON object")
                    if attempt >= _MAX_PROSE_RETRIES:
                        # A second format failure means the model cannot follow the
                        # prompt; retrying again only burns another inference round.
                        break
                    prompt = _user_prompt(text, use_seed, errors[-1])
                    continue
                try:
                    scenario = Scenario.model_validate(_seeded_payload(payload, use_seed, text))
                except Exception as exc:
                    errors.append(f"attempt {attempt + 1}: {type(exc).__name__}: {exc}"[:600])
                    prompt = _user_prompt(text, use_seed, errors[-1])
                    continue
                ok, message = _sanity(scenario)
                if not ok:
                    errors.append(f"sanity simulation rejected it: {message}")
                    break
                return NlBuildResult(
                    scenario=scenario,
                    path="llm",
                    explanation=(
                        f"Built by the local LLM after {attempt + 1} attempt(s); "
                        "the result passed Pydantic validation and a headless sanity run."
                    ),
                    attempts=attempt + 1,
                    validation_errors=errors,
                    llm_available=True,
                    sanity_ok=True,
                    sanity_message=message,
                )
        else:
            errors.append("local LLM not reachable")

    scenario, tags = rule_based_scenario(text, use_seed)
    if base is not None:
        scenario = _merge_from_template(scenario, base)
    ok, message = _sanity(scenario)
    path = "llm_fallback_rule_based" if use_llm else "rule_based"
    explanation = (
        "Built by the deterministic rule-based parser (no LLM output was usable). "
        f"Extracted tags: {', '.join(tags) or 'none'}."
    )
    return NlBuildResult(
        scenario=scenario,
        path=path,  # type: ignore[arg-type]
        explanation=explanation,
        attempts=0,
        validation_errors=errors,
        llm_available=use_llm,
        sanity_ok=ok,
        sanity_message=message,
    )


def _sanity(scenario: Scenario) -> tuple[bool, str]:
    from app.core.generator import sanity_check

    return sanity_check(scenario)


def _merge_from_template(scenario: Scenario, base: Scenario) -> Scenario:
    """Keep the terrain/site/effector layout of a template the LLM did not set."""
    data = scenario.model_dump(mode="python")
    template = base.model_dump(mode="python")
    for key in ("terrain", "site", "effectors", "sensors"):
        if key not in data or not data.get("threats"):
            data[key] = template[key]
    data["terrain"] = data.get("terrain") or template["terrain"]
    return Scenario.model_validate(data)


SYSTEM_PROMPT = _system_prompt()
