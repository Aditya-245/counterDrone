"""Export every Pydantic model's JSON Schema into `docs/schema/`.

Run with ``make schemas``. The output is what the NL builder embeds in its
system prompt and what API consumers validate against.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from app.core.schemas import (
    SCHEMA_VERSION,
    DifficultyVector,
    ForkRequest,
    ForkResult,
    LandscapeSpec,
    ReplaySession,
    Scenario,
    ScoreBreakdown,
    SessionRecord,
    StateFrame,
    TraineeAction,
)

__all__ = ["EXPORTS", "export_all"]

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = REPO_ROOT / "docs" / "schema"

EXPORTS: dict[str, type[BaseModel]] = {
    "Scenario": Scenario,
    "DifficultyVector": DifficultyVector,
    "TraineeAction": TraineeAction,
    "StateFrame": StateFrame,
    "ScoreBreakdown": ScoreBreakdown,
    "SessionRecord": SessionRecord,
    "ReplaySession": ReplaySession,
    "ForkRequest": ForkRequest,
    "ForkResult": ForkResult,
    "LandscapeSpec": LandscapeSpec,
}


def export_all(out_dir: Path | None = None) -> list[Path]:
    """Write one JSON Schema file per model plus a combined index."""
    target = out_dir or OUT_DIR
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    index: dict[str, str] = {}
    for name, model in EXPORTS.items():
        payload = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": name,
            "x-skygym-schema-version": SCHEMA_VERSION,
            **model.model_json_schema(),
        }
        path = target / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        written.append(path)
        index[name] = path.name

    combined = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "x-skygym-schema-version": SCHEMA_VERSION,
        "models": index,
        "definitions": {name: model.model_json_schema() for name, model in EXPORTS.items()},
    }
    index_path = target / "index.json"
    index_path.write_text(json.dumps(combined, indent=2, sort_keys=True), encoding="utf-8")
    written.append(index_path)
    return written


def main() -> int:
    """CLI entry point."""
    paths = export_all()
    for path in paths:
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
