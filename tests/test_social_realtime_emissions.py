from __future__ import annotations

from uuid import UUID

import pytest

pytestmark = pytest.mark.anyio


async def _two_users(client, client_factory, user_factory, login_helper):
    user_a = await user_factory(client, display_name="Realtime A")
    await login_helper(client, email=user_a["email"], password=user_a["password"])
    async with client_factory() as client_b:
        user_b = await user_factory(client_b, display_name="Realtime B")
        token_b = await login_helper(
            client_b,
            email=user_b["email"],
            password=user_b["password"],
        )
    return user_a, user_b, token_b


async def test_friendship_event_emits_once_to_both_users_after_creation(
    client,
    client_factory,
    user_factory,
    login_helper,
    monkeypatch,
):
    from app.api.routes import invites as invite_routes

    user_a, user_b, token_b = await _two_users(
        client, client_factory, user_factory, login_helper
    )
    token = (await client.post("/friends/invites")).json()["token"]
    events: list[tuple[set[UUID], str]] = []

    async def record(user_ids, *, reason: str):
        events.append((set(user_ids), reason))

    monkeypatch.setattr(invite_routes, "publish_friendship_update", record)
    async with client_factory() as client_b:
        client_b.cookies.set("access_token", token_b)
        first = await client_b.post(f"/invites/friend/{token}/accept")
        second = await client_b.post(f"/invites/friend/{token}/accept")

    assert first.status_code == 200
    assert second.status_code == 200
    assert events == [
        (
            {UUID(user_a["id"]), UUID(user_b["id"])},
            "friendship_created",
        )
    ]


async def test_targeted_group_invite_and_acceptance_emit_compact_updates(
    client,
    client_factory,
    user_factory,
    login_helper,
    monkeypatch,
):
    from app.api.routes import group_invites as decision_routes
    from app.api.routes import groups as group_routes

    user_a, user_b, token_b = await _two_users(
        client, client_factory, user_factory, login_helper
    )
    friend_token = (await client.post("/friends/invites")).json()["token"]
    async with client_factory() as client_b:
        client_b.cookies.set("access_token", token_b)
        await client_b.post(f"/invites/friend/{friend_token}/accept")

    group_id = (
        await client.post("/groups", json={"name": "Realtime Club"})
    ).json()["id"]
    invite_events: list[tuple[set[UUID], str, UUID]] = []
    group_events: list[tuple[set[UUID], str, UUID, UUID | None]] = []

    async def record_invite(user_ids, *, reason: str, group_id: UUID):
        invite_events.append((set(user_ids), reason, group_id))

    async def record_group(
        user_ids,
        *,
        reason: str,
        group_id: UUID,
        member_user_id: UUID | None = None,
    ):
        group_events.append((set(user_ids), reason, group_id, member_user_id))

    monkeypatch.setattr(group_routes, "publish_group_invite_update", record_invite)
    created = await client.post(
        f"/groups/{group_id}/invites",
        json={"target_user_id": user_b["id"], "max_uses": 1},
    )
    invite_id = created.json()["id"]
    assert invite_events[-1][1] == "targeted_invite_created"
    assert invite_events[-1][0] == {UUID(user_a["id"]), UUID(user_b["id"])}

    monkeypatch.setattr(decision_routes, "publish_group_invite_update", record_invite)
    monkeypatch.setattr(decision_routes, "publish_group_update", record_group)
    async with client_factory() as client_b:
        client_b.cookies.set("access_token", token_b)
        accepted = await client_b.post(
            f"/group-invites/{invite_id}/decision",
            json={"decision": "accept"},
        )

    assert accepted.status_code == 200
    assert invite_events[-1][1] == "invite_accepted"
    assert group_events[-1][1:] == (
        "membership_created",
        UUID(group_id),
        UUID(user_b["id"]),
    )
    assert group_events[-1][0] == {UUID(user_a["id"]), UUID(user_b["id"])}


async def test_profile_and_group_rename_emit_compact_updates(
    client,
    user_factory,
    login_helper,
    monkeypatch,
):
    from app.api.routes import groups as group_routes
    from app.api.routes import me as me_routes

    user = await user_factory(client, display_name="Before")
    await login_helper(client, email=user["email"], password=user["password"])
    profile_events: list[tuple[set[UUID], UUID]] = []
    group_events: list[tuple[set[UUID], str, UUID]] = []

    async def record_profile(user_ids, *, user_id: UUID):
        profile_events.append((set(user_ids), user_id))

    async def record_group(
        user_ids,
        *,
        reason: str,
        group_id: UUID,
        member_user_id: UUID | None = None,
    ):
        _ = member_user_id
        group_events.append((set(user_ids), reason, group_id))

    monkeypatch.setattr(me_routes, "publish_profile_update", record_profile)
    profile_response = await client.patch(
        "/me",
        json={"display_name": "After"},
    )
    assert profile_response.status_code == 200
    assert profile_events == [({UUID(user["id"])}, UUID(user["id"]))]

    group_id = UUID((await client.post("/groups", json={"name": "Before"})).json()["id"])
    monkeypatch.setattr(group_routes, "publish_group_update", record_group)
    group_response = await client.patch(
        f"/groups/{group_id}",
        json={"name": "After"},
    )
    assert group_response.status_code == 200
    assert group_events == [({UUID(user["id"])}, "group_renamed", group_id)]
