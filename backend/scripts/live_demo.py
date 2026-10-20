"""Play one session the way the browser does, then let the server score it.

This is the end-to-end demo from the checklist (spec section 13). The
simulation runs *locally* - the same way the browser runs it - and the server
is asked to score the resulting action log. A green run therefore means the
local engine, the HTTP API, the stored action log and the deterministic replay
all work together.

    make dev                 # in one shell
    make live-demo           # in another (python backend/scripts/live_demo.py -h)

The script plays a *scripted trainee*: it acknowledges every new track,
classifies it from the sensor hint, and engages hostile-suggested tracks once
they are inside the perimeter guard band. It is a driver, not a solver - it
sees only what the sensors report - but it exercises the full path, including
the server-side replay that produces the final score.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_BASE = "http://127.0.0.1:8000"
DEFAULT_SCENARIO = "scn_urban_night_swarm"
DEFAULT_DT = 0.1
DEFAULT_ENGAGE_TTP_S = 30.0
SCORE_PARTS = ("detection", "classification", "prioritisation", "engagement", "workload")


@dataclass(frozen=True)
class RunStats:
    """What the local leg of the run did."""

    frames: int
    actions: int
    wall_s: float
    status: str


class ScriptedTrainee:
    """A driver that only ever sees what the trainee would see.

    It reads the *projected* frame - the same `view.build_state_frame` the
    browser renders - so it cannot cheat by reading ground truth.
    """

    def __init__(self, engage_ttp_s: float) -> None:
        self.engage_ttp_s = engage_ttp_s
        self.sent: set[tuple[str, str]] = set()
        self.frames = 0
        self.actions = 0

    def act(self, state: Any, world: Any, t: float) -> list[Any]:
        """Decide the next actions from one authoritative frame."""
        from app.core.view import build_state_frame

        frame = build_state_frame(state, "live-demo")
        self.frames += 1
        ready = [e for e in frame.effectors if e.ready]
        effector = ready[0].id if ready else None
        out: list[Any] = []
        for track in frame.tracks:
            tid = track.id
            if not track.acknowledged and ("ack", tid) not in self.sent:
                self.sent.add(("ack", tid))
                out.append(_action(t, "ACK_TRACK", tid))
            hint = track.suggested_label
            if track.trainee_class is None and hint and ("cls", tid) not in self.sent:
                self.sent.add(("cls", tid))
                out.append(_action(t, "CLASSIFY", tid, **{"class": str(hint)}))
            ttp = track.time_to_perimeter_s
            if (
                track.trainee_class == "hostile"
                and not track.neutralised
                and effector is not None
                and ttp is not None
                and float(ttp) <= self.engage_ttp_s
                and ("eng", tid) not in self.sent
            ):
                self.sent.add(("eng", tid))
                out.append(_action(t, "ENGAGE_H", tid, effector=effector))
        self.actions += len(out)
        return out


def _action(t: float, kind: str, track_id: str | None = None, **params: Any) -> Any:
    """Build one trainee action."""
    from app.core.schemas import TraineeAction

    return TraineeAction(
        t=round(float(t), 3),
        type=kind,
        track_id=track_id,
        params=params,
        source="scripted",
    )


def pick_scenario(client: httpx.Client, wanted: str) -> dict[str, Any]:
    """The shipped scenario named `wanted`, or the first shipped one."""
    response = client.get("/api/scenarios", params={"source": "shipped"})
    response.raise_for_status()
    items = response.json()["items"]
    if not items:
        raise SystemExit("the library has no shipped scenarios; run `make seed`")
    for item in items:
        if item["id"] == wanted:
            return dict(item)
    return dict(items[0])


def pick_trainee(client: httpx.Client, wanted: str) -> dict[str, Any]:
    """The named trainee, or the first non-synthetic trainee, or anyone."""
    response = client.get("/api/users")
    response.raise_for_status()
    users = [u for u in response.json() if u.get("role") == "trainee"]
    if not users:
        raise SystemExit("no trainees in the database; run `make seed`")
    for user in users:
        if user["id"] == wanted:
            return dict(user)
    return dict(next((u for u in users if not u.get("synthetic")), users[0]))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE, help="running server")
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO, help="shipped scenario id")
    parser.add_argument("--trainee", default="", help="trainee id (default: first non-synthetic)")
    parser.add_argument(
        "--dt",
        type=float,
        default=DEFAULT_DT,
        help="seconds of simulated time per tick (the server replays at 0.1)",
    )
    parser.add_argument(
        "--engage-ttp",
        type=float,
        default=DEFAULT_ENGAGE_TTP_S,
        help="engage hostile-suggested tracks inside this time-to-perimeter",
    )
    parser.add_argument("--quiet", action="store_true", help="only print the final report")
    args = parser.parse_args(argv)

    verbose = not args.quiet
    base = args.base_url.rstrip("/")
    timeout = httpx.Timeout(30.0, read=900.0)  # the closing replay is CPU-heavy

    with httpx.Client(base_url=base, timeout=timeout) as client:
        scenario = pick_scenario(client, args.scenario)
        trainee = pick_trainee(client, args.trainee)
        created = client.post(
            "/api/sessions",
            json={"scenario_id": scenario["id"], "trainee_id": trainee["id"]},
        )
        if created.status_code >= 400:
            print(f"create failed ({created.status_code}): {created.text}", file=sys.stderr)
            return 2
        session_id = str(created.json()["id"])

        print(f"scenario    : {scenario['id']} - {scenario.get('name', '')}")
        print(f"trainee     : {trainee['id']} - {trainee.get('display_name', '')}")
        print(f"session     : {session_id}")

        # The local leg: the same engine the browser runs.
        from app.core.schemas import Scenario
        from app.core.session import play

        document = client.get(f"/api/scenarios/{scenario['id']}").json()
        engine_scenario = Scenario.model_validate(document)
        policy = ScriptedTrainee(args.engage_ttp)
        started = time.monotonic()
        result = play(engine_scenario, policy, dt=args.dt)
        stats = RunStats(
            frames=policy.frames,
            actions=policy.actions,
            wall_s=time.monotonic() - started,
            status="finished",
        )
        print(
            f"local leg   : {stats.status}, {stats.frames} frames, "
            f"{stats.actions} actions, {stats.wall_s:.1f}s wall clock"
        )

        if verbose:
            print("scoring     : server-side deterministic replay ...")
        scoring_started = time.monotonic()
        finished = client.post(
            f"/api/sessions/{session_id}/result",
            json={
                "actions": [a.model_dump(mode="json") for a in result.actions],
                "final_state_hash": result.final_state_hash,
            },
        )
        if finished.status_code >= 400:
            print(f"result failed ({finished.status_code}): {finished.text}", file=sys.stderr)
            return 3
        record = finished.json()

    score = record.get("score") or {}
    if not score:
        print("the server returned no score for the session", file=sys.stderr)
        return 4
    parts = ", ".join(f"{name} {float(score[name]):.1f}" for name in SCORE_PARTS if name in score)
    print(f"replay      : {time.monotonic() - scoring_started:.1f}s")
    print(f"score       : {float(score.get('total', 0.0)):.1f} / 100   ({parts})")
    counts = score.get("verdict_counts") or {}
    if counts:
        print("verdicts    : " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"state hash  : {record.get('final_state_hash', '')}")
    if record.get("final_state_hash") != result.final_state_hash:
        print("the server reproduced a different hash", file=sys.stderr)
        return 6
    print(f"stored      : {session_id} (open the AAR page to review it)")
    return 0 if float(score.get("total", 0.0)) > 0.0 else 5


if __name__ == "__main__":
    sys.exit(main())
