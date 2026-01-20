import pytest

pytestmark = pytest.mark.anyio


async def test_register_login_me(client):
    # register
    r = await client.post(
        "/auth/register",
        json={
            "email": "a@example.com",
            "username": "auser",
            "display_name": "A",
            "password": "SuperSecret123",
        },
    )
    assert r.status_code in (200, 201)

    # login (cookie should be set)
    r = await client.post("/auth/login", json={"email": "a@example.com", "password": "SuperSecret123"})
    assert r.status_code == 200
    assert "set-cookie" in r.headers
    assert "access_token=" in r.headers["set-cookie"]

    # /me should work (your route is /me, not /auth/me)
    r = await client.get("/me")
    assert r.status_code == 200
    data = r.json()
    assert data["email"] == "a@example.com"
    assert data["username"] == "auser"
    assert data["display_name"] == "A"


async def test_me_requires_auth(client):
    r = await client.get("/me")
    # Depending on your auth logic, could be 401 or 403
    assert r.status_code in (401, 403)
