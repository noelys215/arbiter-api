import pytest

pytestmark = pytest.mark.anyio


async def test_friend_invite_accept_flow(client, client_factory, user_factory, login_helper):
    # User A
    user_a = await user_factory(client, display_name="A")
    await login_helper(client, email=user_a["email"], password=user_a["password"])

    # Create invite
    r = await client.post("/friends/invite")
    assert r.status_code in (200, 201)
    code = r.json()["code"]
    assert isinstance(code, str) and len(code) >= 6

    # Create separate client for User B (fresh cookies)
    async with client_factory() as client_b:
        # Register/login B
        user_b = await user_factory(client_b, display_name="B")
        await login_helper(client_b, email=user_b["email"], password=user_b["password"])

        # Accept invite
        r = await client_b.post("/friends/accept", json={"code": code})
        assert r.status_code == 200
        assert r.json().get("ok") is True

        # B lists friends -> sees A
        r = await client_b.get("/friends")
        assert r.status_code == 200
        friends = r.json()
        assert any(f["email"] == user_a["email"] for f in friends)

    # A lists friends -> sees B
    r = await client.get("/friends")
    assert r.status_code == 200
    friends = r.json()
    assert any(f["email"] == user_b["email"] for f in friends)


async def test_invite_can_only_be_used_once(client, client_factory, user_factory, login_helper):
    # A
    user_a = await user_factory(client, display_name="A2")
    await login_helper(client, email=user_a["email"], password=user_a["password"])
    r = await client.post("/friends/invite")
    code = r.json()["code"]

    # B accepts
    async with client_factory() as client_b:
        user_b = await user_factory(client_b, display_name="B2")
        await login_helper(client_b, email=user_b["email"], password=user_b["password"])
        r = await client_b.post("/friends/accept", json={"code": code})
        assert r.status_code == 200

    # C tries to accept same code -> should fail (400/409)
    async with client_factory() as client_c:
        user_c = await user_factory(client_c, display_name="C2")
        await login_helper(client_c, email=user_c["email"], password=user_c["password"])
        r = await client_c.post("/friends/accept", json={"code": code})
        assert r.status_code in (400, 409)


async def test_cannot_accept_own_invite(client, user_factory, login_helper):
    user = await user_factory(client, display_name="Self")
    await login_helper(client, email=user["email"], password=user["password"])
    r = await client.post("/friends/invite")
    code = r.json()["code"]

    r = await client.post("/friends/accept", json={"code": code})
    assert r.status_code in (400, 409)
