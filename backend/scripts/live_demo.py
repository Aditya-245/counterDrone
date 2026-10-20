"""Drive one live session over the public API and print the authoritative score.

This is the end-to-end demo from the checklist (spec section 13). Nothing is
simulated in-process: the script talks to a *running* server, so a green run
means the HTTP API, the WebSocket runtime, the stored action log and the
deterministic replay all work together.

    make dev                 # in one shell
    make live-demo           # in another (python backend/scripts/live_demo.py -h)

The script plays a *scripted trainee*: it acknowledges every new track,
classifies it from the sensor hint, and engages hostile-suggested tracks once
they are inside the perimeter guard band. It is a driver, not a solver - it does
not see ground truth - but it exercises the full path, including the server-side
replay that produces the final score.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx
from websockets.asyncio.client import connect as ws_connect

DEFAULT_BASE = "http://127.0.0.1:8000"
DEFAULT_SCENARIO = "scn_urban_night_swarm"
DEFAULT_DT = 1.0
DEFAULT_MAX_WALL_S = 240.0
DEFAULT_ENGAGE_TTP_S = 30.0
SCORE_PARTS = ("detection", "classification", "prioritisation", "engagement", "workload")


@dataclass(frozen=True)
class RunStats:
    """What the live leg of the run did."""

    frames: int
    actions: int
    wall_s: float
    status: str


def action(t: float, kind: str, track_id: str | None = None, **params: Any) -> dict[str, Any]:
    """Build one client -> server action message."""
    return {
        "type": "action",
        "action": {
            "t": round(float(t), 3),
            "type": kind,
            "track_id": track_id,
            "params": params,
        },
    }


def scripted_trainee(
    frame: dict[str, Any], sent: set[tuple[str, str]], engage_ttp_s: float
) -> list[dict[str, Any]]:
    """Decide the next actions from one authoritative frame.

    `sent` remembers `(kind, track_id)` pairs so a track is only acted on once.
    """
    out: list[dict[str, Any]] = []
    t = float(frame.get("t") or 0.0)
    ready = [e for e in frame.get("effectors", []) if e.get("ready")]
    effector = str(ready[0]["id"]) if ready else None
    for track in frame.get("tracks", []):
        track_id = str(track.get("id"))
        if not track.get("acknowledged") and ("ack", track_id) not in sent:
            sent.add(("ack", track_id))
            out.append(action(t, "ACK_TRACK", track_id))
        hint = track.get("suggested_label")
        if track.get("trainee_class") is None and hint and ("cls", track_id) not in sent:
            sent.add(("cls", track_id))
            out.append(action(t, "CLASSIFY", track_id, **{"class": str(hint)}))
        ttp = track.get("time_to_perimeter_s")
        if (
            track.get("trainee_class") == "hostile"
            and not track.get("neutralised")
            and effector is not None
            and ttp is not None
            and float(ttp) <= engage_ttp_s
            and ("eng", track_id) not in sent
        ):
            sent.add(("eng", track_id))
            out.append(action(t, "ENGAGE_H", track_id, effector=effector))
    return out


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


async def play(ws_url: str, engage_ttp_s: float, max_wall_s: float, verbose: bool) -> RunStats:
    """Connect, react to frames until the run ends, and report what happened."""
    sent: set[tuple[str, str]] = set()
    frames = 0
    actions = 0
    started = time.monotonic()
    last_log = 0.0
    status = "unknown"
    async with ws_connect(ws_url, max_size=4 * 1024 * 1024) as socket:
        while True:
            if time.monotonic() - started > max_wall_s:
                if verbose:
                    print("  wall-clock guard hit; asking the server to finish")
                await socket.send(json.dumps({"type": "finish"}))
            message = json.loads(await asyncio.wait_for(socket.recv(), timeout=30.0))
            kind = message.get("type")
            if kind == "error":
                raise SystemExit(f"server error: {message.get('detail')}")
            if kind not in ("state_frame", "resumed", "end"):
                continue
            frame = message["frame"]
            frames += 1
            status = str(frame.get("status", status))
            for payload in scripted_trainee(frame, sent, engage_ttp_s):
                await socket.send(json.dumps(payload))
                actions += 1
            now = time.monotonic()
            if verbose and now - last_log >= 5.0:
                last_log = now
                print(
                    f"  t={frame.get('t'):6.1f}s  step={frame.get('step'):5d}  "
                    f"tracks={len(frame.get('tracks', [])):2d}  actions={actions:3d}"
                )
            if kind == "end" or status == "finished":
                break
    return RunStats(
        frames=frames, actions=actions, wall_s=time.monotonic() - started, status=status
    )


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
        help="seconds of simulated time per tick (the clock runs at tick_hz)",
    )
    parser.add_argument(
        "--engage-ttp",
        type=float,
        default=DEFAULT_ENGAGE_TTP_S,
        help="engage hostile-suggested tracks inside this time-to-perimeter",
    )
    parser.add_argument(
        "--max-wall", type=float, default=DEFAULT_MAX_WALL_S, help="wall-clock guard, seconds"
    )
    parser.add_argument("--quiet", action="store_true", help="only print the final report")
    args = parser.parse_args(argv)

    verbose = not args.quiet
    base = args.base_url.rstrip("/")
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    timeout = httpx.Timeout(30.0, read=900.0)  # the closing replay is CPU-heavy

    with httpx.Client(base_url=base, timeout=timeout) as client:
        scenario = pick_scenario(client, args.scenario)
        trainee = pick_trainee(client, args.trainee)
        created = client.post(
            "/api/sessions",
            json={"scenario_id": scenario["id"], "trainee_id": trainee["id"], "dt": args.dt},
        )
        if created.status_code >= 400:
            print(f"create failed ({created.status_code}): {created.text}", file=sys.stderr)
            return 2
        session_id = str(created.json()["id"])

        print(f"scenario    : {scenario['id']} - {scenario.get('name', '')}")
        print(f"trainee     : {trainee['id']} - {trainee.get('display_name', '')}")
        print(f"session     : {session_id} (dt={args.dt})")
        stats = asyncio.run(
            play(f"{ws_base}/ws/session/{session_id}", args.engage_ttp, args.max_wall, verbose)
        )
        print(
            f"live leg    : {stats.status}, {stats.frames} frames, "
            f"{stats.actions} actions, {stats.wall_s:.1f}s wall clock"
        )

        if verbose:
            print("scoring     : server-side deterministic replay ...")
        started = time.monotonic()
        finished = client.post(f"/api/sessions/{session_id}/result", json={})
        if finished.status_code >= 400:
            print(f"result failed ({finished.status_code}): {finished.text}", file=sys.stderr)
            return 3
        record = finished.json()

    score = record.get("score") or {}
    if not score:
        print("the server returned no score for the session", file=sys.stderr)
        return 4
    parts = ", ".join(f"{name} {float(score[name]):.1f}" for name in SCORE_PARTS if name in score)
    print(f"replay      : {time.monotonic() - started:.1f}s")
    print(f"score       : {float(score.get('total', 0.0)):.1f} / 100   ({parts})")
    counts = score.get("verdict_counts") or {}
    if counts:
        print("verdicts    : " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"state hash  : {record.get('final_state_hash', '')}")
    print(f"stored      : {session_id} (open the AAR page to review it)")
    return 0 if stats.status == "finished" and float(score.get("total", 0.0)) > 0.0 else 5


if __name__ == "__main__":
    sys.exit(main())
