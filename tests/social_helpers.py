from httpx import AsyncClient


def act_as(client: AsyncClient, token: str) -> None:
    client.cookies.clear()
    client.cookies.set("access_token", token)


async def create_friendship(
    sender: AsyncClient,
    recipient: AsyncClient,
    *,
    recipient_email: str,
) -> str:
    created = await sender.post("/friends/requests", json={"identifier": recipient_email})
    assert created.status_code == 201, created.text
    outgoing = (await sender.get("/friends/requests")).json()["outgoing"]
    request_id = outgoing[0]["id"]
    accepted = await recipient.post(
        f"/friends/requests/{request_id}/decision",
        json={"decision": "accept"},
    )
    assert accepted.status_code == 200, accepted.text
    return request_id


async def add_friend_to_group(
    owner: AsyncClient,
    recipient: AsyncClient,
    *,
    group_id: str,
    target_user_id: str,
) -> str:
    created = await owner.post(
        f"/groups/{group_id}/invites",
        json={"target_user_id": target_user_id},
    )
    assert created.status_code == 201, created.text
    invite_id = created.json()["id"]
    accepted = await recipient.post(
        f"/group-invites/{invite_id}/decision",
        json={"decision": "accept"},
    )
    assert accepted.status_code == 200, accepted.text
    return invite_id


async def create_friendship_with_tokens(
    client: AsyncClient,
    *,
    sender_token: str,
    recipient_token: str,
    recipient_email: str,
) -> str:
    act_as(client, sender_token)
    created = await client.post("/friends/requests", json={"identifier": recipient_email})
    assert created.status_code == 201, created.text
    request_id = (await client.get("/friends/requests")).json()["outgoing"][0]["id"]
    act_as(client, recipient_token)
    accepted = await client.post(
        f"/friends/requests/{request_id}/decision",
        json={"decision": "accept"},
    )
    assert accepted.status_code == 200, accepted.text
    return request_id


async def add_friend_to_group_with_tokens(
    client: AsyncClient,
    *,
    owner_token: str,
    recipient_token: str,
    group_id: str,
    target_user_id: str,
) -> str:
    act_as(client, owner_token)
    created = await client.post(
        f"/groups/{group_id}/invites",
        json={"target_user_id": target_user_id},
    )
    assert created.status_code == 201, created.text
    invite_id = created.json()["id"]
    act_as(client, recipient_token)
    accepted = await client.post(
        f"/group-invites/{invite_id}/decision",
        json={"decision": "accept"},
    )
    assert accepted.status_code == 200, accepted.text
    return invite_id
