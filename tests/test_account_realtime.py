from uuid import uuid4

import pytest

from app.services.account_realtime import (
    AccountRealtimeHub,
    friendship_updated_event,
    group_invite_updated_event,
    group_updated_event,
)


class FakeWebSocket:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.accepted = False
        self.sent: list[dict] = []
        self.fail_send = fail_send

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        if self.fail_send:
            raise RuntimeError("socket closed")
        self.sent.append(payload)


@pytest.mark.anyio
async def test_account_hub_isolates_user_rooms_and_allows_multiple_tabs():
    hub = AccountRealtimeHub()
    user_a = uuid4()
    user_b = uuid4()
    socket_a_1 = FakeWebSocket()
    socket_a_2 = FakeWebSocket()
    socket_b = FakeWebSocket()

    await hub.connect(user_a, socket_a_1)  # type: ignore[arg-type]
    await hub.connect(user_a, socket_a_2)  # type: ignore[arg-type]
    await hub.connect(user_b, socket_b)  # type: ignore[arg-type]
    event = friendship_updated_event(reason="friendship_created")
    await hub.broadcast_to_users([user_a], event)

    assert socket_a_1.sent == [event]
    assert socket_a_2.sent == [event]
    assert socket_b.sent == []


def test_account_events_are_compact_and_contain_no_private_fields():
    group_id = uuid4()
    member_id = uuid4()
    events = [
        friendship_updated_event(reason="friendship_removed"),
        group_invite_updated_event(
            reason="targeted_invite_created", group_id=group_id
        ),
        group_updated_event(
            reason="membership_created",
            group_id=group_id,
            member_user_id=member_id,
        ),
    ]

    for event in events:
        assert not ({"token", "code", "email", "name", "url"} & event.keys())
