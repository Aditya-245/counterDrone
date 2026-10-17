"""Team mode: WebSocket rooms with three operator roles (spec 7.8).

Roles
-----
* ``sensor_operator`` steers sensors and marks tracks
* ``commander`` classifies tracks and sets priorities
* ``effector_operator`` executes engagements

Single-player is unaffected: a room with one member holding every role behaves
exactly like a solo trainee, so the trainer never has to staff three consoles to
get started.

What is measured
----------------
* **Communication latency** - delay between the moment the commander marks a
  track and the moment the effector operator acts on it.
* **Role handoff correctness** - whether the engagement the effector operator
  fired belonged to a track the commander had classified and prioritised.
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core.schemas import ActionType, Role, TraineeAction
from app.db.models import ScenarioRow
from app.db.session import session_scope
from app.live import live_registry

router = APIRouter(prefix="/api/teams", tags=["teams"])


class JoinRoom(BaseModel):
    """Body for `POST /api/teams/rooms`."""

    session_id: str
    trainee_id: str
    role: Role = Role.TRAINEE
    display_name: str = ""


class RoomMember(BaseModel):
    """One connected operator."""

    id: str
    trainee_id: str
    display_name: str
    role: Role
    joined_t: float


class RoomSummary(BaseModel):
    """Team-session scorecard returned by `GET /api/teams/rooms/{id}`."""

    room_id: str
    session_id: str
    members: list[RoomMember]
    comms_latency_ms: list[float] = Field(default_factory=list)
    mean_comms_latency_ms: float = 0.0
    handoffs_total: int = 0
    handoffs_correct: int = 0
    handoff_accuracy: float = 1.0
    role_violations: list[str] = Field(default_factory=list)


def _roles_covered(roles: set[Role]) -> bool:
    """A room is fully staffed only with all three operator roles."""
    return {
        Role.SENSOR_OPERATOR,
        Role.COMMANDER,
        Role.EFFECTOR_OPERATOR,
    } <= roles


@dataclass
class TeamRoom:
    """One team session: shared authoritative sim plus role bookkeeping."""

    room_id: str
    session_id: str
    members: dict[str, RoomMember] = field(default_factory=dict)
    comms_latency_ms: list[float] = field(default_factory=list)
    marked_at: dict[str, float] = field(default_factory=dict)
    prioritised: dict[str, int] = field(default_factory=dict)
    classified: dict[str, str] = field(default_factory=dict)
    handoffs_total: int = 0
    handoffs_correct: int = 0
    violations: list[str] = field(default_factory=list)
    sockets: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> RoomSummary:
        """Build the scorecard."""
        latency = (
            float(sum(self.comms_latency_ms) / len(self.comms_latency_ms))
            if self.comms_latency_ms
            else 0.0
        )
        return RoomSummary(
            room_id=self.room_id,
            session_id=self.session_id,
            members=list(self.members.values()),
            comms_latency_ms=[round(v, 2) for v in self.comms_latency_ms],
            mean_comms_latency_ms=round(latency, 2),
            handoffs_total=self.handoffs_total,
            handoffs_correct=self.handoffs_correct,
            handoff_accuracy=round(
                self.handoffs_correct / self.handoffs_total if self.handoffs_total else 1.0, 4
            ),
            role_violations=list(self.violations),
        )

    def record_action(self, member: RoomMember, action: TraineeAction) -> None:
        """Apply one member's action to the shared scoring state."""
        tid = action.track_id or ""
        if action.type is ActionType.CLASSIFY:
            if member.role is not Role.COMMANDER and _roles_covered(
                {m.role for m in self.members.values()}
            ):
                self.violations.append(
                    f"{member.display_name} classified as {member.role.value} at t={action.t:.1f}"
                )
            self.classified[tid] = str(action.params.get("class", "unknown"))
        elif action.type is ActionType.SET_PRIORITY:
            self.prioritised[tid] = int(action.params.get("priority", 1))
        elif action.type in (ActionType.ACK_TRACK, ActionType.STEER_SENSOR):
            if tid:
                self.marked_at[tid] = time.time()
        elif action.type is ActionType.ACK_TRACK and tid:
            self.marked_at[tid] = time.time()
        elif action.is_engagement():
            self.handoffs_total += 1
            marked = self.marked_at.get(tid)
            if marked is not None:
                self.comms_latency_ms.append((time.time() - marked) * 1000.0)
            correct = bool(tid in self.prioritised) and tid in self.classified
            self.handoffs_correct += int(correct)
            if not correct and _roles_covered({m.role for m in self.members.values()}):
                self.violations.append(
                    f"{member.display_name} engaged {tid} without a commander handoff at "
                    f"t={action.t:.1f}"
                )

    def to_dict(self) -> dict[str, Any]:
        """Serialise the scorecard for the session row."""
        return self.summary().model_dump(mode="json")


