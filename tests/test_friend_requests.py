from uuid import UUID

import pytest


pytestmark = pytest.mark.anyio


async def _two_users(client, client_factory, user_factory, login_helper):
    user_a = await user_factory(client, display_name="Request A")
    await login_helper(client, email=user_a["email"], password=user_a["password"])
    async with client_factory() as client_b:
        user_b = await user_factory(client_b, display_name="Request B")
        token_b = await login_helper(
            client_b, email=user_b["email"], password=user_b["password"]
        )
    return user_a, user_b, token_b


async def test_targeted_friend_request_is_listed_and_accepted_without_groups(
    client, client_factory, user_factory, login_helper
):
    user_a, user_b, token_b = await _two_users(
        client, client_factory, user_factory, login_helper
    )

    created = await client.post("/friends/requests", json={"identifier": user_b["email"]})
    assert created.status_code == 201
    assert created.json() == {"ok": True}

    outgoing = (await client.get("/friends/requests")).json()
    assert outgoing["incoming"] == []
    assert outgoing["outgoing"][0]["user"]["id"] == user_b["id"]
    request_id = outgoing["outgoing"][0]["id"]

    async with client_factory() as client_b:
        client_b.cookies.set("access_token", token_b)
        incoming = (await client_b.get("/friends/requests")).json()
        assert incoming["outgoing"] == []
        assert incoming["incoming"][0]["user"]["id"] == user_a["id"]

        accepted = await client_b.post(
            f"/friends/requests/{request_id}/decision",
            json={"decision": "accept"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["decision"] == "accepted"
        accepted_again = await client_b.post(
            f"/friends/requests/{request_id}/decision",
            json={"decision": "accept"},
        )
        assert accepted_again.status_code == 200
        assert accepted_again.json()["already_friends"] is True
        assert (await client_b.get("/friends/requests")).json()["incoming"] == []
        friends = (await client_b.get("/friends")).json()
        assert any(friend["id"] == user_a["id"] for friend in friends)
        assert all("email" not in friend for friend in friends)

    assert (await client.get("/friends/requests")).json()["outgoing"] == []
    friends = (await client.get("/friends")).json()
    assert any(friend["id"] == user_b["id"] for friend in friends)
    assert all("email" not in friend for friend in friends)


async def test_friend_request_prevents_self_existing_and_duplicate_requests(
    client, client_factory, user_factory, login_helper
):
    user_a, user_b, token_b = await _two_users(
        client, client_factory, user_factory, login_helper
    )
    self_request = await client.post(
        "/friends/requests", json={"identifier": user_a["email"]}
    )
    assert self_request.status_code == 400

    assert (
        await client.post("/friends/requests", json={"identifier": user_b["email"]})
    ).status_code == 201
    duplicate = await client.post(
        "/friends/requests", json={"identifier": user_b["email"]}
    )
    assert duplicate.status_code == 409

    async with client_factory() as client_b:
        client_b.cookies.set("access_token", token_b)
        reverse = await client_b.post(
            "/friends/requests", json={"identifier": user_a["email"]}
        )
        assert reverse.status_code == 409
        request_id = (await client_b.get("/friends/requests")).json()["incoming"][0]["id"]
        assert (
            await client_b.post(
                f"/friends/requests/{request_id}/decision",
                json={"decision": "accept"},
            )
        ).status_code == 200

    already_friends = await client.post(
        "/friends/requests", json={"identifier": user_b["email"]}
    )
    assert already_friends.status_code == 409


async def test_friend_request_decline_cancel_and_recipient_authorization(
    client, client_factory, user_factory, login_helper
):
    user_a, user_b, token_b = await _two_users(
        client, client_factory, user_factory, login_helper
    )
    await client.post("/friends/requests", json={"identifier": user_b["email"]})
    request_id = (await client.get("/friends/requests")).json()["outgoing"][0]["id"]

    unauthorized = await client.post(
        f"/friends/requests/{request_id}/decision", json={"decision": "accept"}
    )
    assert unauthorized.status_code == 403

    async with client_factory() as client_b:
        client_b.cookies.set("access_token", token_b)
        declined = await client_b.post(
            f"/friends/requests/{request_id}/decision",
            json={"decision": "decline"},
        )
        assert declined.status_code == 200
        assert declined.json()["decision"] == "declined"

    await client.post("/friends/requests", json={"identifier": user_b["email"]})
    second_id = (await client.get("/friends/requests")).json()["outgoing"][0]["id"]
    cancelled = await client.delete(f"/friends/requests/{second_id}")
    assert cancelled.status_code == 200
    assert cancelled.json()["decision"] == "cancelled"


async def test_unknown_email_is_a_private_successful_noop(
    client, user_factory, login_helper
):
    user = await user_factory(client)
    await login_helper(client, email=user["email"], password=user["password"])

    response = await client.post(
        "/friends/requests", json={"identifier": "missing-account@example.com"}
    )
    assert response.status_code == 201
    assert response.json() == {"ok": True}
    assert (await client.get("/friends/requests")).json() == {
        "incoming": [],
        "outgoing": [],
    }


async def test_friend_request_accepts_email_and_username(
    client, user_factory, login_helper, unique_str
):
    sender = await user_factory(client, display_name=unique_str("Sender"))
    await login_helper(client, email=sender["email"], password=sender["password"])
    recipients = [
        await user_factory(client, display_name=unique_str(label))
        for label in ("Email Friend", "Username Friend", "At User")
    ]
    identifiers = [
        recipients[0]["email"].upper(),
        recipients[1]["username"].swapcase(),
        f"@{recipients[2]['username'].swapcase()}",
    ]

    for recipient, identifier in zip(recipients, identifiers, strict=True):
        response = await client.post(
            "/friends/requests", json={"identifier": identifier}
        )
        assert response.status_code == 201
        outgoing = (await client.get("/friends/requests")).json()["outgoing"]
        assert len(outgoing) == 1
        assert outgoing[0]["user"]["id"] == recipient["id"]
        assert (
            await client.delete(f"/friends/requests/{outgoing[0]['id']}")
        ).status_code == 200

    display_only = await user_factory(
        client, display_name=unique_str("Display Only")
    )
    response = await client.post(
        "/friends/requests", json={"identifier": display_only["display_name"]}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "No Arbiter account uses that username."
    assert (await client.get("/friends/requests")).json()["outgoing"] == []


async def test_blocking_closes_requests_and_prevents_future_contact_until_unblocked(
    client, client_factory, user_factory, login_helper
):
    user_a, user_b, token_b = await _two_users(
        client, client_factory, user_factory, login_helper
    )
    assert (
        await client.post("/friends/requests", json={"identifier": user_b["username"]})
    ).status_code == 201

    async with client_factory() as client_b:
        client_b.cookies.set("access_token", token_b)
        blocked = await client_b.post(f"/friends/{user_a['id']}/block")
        assert blocked.status_code == 200
        assert (await client_b.get("/friends/requests")).json()["incoming"] == []
        blocked_users = (await client_b.get("/friends/blocked")).json()
        assert [row["id"] for row in blocked_users] == [user_a["id"]]

        client.cookies.clear()
        await login_helper(
            client, email=user_a["email"], password=user_a["password"]
        )
        hidden = await client.post(
            "/friends/requests", json={"identifier": user_b["username"]}
        )
        assert hidden.status_code == 201
        assert (await client.get("/friends/requests")).json()["outgoing"] == []

        unblocked = await client_b.delete(f"/friends/{user_a['id']}/block")
        assert unblocked.status_code == 200
        assert (await client_b.get("/friends/blocked")).json() == []

        retried = await client.post(
            "/friends/requests", json={"identifier": user_b["username"]}
        )
        assert retried.status_code == 201


async def test_blocking_an_existing_friend_removes_friendship_not_group_membership(
    client, client_factory, user_factory, login_helper
):
    user_a, user_b, token_b = await _two_users(
        client, client_factory, user_factory, login_helper
    )
    async with client_factory() as client_b:
        client_b.cookies.set("access_token", token_b)
        await client.post("/friends/requests", json={"identifier": user_b["email"]})
        request_id = (await client.get("/friends/requests")).json()["outgoing"][0]["id"]
        await client_b.post(
            f"/friends/requests/{request_id}/decision", json={"decision": "accept"}
        )
        group = (await client.post("/groups", json={"name": "Still Together"})).json()
        invite = await client.post(
            f"/groups/{group['id']}/invites",
            json={"target_user_id": user_b["id"]},
        )
        await client_b.post(
            f"/group-invites/{invite.json()['id']}/decision",
            json={"decision": "accept"},
        )

        await client_b.post(f"/friends/{user_a['id']}/block")
        assert (await client_b.get("/friends")).json() == []
        assert (await client.get("/friends")).json() == []
        assert (await client_b.get(f"/groups/{group['id']}")).status_code == 200


async def test_friend_request_emits_compact_updates_after_commit(
    client, client_factory, user_factory, login_helper, monkeypatch
):
    from app.api.routes import friends as friend_routes

    user_a, user_b, token_b = await _two_users(
        client, client_factory, user_factory, login_helper
    )
    request_events: list[tuple[set[UUID], str]] = []
    friendship_events: list[tuple[set[UUID], str]] = []

    async def record_request(user_ids, *, reason: str):
        request_events.append((set(user_ids), reason))

    async def record_friendship(user_ids, *, reason: str):
        friendship_events.append((set(user_ids), reason))

    monkeypatch.setattr(friend_routes, "publish_friend_request_update", record_request)
    monkeypatch.setattr(friend_routes, "publish_friendship_update", record_friendship)

    await client.post("/friends/requests", json={"identifier": user_b["email"]})
    request_id = (await client.get("/friends/requests")).json()["outgoing"][0]["id"]
    async with client_factory() as client_b:
        client_b.cookies.set("access_token", token_b)
        await client_b.post(
            f"/friends/requests/{request_id}/decision",
            json={"decision": "accept"},
        )

    recipients = {UUID(user_a["id"]), UUID(user_b["id"])}
    assert request_events == [
        (recipients, "request_created"),
        (recipients, "request_accepted"),
    ]
    assert friendship_events == [(recipients, "friendship_created")]
