"""WebSocket endpoint: `/ws/session/{id}`.

Protocol (server authoritative):

* client -> server: ``{"type": "action", "action": TraineeAction}``
* client -> server: ``{"type": "resume", "last_step": n}``
* client -> server: ``{"type": "ping"}``
* client -> server: ``{"type": "finish"}``
* server -> client: ``{"type": "state_frame", "frame": StateFrame}``
* server -> client: ``{"type": "resumed", "frame": StateFrame, "actions": [...]}``
* server -> client: ``{"type": "end", "frame": StateFrame}``
* server -> client: ``{"type": "error", "detail": "..."}``

Concurrency rule
----------------
**Exactly one task ever writes to the socket.** The frame pump owns the
connection; the reader task only ever puts control messages on the outbox.
Two tasks calling ``websocket.send`` concurrently is the classic way to end up
with uvicorn's "Unexpected ASGI message, after sending 'websocket.close'".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.core.schemas import TraineeAction
from app.live import live_registry

logger = logging.getLogger("skygym.ws")

router = APIRouter(tags=["realtime"])

#: Queue depth for control messages (frames are dropped rather than queued).
CONTROL_QUEUE_DEPTH = 32


@router.websocket("/ws/session/{session_id}")
async def session_socket(websocket: WebSocket, session_id: str) -> None:
    """Authoritative 10 Hz session stream."""
    await websocket.accept()
    settings = get_settings()
    live = live_registry.get(session_id)
    if live is None or live.state is None:
        await websocket.send_json(
            {"type": "error", "detail": f"session {session_id} is not running"}
        )
        await websocket.close(code=4404)
        return

    frame_queue = live.subscribe()
    control: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=CONTROL_QUEUE_DEPTH)

    async def control_send(payload: dict[str, Any]) -> None:
        """Queue a control message for the pump (never writes to the socket)."""
        with contextlib.suppress(asyncio.QueueFull):
            control.put_nowait(payload)

    reader = asyncio.create_task(_read_actions(websocket, live, control_send))
    pump = asyncio.create_task(
        _pump(websocket, live, frame_queue, control, settings.ws_send_interval_ticks)
    )
    try:
        await pump
    except (WebSocketDisconnect, RuntimeError):
        # Normal teardown: the client went away mid-send.
        logger.debug("session socket for %s closed by the client", session_id)
    except asyncio.CancelledError:  # pragma: no cover - shutdown path
        raise
    except Exception:
        logger.exception("session socket for %s failed", session_id)
    finally:
        for task in (reader, pump):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        live.unsubscribe(frame_queue)
        with contextlib.suppress(Exception):
            await websocket.close()


async def _pump(
    websocket: WebSocket,
    live: Any,
    frame_queue: asyncio.Queue[Any],
    control: asyncio.Queue[dict[str, Any]],
    send_every: int,
) -> None:
    """The only writer to the socket: interleaves control messages and frames.

    Both queues are awaited together with ``FIRST_COMPLETED`` so frames keep
    flowing whether or not the client is talking, and control messages are never
    stuck behind a frame backlog.
    """
    tick = 0
    every = max(1, int(send_every))
    await websocket.send_json(
        {"type": "state_frame", "frame": live.current_frame().model_dump(mode="json")}
    )
    while True:
        control_task = asyncio.ensure_future(control.get())
        frame_task = asyncio.ensure_future(frame_queue.get())
        done, pending = await asyncio.wait(
            {control_task, frame_task},
            timeout=15.0,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

        if not done:
            await websocket.send_json({"type": "ping"})
            continue

        if frame_task in done:
            frame = frame_task.result()
            tick += 1
            logger.debug(
                "ws %s frame t=%.1f status=%s pending=%d",
                live.session_id,
                frame.t,
                frame.status,
                len(pending),
            )
            if tick % every == 0:
                payload = frame.model_dump(mode="json")
                await websocket.send_json(
                    {"type": "end", "frame": payload}
                    if frame.status == "finished"
                    else {"type": "state_frame", "frame": payload}
                )
                if frame.status == "finished":
                    return
            continue

        message = control_task.result()
        await websocket.send_json(message)
        if message.get("type") == "end":
            return


async def _read_actions(websocket: WebSocket, live: Any, control_send: Any) -> None:
    """Consume client messages until the socket closes."""
    while True:
        raw = await websocket.receive_text()
        try:
            message = json.loads(raw)
        except ValueError as exc:
            await control_send({"type": "error", "detail": f"malformed message: {exc}"})
            continue
        kind = str(message.get("type", ""))
        if kind == "action":
            try:
                action = TraineeAction.model_validate(message.get("action") or {})
            except Exception as exc:
                await control_send({"type": "error", "detail": f"invalid action: {exc}"})
                continue
            live.queue(action)
        elif kind == "resume":
            await control_send(
                {
                    "type": "resumed",
                    "frame": live.current_frame().model_dump(mode="json"),
                    "actions": [a.model_dump(mode="json") for a in live.actions],
                }
            )
        elif kind == "ping":
            await control_send({"type": "pong"})
        elif kind == "finish":
            live.finish()
            await control_send(
                {"type": "end", "frame": live.current_frame().model_dump(mode="json")}
            )
            return
