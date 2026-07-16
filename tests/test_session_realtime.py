from uuid import uuid4

import pytest

from app.services.session_realtime import SessionRealtimeHub


class FakeWebSocket:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.accepted = False
        self.closed_code: int | None = None
        self.sent: list[dict] = []
        self.fail_send = fail_send

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        if self.fail_send:
            raise RuntimeError("socket closed")
        self.sent.append(payload)

    async def close(self, *, code: int) -> None:
        self.closed_code = code


@pytest.mark.anyio
async def test_session_realtime_hub_broadcasts_to_session_connections():
    hub = SessionRealtimeHub()
    session_id = uuid4()
    user_id = uuid4()
    group_id = uuid4()
    socket = FakeWebSocket()

    await hub.connect(session_id, user_id, group_id, socket)  # type: ignore[arg-type]
    await hub.broadcast_session_updated(session_id, reason="watch_party_updated")

    assert socket.accepted is True
    assert socket.sent == [
        {
            "type": "session_updated",
            "session_id": str(session_id),
            "reason": "watch_party_updated",
        }
    ]


@pytest.mark.anyio
async def test_session_realtime_hub_drops_closed_connections():
    hub = SessionRealtimeHub()
    session_id = uuid4()
    user_id = uuid4()
    group_id = uuid4()
    closed_socket = FakeWebSocket(fail_send=True)
    open_socket = FakeWebSocket()

    await hub.connect(session_id, user_id, group_id, closed_socket)  # type: ignore[arg-type]
    await hub.connect(session_id, user_id, group_id, open_socket)  # type: ignore[arg-type]
    await hub.broadcast_session_updated(session_id, reason="vote_cast")
    await hub.broadcast_session_updated(session_id, reason="session_changed")

    assert open_socket.sent == [
        {
            "type": "session_updated",
            "session_id": str(session_id),
            "reason": "vote_cast",
        },
        {
            "type": "session_updated",
            "session_id": str(session_id),
            "reason": "session_changed",
        },
    ]


@pytest.mark.anyio
async def test_session_realtime_hub_disconnects_only_user_in_affected_group():
    hub = SessionRealtimeHub()
    group_id = uuid4()
    other_group_id = uuid4()
    user_id = uuid4()
    affected_socket = FakeWebSocket()
    other_socket = FakeWebSocket()

    await hub.connect(uuid4(), user_id, group_id, affected_socket)  # type: ignore[arg-type]
    await hub.connect(uuid4(), user_id, other_group_id, other_socket)  # type: ignore[arg-type]
    await hub.disconnect_group_user(group_id, user_id)

    assert affected_socket.closed_code == 1008
    assert other_socket.closed_code is None
