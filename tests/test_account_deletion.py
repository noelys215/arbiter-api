from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


async def test_account_deletion_requires_exact_confirmation(client, authed_user):
    await authed_user(client)
    response = await client.request(
        "DELETE", "/me", json={"confirmation": "delete"}
    )
    assert response.status_code == 422


async def test_account_deletion_revokes_authentication(client, authed_user):
    await authed_user(client)
    response = await client.request(
        "DELETE", "/me", json={"confirmation": "DELETE"}
    )
    assert response.status_code == 204, response.text
    assert client.cookies.get("access_token") is None
    assert (await client.get("/me")).status_code == 401


async def test_account_deletion_requires_owned_group_resolution(
    client, authed_user
):
    await authed_user(client)
    created = await client.post("/groups", json={"name": "Owned group"})
    assert created.status_code == 201, created.text

    response = await client.request(
        "DELETE", "/me", json={"confirmation": "DELETE"}
    )
    assert response.status_code == 409
    assert response.json() == {
        "detail": "Transfer or delete groups you own before deleting your account."
    }
    assert (await client.get("/me")).status_code == 200
