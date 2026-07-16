from uuid import uuid4

import pytest

from app.services.watchlist_realtime import WatchlistRealtimeHub


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
async def test_watchlist_realtime_hub_broadcasts_to_group_connections():
    hub = WatchlistRealtimeHub()
    group_id = uuid4()
    user_id = uuid4()
    socket = FakeWebSocket()

    await hub.connect(group_id, user_id, socket)  # type: ignore[arg-type]
    await hub.broadcast_watchlist_updated(group_id, reason="item_added")

    assert socket.accepted is True
    assert socket.sent == [
        {
            "type": "watchlist_updated",
            "group_id": str(group_id),
            "reason": "item_added",
        }
    ]


@pytest.mark.anyio
async def test_watchlist_realtime_hub_drops_closed_connections():
    hub = WatchlistRealtimeHub()
    group_id = uuid4()
    user_id = uuid4()
    closed_socket = FakeWebSocket(fail_send=True)
    open_socket = FakeWebSocket()

    await hub.connect(group_id, user_id, closed_socket)  # type: ignore[arg-type]
    await hub.connect(group_id, user_id, open_socket)  # type: ignore[arg-type]
    await hub.broadcast_watchlist_updated(group_id, reason="item_removed")
    await hub.broadcast_watchlist_updated(group_id, reason="item_updated")

    assert open_socket.sent == [
        {
            "type": "watchlist_updated",
            "group_id": str(group_id),
            "reason": "item_removed",
        },
        {
            "type": "watchlist_updated",
            "group_id": str(group_id),
            "reason": "item_updated",
        },
    ]


@pytest.mark.anyio
async def test_watchlist_realtime_hub_disconnects_only_requested_user():
    hub = WatchlistRealtimeHub()
    group_id = uuid4()
    removed_user_id = uuid4()
    remaining_user_id = uuid4()
    removed_socket = FakeWebSocket()
    remaining_socket = FakeWebSocket()

    await hub.connect(group_id, removed_user_id, removed_socket)  # type: ignore[arg-type]
    await hub.connect(group_id, remaining_user_id, remaining_socket)  # type: ignore[arg-type]
    await hub.disconnect_user(group_id, removed_user_id)
    await hub.broadcast_watchlist_updated(group_id, reason="item_updated")

    assert removed_socket.closed_code == 1008
    assert removed_socket.sent == []
    assert remaining_socket.closed_code is None
    assert remaining_socket.sent[0]["type"] == "watchlist_updated"
