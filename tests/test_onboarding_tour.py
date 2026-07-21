from datetime import datetime

import pytest


pytestmark = pytest.mark.anyio


async def test_onboarding_state_requires_authentication(client):
    response = await client.patch(
        "/me/onboarding-tour",
        json={"version": 1, "status": "completed"},
    )
    assert response.status_code in (401, 403)


async def test_user_can_persist_own_onboarding_state(
    client, user_factory, login_helper
):
    user = await user_factory(client)
    await login_helper(client, email=user["email"], password=user["password"])

    before = await client.get("/me")
    assert before.status_code == 200
    assert before.json()["onboarding_tour_version"] is None
    assert before.json()["onboarding_tour_status"] is None

    response = await client.patch(
        "/me/onboarding-tour",
        json={"version": 1, "status": "completed"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["id"] == user["id"]
    assert payload["onboarding_tour_version"] == 1
    assert payload["onboarding_tour_status"] == "completed"
    datetime.fromisoformat(payload["onboarding_tour_updated_at"])


async def test_repeated_onboarding_update_is_idempotent(
    client, user_factory, login_helper
):
    user = await user_factory(client)
    await login_helper(client, email=user["email"], password=user["password"])
    request = {"version": 1, "status": "skipped"}

    first = await client.patch("/me/onboarding-tour", json=request)
    second = await client.patch("/me/onboarding-tour", json=request)

    assert first.status_code == second.status_code == 200
    assert (
        first.json()["onboarding_tour_updated_at"]
        == second.json()["onboarding_tour_updated_at"]
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 0, "status": "completed"},
        {"version": 1, "status": "dismissed"},
        {"version": "1", "status": "completed"},
        {"version": 1, "status": "completed", "user_id": "someone-else"},
    ],
)
async def test_onboarding_update_rejects_invalid_or_protected_fields(
    client, user_factory, login_helper, payload
):
    user = await user_factory(client)
    await login_helper(client, email=user["email"], password=user["password"])

    response = await client.patch("/me/onboarding-tour", json=payload)

    assert response.status_code == 422


async def test_older_tour_version_cannot_overwrite_newer_state(
    client, user_factory, login_helper
):
    user = await user_factory(client)
    await login_helper(client, email=user["email"], password=user["password"])
    await client.patch(
        "/me/onboarding-tour", json={"version": 2, "status": "completed"}
    )

    response = await client.patch(
        "/me/onboarding-tour", json={"version": 1, "status": "skipped"}
    )

    assert response.status_code == 200
    assert response.json()["onboarding_tour_version"] == 2
    assert response.json()["onboarding_tour_status"] == "completed"
