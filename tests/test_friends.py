import pytest

pytestmark = pytest.mark.anyio


async def register_and_login(client, email, username):
    r = await client.post(
        "/auth/register",
        json={"email": email, "username": username, "display_name": username.upper(), "password": "SuperSecret123"},
    )
    assert r.status_code in (200, 201)

    r = await client.post("/auth/login", json={"email": email, "password": "SuperSecret123"})
    assert r.status_code == 200


async def test_friend_invite_accept_flow(client):
    # User A
    await register_and_login(client, "fa@example.com", "fauser")

    # Create invite
    r = await client.post("/friends/invite")
    assert r.status_code in (200, 201)
    code = r.json()["code"]
    assert isinstance(code, str) and len(code) >= 6

    # Create separate client for User B (fresh cookies)
    from httpx import ASGITransport, AsyncClient
    transport_b = ASGITransport(app=client._transport.app)
    async with AsyncClient(transport=transport_b, base_url="http://test") as client_b:
        # Register/login B
        await register_and_login(client_b, "fb@example.com", "fbuser")

        # Accept invite
        r = await client_b.post("/friends/accept", json={"code": code})
        assert r.status_code == 200
        assert r.json().get("ok") is True

        # B lists friends -> sees A
        r = await client_b.get("/friends")
        assert r.status_code == 200
        friends = r.json()
        assert any(f["email"] == "fa@example.com" for f in friends)

    # A lists friends -> sees B
    r = await client.get("/friends")
    assert r.status_code == 200
    friends = r.json()
    assert any(f["email"] == "fb@example.com" for f in friends)


async def test_invite_can_only_be_used_once(client):
    # A
    await register_and_login(client, "a2@example.com", "auser2")
    r = await client.post("/friends/invite")
    code = r.json()["code"]

    # B accepts
    from httpx import ASGITransport, AsyncClient
    transport_b = ASGITransport(app=client._transport.app)
    async with AsyncClient(transport=transport_b, base_url="http://test") as client_b:
        await register_and_login(client_b, "b2@example.com", "buser2")
        r = await client_b.post("/friends/accept", json={"code": code})
        assert r.status_code == 200

    # C tries to accept same code -> should fail (400/409)
    transport_c = ASGITransport(app=client._transport.app)
    async with AsyncClient(transport=transport_c, base_url="http://test") as client_c:
        await register_and_login(client_c, "c2@example.com", "cuser2")
        r = await client_c.post("/friends/accept", json={"code": code})
        assert r.status_code in (400, 409)


async def test_cannot_accept_own_invite(client):
    await register_and_login(client, "self@example.com", "selfuser")
    r = await client.post("/friends/invite")
    code = r.json()["code"]

    r = await client.post("/friends/accept", json={"code": code})
    assert r.status_code in (400, 409)
