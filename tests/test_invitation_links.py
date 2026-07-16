from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.return_paths import validate_invite_return_path
from app.models.friend_invite import FriendInvite
from app.models.group_invite import GroupInvite

pytestmark = pytest.mark.anyio


async def _users(client, client_factory, user_factory, login_helper):
    user_a = await user_factory(client, display_name="Test User A")
    await login_helper(client, email=user_a["email"], password=user_a["password"])
    async with client_factory() as client_b:
        user_b = await user_factory(client_b, display_name="Test User B")
        token_b = await login_helper(
            client_b,
            email=user_b["email"],
            password=user_b["password"],
        )
    return user_a, user_b, token_b


async def test_friendship_is_visible_with_zero_groups(
    client, client_factory, user_factory, login_helper
):
    user_a, user_b, token_b = await _users(
        client, client_factory, user_factory, login_helper
    )
    created = await client.post("/friends/invites")
    assert created.status_code == 201, created.text
    token = created.json()["token"]

    async with client_factory() as client_b:
        client_b.cookies.set("access_token", token_b)
        accepted = await client_b.post(f"/invites/friend/{token}/accept")
        assert accepted.status_code == 200, accepted.text
        assert (await client_b.get("/groups")).json() == []
        friends_b = (await client_b.get("/friends")).json()
        assert [friend["id"] for friend in friends_b] == [user_a["id"]]

    assert (await client.get("/groups")).json() == []
    friends_a = (await client.get("/friends")).json()
    assert [friend["id"] for friend in friends_a] == [user_b["id"]]


async def test_friend_preview_is_public_and_excludes_email(
    client, user_factory, login_helper
):
    await user_factory(client, display_name="Private Inviter")
    user = await user_factory(client, display_name="Public Inviter")
    await login_helper(client, email=user["email"], password=user["password"])
    token = (await client.post("/friends/invites")).json()["token"]

    client.cookies.clear()
    preview = await client.get(f"/invites/friend/{token}")
    assert preview.status_code == 200
    assert preview.json()["inviter"]["display_name"] == "Public Inviter"
    assert "email" not in preview.json()["inviter"]


async def test_friend_invite_creator_cannot_accept_own_invite(
    client, user_factory, login_helper
):
    user = await user_factory(client)
    await login_helper(client, email=user["email"], password=user["password"])
    token = (await client.post("/friends/invites")).json()["token"]

    response = await client.post(f"/invites/friend/{token}/accept")

    assert response.status_code == 400
    assert response.json() == {"detail": "You cannot accept your own invitation."}
    assert (await client.get("/friends")).json() == []


async def test_raw_friend_token_is_not_persisted(
    client, user_factory, login_helper, db_session
):
    user = await user_factory(client)
    await login_helper(client, email=user["email"], password=user["password"])
    payload = (await client.post("/friends/invites")).json()
    invite = (
        await db_session.execute(
            select(FriendInvite).where(FriendInvite.id == uuid.UUID(payload["id"]))
        )
    ).scalar_one()
    assert invite.token_hash
    assert invite.token_hash != payload["token"]
    assert len(invite.token_hash) == 64


async def test_revoking_friend_invite_invalidates_token_and_code(
    client, user_factory, login_helper
):
    user = await user_factory(client)
    await login_helper(client, email=user["email"], password=user["password"])
    payload = (await client.post("/friends/invites")).json()
    assert (await client.delete(f"/friends/invites/{payload['id']}")).status_code == 204

    assert (await client.get(f"/invites/friend/{payload['token']}")).status_code == 410
    code_accept = await client.post("/friends/accept", json={"code": payload["code"]})
    assert code_accept.status_code == 410


async def test_valid_group_invite_preview_returns_public_group_context(
    client, user_factory, login_helper
):
    user = await user_factory(client, display_name="Preview Host")
    await login_helper(client, email=user["email"], password=user["password"])
    group = (await client.post("/groups", json={"name": "Preview Club"})).json()
    invite = (
        await client.post(
            f"/groups/{group['id']}/invites",
            json={"target_user_id": None, "max_uses": 25},
        )
    ).json()

    client.cookies.clear()
    response = await client.get(f"/invites/group/{invite['token']}")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "group_id": group["id"],
        "group_name": "Preview Club",
        "inviter": {
            "id": user["id"],
            "username": user["username"],
            "display_name": "Preview Host",
            "avatar_url": None,
            "avatar_source": None,
            "avatar_style": None,
            "avatar_seed": None,
        },
        "member_count": 1,
        "expires_at": invite["expires_at"],
        "targeted": False,
    }