_ROOMS: dict[str, TeamRoom] = {}


def get_room(room_id: str) -> TeamRoom | None:
    """Look up one team room."""
    return _ROOMS.get(room_id)


def drop_room(room_id: str) -> None:
    """Forget one team room."""
    room = _ROOMS.pop(room_id, None)
    if room is not None:
        for socket in room.sockets.values():
            with contextlib.suppress(Exception):
                socket.close()


@router.post("/rooms", response_model=RoomSummary, status_code=201)
def create_room(body: JoinRoom) -> RoomSummary:
    """Open a team room bound to a live session."""
    with session_scope() as db:
        scenario_id = None
        from app.db.models import SessionRow

        row = db.get(SessionRow, body.session_id)
        if row is not None:
            scenario_id = row.scenario_id
        elif db.get(ScenarioRow, body.session_id) is not None:
            scenario_id = body.session_id
        if scenario_id is None:
            from app.api.deps import not_found

            raise not_found("session", body.session_id)

    room_id = f"room_{uuid.uuid4().hex[:10]}"
    room = TeamRoom(room_id=room_id, session_id=body.session_id)
    _ROOMS[room_id] = room
    member = RoomMember(
        id=f"{body.trainee_id}:{body.role.value}",
        trainee_id=body.trainee_id,
        display_name=body.display_name or body.trainee_id,
        role=body.role,
        joined_t=time.time(),
    )
    room.members[member.id] = member
    return room.summary()


@router.get("/rooms/{room_id}", response_model=RoomSummary)
def read_room(room_id: str) -> RoomSummary:
    """The team scorecard: communication latency and handoff correctness."""
    room = get_room(room_id)
    if room is None:
        from app.api.deps import not_found

        raise not_found("room", room_id)
    return room.summary()


@router.websocket("/rooms/{room_id}/ws")
async def room_socket(websocket: WebSocket, room_id: str, role: str = "trainee") -> None:
    """Team room socket: every member sees the same authoritative frame."""
    room = get_room(room_id)
    if room is None:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    member_id = f"socket_{uuid.uuid4().hex[:8]}"
    room.sockets[member_id] = websocket
    live = live_registry.get(room.session_id)
    if live is not None:
        await websocket.send_json(
            {"type": "state_frame", "frame": live.current_frame().model_dump(mode="json")}
        )
    try:
        while True:
            message = await websocket.receive_text()
            payload = json.loads(message)
            if payload.get("type") != "action":
                continue
            action = TraineeAction.model_validate(payload.get("action") or {})
            member = next(iter(room.members.values()), None)
            if member is None:
                continue
            room.record_action(member, action)
            if live is not None:
                live.queue(action)
            for peer_id, peer in list(room.sockets.items()):
                if peer_id == member_id:
                    continue
                with contextlib.suppress(Exception):
                    await peer.send_json(
                        {"type": "member_action", "action": action.model_dump(mode="json")}
                    )
    except WebSocketDisconnect:
        pass
    finally:
        room.sockets.pop(member_id, None)


def clear_rooms() -> None:
    """Drop every room (used by tests)."""
    for room_id in list(_ROOMS):
        drop_room(room_id)
