import pytest

from social_helpers import add_friend_to_group, create_friendship


pytestmark = pytest.mark.anyio


async def _users(client, client_factory, user_factory, login_helper):
    owner = await user_factory(client, display_name="Owner")
    await login_helper(client, email=owner["email"], password=owner["password"])
    async with client_factory() as recipient_client:
        recipient = await user_factory(recipient_client, display_name="Recipient")
        recipient_token = await login_helper(
            recipient_client,
            email=recipient["email"],
            password=recipient["password"],
        )
    return owner, recipient, recipient_token


async def test_targeted_group_invitation_requires_explicit_acceptance(
    client, client_factory, user_factory, login_helper
):
    owner, recipient, recipient_token = await _users(
        client, client_factory, user_factory, login_helper
    )
    async with client_factory() as recipient_client:
        recipient_client.cookies.set("access_token", recipient_token)
        await create_friendship(
            client,
            recipient_client,
            recipient_email=recipient["email"],
        )
        group = (await client.post("/groups", json={"name": "Match Club"})).json()

        created = await client.post(
            f"/groups/{group['id']}/invites",
            json={"target_user_id": recipient["id"]},
        )
        assert created.status_code == 201
        assert set(created.json()) == {
            "id",
            "group_id",
            "target_user_id",
            "expires_at",
        }
        assert (await recipient_client.get("/groups")).json() == []

        invite_id = created.json()["id"]
        incoming = (await recipient_client.get("/group-invites")).json()
        assert incoming[0]["id"] == invite_id
        assert incoming[0]["target"]["id"] == recipient["id"]

        first = await recipient_client.post(
            f"/group-invites/{invite_id}/decision",
            json={"decision": "accept"},
        )
        second = await recipient_client.post(
            f"/group-invites/{invite_id}/decision",
            json={"decision": "accept"},
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["already_member"] is True
        assert (await recipient_client.get("/groups")).json()[0]["id"] == group["id"]


async def test_declined_group_invitation_is_terminal(
    client, client_factory, user_factory, login_helper
):
    _, recipient, recipient_token = await _users(
        client, client_factory, user_factory, login_helper
    )
    async with client_factory() as recipient_client:
        recipient_client.cookies.set("access_token", recipient_token)
        await create_friendship(
            client,
            recipient_client,
            recipient_email=recipient["email"],
        )
        group = (await client.post("/groups", json={"name": "Match Club"})).json()
        invite = await client.post(
            f"/groups/{group['id']}/invites",
            json={"target_user_id": recipient["id"]},
        )
        invite_id = invite.json()["id"]

        declined = await recipient_client.post(
            f"/group-invites/{invite_id}/decision",
            json={"decision": "decline"},
        )
        accepted = await recipient_client.post(
            f"/group-invites/{invite_id}/decision",
            json={"decision": "accept"},
        )
        assert declined.status_code == 200
        assert accepted.status_code == 410


async def test_group_membership_changes_do_not_change_friendship(
    client, client_factory, user_factory, login_helper
):
    _, recipient, recipient_token = await _users(
        client, client_factory, user_factory, login_helper
    )
    async with client_factory() as recipient_client:
        recipient_client.cookies.set("access_token", recipient_token)
        await create_friendship(
            client,
            recipient_client,
            recipient_email=recipient["email"],
        )
        group = (await client.post("/groups", json={"name": "Match Club"})).json()
        await add_friend_to_group(
            client,
            recipient_client,
            group_id=group["id"],
            target_user_id=recipient["id"],
        )
        assert (await recipient_client.post(f"/groups/{group['id']}/leave")).status_code == 200
        assert len((await recipient_client.get("/friends")).json()) == 1
        assert (await client.delete(f"/groups/{group['id']}")).status_code == 200
        assert len((await client.get("/friends")).json()) == 1


async def test_legacy_invitation_routes_are_removed(client):
    assert (await client.post("/friends/invite")).status_code == 404
    assert (await client.post("/friends/invites")).status_code == 404
    assert (await client.post("/friends/accept", json={"code": "old"})).status_code == 404
    assert (await client.get("/invites/friend/old-token")).status_code == 404
    assert (await client.get("/invites/group/old-token")).status_code == 404
    assert (await client.post("/groups/00000000-0000-0000-0000-000000000000/invite")).status_code == 404
    assert (await client.post("/groups/accept-invite", json={"code": "old"})).status_code in {404, 405}


async def test_direct_membership_addition_is_removed(
    client, user_factory, login_helper
):
    owner = await user_factory(client, display_name="Owner")
    member = await user_factory(client, display_name="Member")
    await login_helper(client, email=owner["email"], password=owner["password"])

    rejected_create = await client.post(
        "/groups",
        json={"name": "No Bypass", "member_user_ids": [member["id"]]},
    )
    assert rejected_create.status_code == 422

    group = (await client.post("/groups", json={"name": "No Bypass"})).json()
    direct_add = await client.post(
        f"/groups/{group['id']}/members",
        json={"member_user_ids": [member["id"]]},
    )
    assert direct_add.status_code == 404


async def test_owner_can_transfer_group_to_an_existing_member(
    client, client_factory, user_factory, login_helper
):
    owner, member, member_token = await _users(
        client, client_factory, user_factory, login_helper
    )
    async with client_factory() as member_client:
        member_client.cookies.set("access_token", member_token)
        await create_friendship(
            client,
            member_client,
            recipient_email=member["email"],
        )
        group = (await client.post("/groups", json={"name": "Hand Off"})).json()
        await add_friend_to_group(
            client,
            member_client,
            group_id=group["id"],
            target_user_id=member["id"],
        )

        transferred = await client.post(
            f"/groups/{group['id']}/transfer-ownership",
            json={"new_owner_user_id": member["id"]},
        )
        assert transferred.status_code == 200, transferred.text
        assert transferred.json()["owner_id"] == member["id"]

        former_owner_leave = await client.post(f"/groups/{group['id']}/leave")
        assert former_owner_leave.status_code == 200
        assert (await member_client.get(f"/groups/{group['id']}")).status_code == 200


async def test_ownership_transfer_requires_a_current_member(
    client, user_factory, login_helper
):
    owner = await user_factory(client, display_name="Owner")
    outsider = await user_factory(client, display_name="Outsider")
    await login_helper(client, email=owner["email"], password=owner["password"])
    group = (await client.post("/groups", json={"name": "Private"})).json()

    response = await client.post(
        f"/groups/{group['id']}/transfer-ownership",
        json={"new_owner_user_id": outsider["id"]},
    )
    assert response.status_code == 400