async def test_targeted_group_invite_requires_acceptance_and_is_idempotent(
    client, client_factory, user_factory, login_helper
):
    user_a, user_b, token_b = await _users(
        client, client_factory, user_factory, login_helper
    )
    friend_token = (await client.post("/friends/invites")).json()["token"]
    async with client_factory() as client_b:
        client_b.cookies.set("access_token", token_b)
        assert (await client_b.post(f"/invites/friend/{friend_token}/accept")).status_code == 200

    group = await client.post("/groups", json={"name": "Match Club", "member_user_ids": []})
    group_id = group.json()["id"]
    created = await client.post(
        f"/groups/{group_id}/invites",
        json={"target_user_id": user_b["id"], "max_uses": 25},
    )
    assert created.status_code == 201, created.text
    invite = created.json()
    assert invite["max_uses"] == 1

    async with client_factory() as client_b:
        client_b.cookies.set("access_token", token_b)
        assert (await client_b.get("/groups")).json() == []
        incoming = (await client_b.get("/group-invites")).json()
        assert [row["id"] for row in incoming] == [invite["id"]]
        first = await client_b.post(
            f"/group-invites/{invite['id']}/decision", json={"decision": "accept"}
        )
        assert first.status_code == 200
        assert first.json()["already_member"] is False
        second = await client_b.post(f"/invites/group/{invite['token']}/accept")
        assert second.status_code == 200
        assert second.json()["already_member"] is True

    outgoing = (await client.get(f"/group-invites?group_id={group_id}")).json()
    assert outgoing == []
    assert any(friend["id"] == user_b["id"] for friend in (await client.get("/friends")).json())
    assert user_a["id"] != user_b["id"]


async def test_declined_targeted_invite_is_terminal(
    client, client_factory, user_factory, login_helper
):
    _, user_b, token_b = await _users(client, client_factory, user_factory, login_helper)
    friend_token = (await client.post("/friends/invites")).json()["token"]
    async with client_factory() as client_b:
        client_b.cookies.set("access_token", token_b)
        await client_b.post(f"/invites/friend/{friend_token}/accept")
    group_id = (await client.post("/groups", json={"name": "Decline Club"})).json()["id"]
    invite = (
        await client.post(
            f"/groups/{group_id}/invites",
            json={"target_user_id": user_b["id"], "max_uses": 1},
        )
    ).json()
    async with client_factory() as client_b:
        client_b.cookies.set("access_token", token_b)
        decline = await client_b.post(
            f"/group-invites/{invite['id']}/decision", json={"decision": "decline"}
        )
        assert decline.status_code == 200
        assert (await client_b.post(f"/invites/group/{invite['token']}/accept")).status_code == 410
        assert (await client_b.post("/groups/accept-invite", json={"code": invite["code"]})).status_code == 410


async def test_group_leave_and_delete_preserve_friendship(
    client, client_factory, user_factory, login_helper
):
    _, user_b, token_b = await _users(client, client_factory, user_factory, login_helper)
    friend_token = (await client.post("/friends/invites")).json()["token"]
    async with client_factory() as client_b:
        client_b.cookies.set("access_token", token_b)
        await client_b.post(f"/invites/friend/{friend_token}/accept")
    group_id = (
        await client.post(
            "/groups", json={"name": "Independent Friends", "member_user_ids": [user_b["id"]]}
        )
    ).json()["id"]

    async with client_factory() as client_b:
        client_b.cookies.set("access_token", token_b)
        assert (await client_b.post(f"/groups/{group_id}/leave")).status_code == 200
        assert len((await client_b.get("/friends")).json()) == 1

    assert (await client.delete(f"/groups/{group_id}")).status_code == 200
    assert len((await client.get("/friends")).json()) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (f"/invite/friend/{'a' * 43}", True),
        (f"/invite/group/{'A_-' * 14}A", True),
        ("https://evil.example/invite/friend/token", False),
        ("//evil.example/invite/friend/token", False),
        (f"/invite/friend/{'a' * 43}?next=https://evil.example", False),
        (f"/invite/group/{'a' * 43}#fragment", False),
        (f"%2F%2Fevil.example/{'a' * 43}", False),
        (f"/app/{'a' * 43}", False),
    ],
)
def test_invite_return_path_validation(value, expected):
    assert (validate_invite_return_path(value) is not None) is expected
