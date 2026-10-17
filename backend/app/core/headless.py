"""Headless scenario runner used by `make headless` and by the tests.

python -m app.core.headless --scenario scenarios/scn_solo_fpv_dusk.json
python -m app.core.headless --seed 4242 --difficulty 0.6
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.core.engine import SimEngine
from app.core.generator import generate
from app.core.schemas import Scenario
from app.core.view import build_state_frame

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_scenario(path_or_id: str) -> Scenario:
    """Load a scenario from a JSON file, or by id from the `scenarios/` folder."""
    candidate = Path(path_or_id)
    if not candidate.exists():
        candidate = REPO_ROOT / "scenarios" / f"{path_or_id}.json"
    if not candidate.exists():
        raise SystemExit(f"scenario not found: {path_or_id}")
    return Scenario.model_validate(json.loads(candidate.read_text(encoding="utf-8")))


def print_paths(scenario: Scenario, dt: float = 0.2, sample_every_s: float = 5.0) -> str:
    """Run the scenario with no trainee and print sampled entity paths."""
    engine = SimEngine(scenario, dt=dt)
    final = engine.state_hash()
    print(f"scenario   : {scenario.id}  ({scenario.name})")
    print(f"seed       : {scenario.seed}")
    print(f"duration   : {scenario.duration_s} s @ {1 / dt:g} Hz")
    print(
        f"entities   : {scenario.threat_count()} "
        f"({scenario.hostile_count()} hostile, {sum(w.count for w in scenario.decoys)} decoy)"
    )
    print(f"final hash : {final}")
    print("")
    print(
        f"{'t':>7}  {'entity':<16} {'class':<15} {'label':<8} "
        f"{'x':>8} {'y':>8} {'z':>7} {'rng':>7}  status"
    )
    print("-" * 100)

    next_sample = 0.0
    while not engine.finished:
        engine.tick()
        t = engine.state.world.t
        if t + 1e-9 >= next_sample:
            next_sample += sample_every_s
            frame = build_state_frame(engine.state, "headless")
            for ent in engine.state.world.entities.values():
                if not ent.active and ent.neutralised_t is None:
                    continue
                d = ent.distance_to_site()
                status = (
                    "NEUTRALISED"
                    if ent.neutralised
                    else ("BREACHED" if ent.reached_perimeter_t is not None else "tracking")
                )
                print(
                    f"{t:7.1f}  {ent.id:<16} {ent.entity_class.value:<15} "
                    f"{ent.label.value:<8} {ent.position.x:8.1f} {ent.position.y:8.1f} "
                    f"{ent.position.z:7.1f} {d:7.0f}  {status}"
                )
            if frame.tracks:
                print(
                    f"{'':7}  tracks: {len(frame.tracks)} "
                    f"(confirmed {sum(1 for x in frame.tracks if x.confirmed)})"
                )
            print("-" * 100)

    print("")
    print(f"state hash after full run : {engine.state_hash()}")
    print(f"events                   : {len(engine.state.world.events)}")
    breached = [
        e
        for e in engine.state.world.entities.values()
        if e.label.value == "hostile" and e.reached_perimeter_t is not None
    ]
    print(f"hostiles that breached    : {len(breached)}")
    return engine.state_hash()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description="Run a SKYGYM scenario headless.")
    ap.add_argument("--scenario", help="Scenario id (scenarios/<id>.json) or a JSON path.")
    ap.add_argument("--seed", type=int, default=None, help="Generate a scenario from this seed.")
    ap.add_argument(
        "--difficulty", type=float, default=None, help="0..1 difficulty for generation."
    )
    ap.add_argument("--dt", type=float, default=0.2)
    ap.add_argument("--sample", type=float, default=5.0, help="Sampling period in seconds.")
    args = ap.parse_args(argv)

    if args.scenario:
        scenario = load_scenario(args.scenario)
    else:
        from app.core.schemas import DifficultyVector

        diff = DifficultyVector()
        if args.difficulty is not None:
            d = float(args.difficulty)
            diff = DifficultyVector(
                swarm_size=d,
                approach_axes=d,
                time_pressure=d,
                sensor_degradation_level=d,
                decoy_ratio=d,
                visibility=1.0 - d,
                night=d,
                ambiguity=d,
                simultaneity=d,
                roe_strictness=d,
            )
        scenario = generate(seed=args.seed or 12345, difficulty=diff).scenario
    print_paths(scenario, dt=args.dt, sample_every_s=args.sample)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
