"""Authoritative live-session runtime and the in-process session registry.

The server owns the simulation. Clients send `TraineeAction` messages and
receive `state_frame` snapshots; nothing on the client can change the outcome.

Reconnect/resume (DECISIONS D015): the runtime keeps its step index and full
action log, so a client that drops can ask for `resume` and immediately get the
current frame plus the actions it missed.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import numpy as np

from app.core.engine import EngineState, initial_state, step
from app.core.schemas import Scenario, SimEvent, StateFrame, TraineeAction
from app.core.view import build_state_frame

__all__ = ["LiveSession", "LiveSessionRegistry", "get_live_session", "live_registry"]


class LiveSession:
    """One authoritative running session.

    `tick()` advances the deterministic engine by exactly one `dt`. It is driven
    by the realtime loop in `app.api.realtime`, never by the client.
    """

    def __init__(self) -> None:
        self.session_id: str = ""
        self.trainee_id: str = ""
        self.scenario: Scenario | None = None
        self.state: EngineState | None = None
        self.dt: float = 0.1
        self.actions: list[TraineeAction] = []
        self.pending: list[TraineeAction] = []
        self.rng: np.random.Generator | None = None
        self.started_monotonic: float = 0.0
        self.accumulator: float = 0.0
        self.running: bool = False
        self.finished: bool = False
        self.step_events: list[SimEvent] = []
        self._listeners: set[asyncio.Queue[StateFrame]] = set()

    # ------------------------------------------------------------------ #
    def start(
        self,
        scenario: Scenario,
        dt: float = 0.1,
        allow_pause: bool = False,
        trainee_id: str = "",
        session_id: str = "",
    ) -> None:
        """Initialise the runtime for `scenario`."""
        import time

        self.scenario = scenario
        self.trainee_id = trainee_id
        self.session_id = session_id or self.session_id
        self.dt = float(dt)
        self.state = initial_state(scenario, dt=self.dt, allow_pause=allow_pause)
        self.rng = np.random.default_rng(int(scenario.seed))
        self.actions = []
        self.pending = []
        self.step_events = []
        self.finished = False
        self.running = True
        self.started_monotonic = time.monotonic()
        self.accumulator = 0.0

    # ------------------------------------------------------------------ #
    def queue(self, action: TraineeAction) -> None:
        """Accept one trainee action; applied at the next tick boundary."""
        if self.state is None:
            raise RuntimeError("session not started")
        self.pending.append(action)
        self.actions.append(action)

    def tick(self) -> StateFrame:
        """Advance exactly one timestep and broadcast the resulting frame."""
        if self.state is None or self.state.world.status != "running":
            self.running = False
            return self.current_frame()
        before = len(self.state.world.events)
        self.state = step(self.state, self.pending, self.rng)  # type: ignore[arg-type]
        self.pending = []
        self.step_events = list(self.state.world.events[before:])
        if self.state.world.status == "finished":
            self.running = False
            self.finished = True
        frame = self.current_frame()
        for queue in list(self._listeners):
            # A slow client drops frames rather than stalling the simulation.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(frame)
        return frame

    def current_frame(self) -> StateFrame:
        """Build a frame from the current state without advancing it."""
        if self.state is None:  # pragma: no cover - guarded by callers
            raise RuntimeError("session not started")
        return build_state_frame(self.state, self.session_id, list(self.step_events))

    def subscribe(self) -> asyncio.Queue[StateFrame]:
        """Register a queue that receives every subsequent frame."""
        queue: asyncio.Queue[StateFrame] = asyncio.Queue(maxsize=8)
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[StateFrame]) -> None:
        """Stop sending frames to `queue`."""
        self._listeners.discard(queue)

    def finish(self) -> None:
        """Stop the runtime."""
        self.running = False

    def snapshot(self) -> dict[str, Any]:
        """Small payload used by `/api/sessions/live`."""
        if self.state is None:
            return {}
        return {
            "session_id": self.session_id,
            "scenario_id": self.scenario.id if self.scenario else "",
            "trainee_id": self.trainee_id,
            "t": self.state.world.t,
            "step": self.state.world.step,
            "status": self.state.world.status,
            "state_hash": self.state.state_hash(),
        }


class LiveSessionRegistry:
    """Process-wide registry of live sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, LiveSession] = {}

    def get(self, session_id: str) -> LiveSession | None:
        """Look up one live session."""
        return self._sessions.get(session_id)

    def get_or_create(self, session_id: str) -> LiveSession:
        """Look up or create one live session."""
        live = self._sessions.get(session_id)
        if live is None:
            live = LiveSession()
            live.session_id = session_id
            self._sessions[session_id] = live
        return live

    def drop(self, session_id: str) -> None:
        """Forget a session."""
        live = self._sessions.pop(session_id, None)
        if live is not None:
            live.finish()

    def all(self) -> list[LiveSession]:
        """Every registered live session."""
        return [s for s in self._sessions.values() if s.scenario is not None]

    def clear(self) -> None:
        """Drop every session (used by tests)."""
        for session_id in list(self._sessions):
            self.drop(session_id)


live_registry = LiveSessionRegistry()


def get_live_session(session_id: str) -> LiveSession:
    """Registry accessor used by the routers."""
    return live_registry.get_or_create(session_id)
