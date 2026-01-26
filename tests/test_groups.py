from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.anyio


def _u(prefix: str) -> str:
    """Unique string helper for emails/usernames."""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


async def register_user(client, *, email: str, username: str, display_name: str, password: str) -> str:
    r = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "display_name": display_name,
            "password": password,
        },
    )
    assert r.status_code in (200, 201), r.text
    data = r.json()
    assert "id" in data
    return data["id"]


async def login_and_get_token(client, *, email: str, password: str) -> str:
    # Ensure we're not accidentally reusing an old token
    client.cookies.clear()

    r = await client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert r.status_code == 200, r.text

    token = client.cookies.get("access_token")
    assert token, "Login did not set access_token cookie"
    return token


def act_as_token(client, token: str | None):
    client.cookies.clear()
    if token:
        client.cookies.set("access_token", token)


async def create_friendship(client, *, token_a: str, token_b: str) -> None:
    # A creates invite code
    act_as_token(client, token_a)
    r = await client.post("/friends/invite")
    assert r.status_code in (200, 201), r.text
    code = r.json()["code"]
    assert code

    # B accepts
    act_as_token(client, token_b)
    r = await client.post("/friends/accept", json={"code": code})
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True


async def create_group(client, *, token_owner: str, name: str, member_user_ids: list[str]) -> str:
    act_as_token(client, token_owner)
    r = await client.post(
        "/groups",
        json={"name": name, "member_user_ids": member_user_ids},
    )
    assert r.status_code in (200, 201), r.text
    data = r.json()
    assert "id" in data
    return data["id"]


async def get_group_invite_code(client, *, token_owner: str, group_id: str) -> str:
    act_as_token(client, token_owner)
    r = await client.post(f"/groups/{group_id}/invite")
    assert r.status_code in (200, 201), r.text
    code = r.json()["code"]
    assert code
    return code


async def accept_group_invite(client, *, token_user: str, code: str) -> None:
    act_as_token(client, token_user)
    r = await client.post("/groups/accept-invite", json={"code": code})
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True


async def test_groups_full_flow_owner_member_invite_accept(client):
    password = "SuperSecret123"

    # Create 3 users: A (owner), B (member via create), C (joins via invite)
    a_email = f"{_u('a')}@example.com"
    b_email = f"{_u('b')}@example.com"
    c_email = f"{_u('c')}@example.com"

    a_username = _u("usera")
    b_username = _u("userb")
    c_username = _u("userc")

    a_id = await register_user(client, email=a_email, username=a_username, display_name="A", password=password)
    b_id = await register_user(client, email=b_email, username=b_username, display_name="B", password=password)
    c_id = await register_user(client, email=c_email, username=c_username, display_name="C", password=password)

    token_a = await login_and_get_token(client, email=a_email, password=password)
    token_b = await login_and_get_token(client, email=b_email, password=password)
    token_c = await login_and_get_token(client, email=c_email, password=password)

    # A + B must be friends to create a group containing B
    await create_friendship(client, token_a=token_a, token_b=token_b)

    # A creates group with B as initial member
    group_id = await create_group(client, token_owner=token_a, name="Movie Night", member_user_ids=[b_id])
    assert group_id

    # A lists groups: should include the group
    act_as_token(client, token_a)
    r = await client.get("/groups")
    assert r.status_code == 200, r.text
    groups_a = r.json()
    assert any(g["id"] == group_id for g in groups_a)

    # B lists groups: should include the group
    act_as_token(client, token_b)
    r = await client.get("/groups")
    assert r.status_code == 200, r.text
    groups_b = r.json()
    assert any(g["id"] == group_id for g in groups_b)

    # Group detail should show at least A and B as members
    act_as_token(client, token_a)
    r = await client.get(f"/groups/{group_id}")
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["id"] == group_id
    member_ids = {m["id"] for m in detail["members"]}
    assert a_id in member_ids
    assert b_id in member_ids

    # Owner generates invite code
    invite_code = await get_group_invite_code(client, token_owner=token_a, group_id=group_id)

    # C joins via invite code
    await accept_group_invite(client, token_user=token_c, code=invite_code)

    # C should now see the group
    act_as_token(client, token_c)
    r = await client.get("/groups")
    assert r.status_code == 200, r.text
    groups_c = r.json()
    assert any(g["id"] == group_id for g in groups_c)

    # Group detail should include C now
    act_as_token(client, token_a)
    r = await client.get(f"/groups/{group_id}")
    assert r.status_code == 200, r.text
    detail2 = r.json()
    member_ids2 = {m["id"] for m in detail2["members"]}
    assert c_id in member_ids2


async def test_group_detail_requires_membership(client):
    password = "SuperSecret123"

    a_email = f"{_u('a')}@example.com"
    b_email = f"{_u('b')}@example.com"
    x_email = f"{_u('x')}@example.com"

    a_username = _u("usera")
    b_username = _u("userb")
    x_username = _u("userx")

    a_id = await register_user(client, email=a_email, username=a_username, display_name="A", password=password)
    b_id = await register_user(client, email=b_email, username=b_username, display_name="B", password=password)
    _ = await register_user(client, email=x_email, username=x_username, display_name="X", password=password)

    token_a = await login_and_get_token(client, email=a_email, password=password)
    token_b = await login_and_get_token(client, email=b_email, password=password)
    token_x = await login_and_get_token(client, email=x_email, password=password)

    # A + B are friends so A can create group with B
    await create_friendship(client, token_a=token_a, token_b=token_b)

    group_id = await create_group(client, token_owner=token_a, name="Private Group", member_user_ids=[b_id])

    # X is NOT a member; should be blocked
    act_as_token(client, token_x)
    r = await client.get(f"/groups/{group_id}")

    assert r.status_code in (401, 403, 404), r.text


async def test_only_owner_can_generate_group_invite(client):
    password = "SuperSecret123"

    a_email = f"{_u('a')}@example.com"
    b_email = f"{_u('b')}@example.com"

    a_username = _u("usera")
    b_username = _u("userb")

    a_id = await register_user(client, email=a_email, username=a_username, display_name="A", password=password)
    b_id = await register_user(client, email=b_email, username=b_username, display_name="B", password=password)

    token_a = await login_and_get_token(client, email=a_email, password=password)
    token_b = await login_and_get_token(client, email=b_email, password=password)

    await create_friendship(client, token_a=token_a, token_b=token_b)

    group_id = await create_group(client, token_owner=token_a, name="Owner Only Invites", member_user_ids=[b_id])

    # B is a member but not owner; should be blocked from invite generation
    act_as_token(client, token_b)
    r = await client.post(f"/groups/{group_id}/invite")

    assert r.status_code in (401, 403), r.text


async def test_accept_group_invite_invalid_code_returns_400(client):
    password = "SuperSecret123"

    c_email = f"{_u('c')}@example.com"
    c_username = _u("userc")

    _ = await register_user(client, email=c_email, username=c_username, display_name="C", password=password)
    token_c = await login_and_get_token(client, email=c_email, password=password)

    act_as_token(client, token_c)
    r = await client.post("/groups/accept-invite", json={"code": "NOT_A_REAL_CODE"})
    assert r.status_code in (400, 404), r.text
