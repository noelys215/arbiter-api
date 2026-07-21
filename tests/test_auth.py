from urllib.parse import parse_qs, urlsplit

import pytest
import jwt
from sqlalchemy import select

from app.core.config import settings
from app.models.auth_session import AuthSession
from app.models.magic_link_grant import MagicLinkGrant
from app.models.oauth_identity import OAuthIdentity

pytestmark = pytest.mark.anyio


async def test_register_login_me(client, user_factory, login_helper):
    user = await user_factory(client, display_name="A")
    await login_helper(client, email=user["email"], password=user["password"])

    # /me should work (your route is /me, not /auth/me)
    r = await client.get("/me")
    assert r.status_code == 200
    data = r.json()
    assert data["email"] == user["email"]
    assert data["username"] == user["username"]
    assert data["display_name"] == "A"


async def test_me_requires_auth(client):
    r = await client.get("/me")
    # Depending on your auth logic, could be 401 or 403
    assert r.status_code in (401, 403)


async def test_user_can_update_display_name(client, user_factory, login_helper):
    user = await user_factory(client, display_name="Original Name")
    await login_helper(client, email=user["email"], password=user["password"])

    response = await client.patch(
        "/me",
        json={"display_name": "  Movie Night Host  "},
    )

    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "Movie Night Host"
    assert (await client.get("/me")).json()["display_name"] == "Movie Night Host"


async def test_display_name_rejects_blank_value(client, user_factory, login_helper):
    user = await user_factory(client)
    await login_helper(client, email=user["email"], password=user["password"])

    response = await client.patch("/me", json={"display_name": "   "})

    assert response.status_code == 422


async def test_registration_rejects_case_insensitive_identifier_conflicts(
    client, user_factory, unique_str
):
    existing = await user_factory(
        client,
        username=unique_str("UniqueUser"),
        display_name=unique_str("Unique Display"),
    )
    email_response = await client.post(
        "/auth/register",
        json={
            "email": existing["email"].upper(),
            "username": unique_str("another_user"),
            "display_name": unique_str("Another Display"),
            "password": "SuperSecret123",
        },
    )
    assert email_response.status_code == 409
    assert email_response.json()["detail"] == "Email already in use"

    payload = {
        "email": f"{unique_str('email')}@example.com",
        "username": f"@{existing['username'].swapcase()}",
        "display_name": unique_str("Another Display"),
        "password": "SuperSecret123",
    }
    response = await client.post("/auth/register", json=payload)
    assert response.status_code == 409
    assert response.json()["detail"] == "Username already in use"

    login = await client.post(
        "/auth/login",
        json={"email": existing["email"].upper(), "password": existing["password"]},
    )
    assert login.status_code == 200


async def test_registration_stores_canonical_username(client, unique_str):
    email = f"{unique_str('canonical')}@example.com"
    response = await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": "  @Movie_Night_Host  ",
            "display_name": "Movie Night Host",
            "password": "SuperSecret123",
        },
    )
    assert response.status_code == 201

    login = await client.post(
        "/auth/login",
        json={"email": email, "password": "SuperSecret123"},
    )
    assert login.status_code == 200
    assert (await client.get("/me")).json()["username"] == "movie_night_host"


async def test_registration_rejects_noncanonical_username_characters(
    client, unique_str
):
    response = await client.post(
        "/auth/register",
        json={
            "email": f"{unique_str('invalid')}@example.com",
            "username": "Movie Night Host",
            "display_name": "Movie Night Host",
            "password": "SuperSecret123",
        },
    )
    assert response.status_code == 422

async def test_display_names_can_be_shared_between_accounts(
    client, user_factory, login_helper, unique_str
):
    shared_name = unique_str("Shared Name")
    current = await user_factory(client, display_name=unique_str("Current Name"))
    other = await user_factory(client, display_name=shared_name)
    await login_helper(client, email=current["email"], password=current["password"])

    response = await client.patch(
        "/me", json={"display_name": other["display_name"]}
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == shared_name


async def test_oauth_login_preserves_custom_display_name(
    client,
    db_session,
    user_factory,
):
    from app.api.routes.auth import _upsert_oauth_user

    user_data = await user_factory(client, display_name="Chosen Name")

    user = await _upsert_oauth_user(
        db_session,
        email=user_data["email"],
        display_name="Provider Name",
        avatar_url="https://example.com/provider-avatar.png",
    )

    assert user.display_name == "Chosen Name"
    assert user.avatar_url == "https://example.com/provider-avatar.png"


async def test_social_oauth_endpoints_require_provider_config(client):
    google = await client.get("/auth/google/login")
    facebook = await client.get("/auth/facebook/login")

    assert google.status_code == 503
    assert facebook.status_code == 404


async def test_logout_revokes_auth_cookie(client, user_factory, login_helper):
    user = await user_factory(client, display_name="A")
    copied_token = await login_helper(client, email=user["email"], password=user["password"])

    me_before = await client.get("/me")
    assert me_before.status_code == 200

    logout = await client.post("/auth/logout")
    assert logout.status_code == 200
    assert logout.json() == {"ok": True}

    me_after = await client.get("/me")
    assert me_after.status_code in (401, 403)

    client.cookies.set("access_token", copied_token)
    replay = await client.get("/me")
    assert replay.status_code == 401


async def test_login_persists_typed_jti_session(
    client, db_session, user_factory, login_helper
):
    user = await user_factory(client)
    token = await login_helper(client, email=user["email"], password=user["password"])
    claims = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        options={"require": ["sub", "jti", "type", "iat", "exp"]},
    )

    assert claims["type"] == "access"
    assert claims["jti"]
    session = (
        await db_session.execute(
            select(AuthSession).where(AuthSession.jti == claims["jti"])
        )
    ).scalar_one()
    assert str(session.user_id) == user["id"]
    assert session.revoked_at is None


async def test_access_token_requires_persisted_session(client, user_factory):
    from app.core.security import create_access_token

    user = await user_factory(client)
    token, _expires_at = create_access_token(subject=user["id"], jti="missing-session")
    client.cookies.set("access_token", token)

    assert (await client.get("/me")).status_code == 401


async def test_logout_clears_oauth_session_cookie(client):
    client.cookies.set("session", "oauth-state-cookie", domain="test", path="/")
    assert client.cookies.get("session")

    logout = await client.post("/auth/logout")
    assert logout.status_code == 200
    assert logout.json() == {"ok": True}
    session_clear_headers = [
        value
        for value in logout.headers.get_list("set-cookie")
        if value.startswith("session=")
    ]
    assert session_clear_headers
    assert "Max-Age=0" in session_clear_headers[0]


async def test_google_callback_fetches_missing_avatar_from_userinfo(
    client, db_session, monkeypatch
):
    from app.api.routes import auth as auth_routes

    class _FakeProfileResponse:
        is_success = True

        @staticmethod
        def json():
            return {
                "email": "google-avatar@example.com",
                "sub": "google-avatar-subject",
                "email_verified": True,
                "name": "Google Avatar",
                "picture": "https://example.com/google-avatar.png",
            }

    class _FakeGoogleClient:
        async def authorize_access_token(self, request):
            _ = request
            return {
                "userinfo": {
                    "email": "google-avatar@example.com",
                    "sub": "google-avatar-subject",
                    "email_verified": True,
                    "name": "Google Avatar",
                }
            }

        async def parse_id_token(self, request, token):
            _ = (request, token)
            return None

        async def get(self, path, token=None):
            _ = token
            assert path == "userinfo"
            return _FakeProfileResponse()

    monkeypatch.setattr(
        auth_routes,
        "get_oauth_client",
        lambda provider: _FakeGoogleClient() if provider == "google" else None,
    )

    callback = await client.get("/auth/google/callback", follow_redirects=False)
    assert callback.status_code == 302

    me = await client.get("/me")
    assert me.status_code == 200, me.text
    assert me.json()["avatar_url"] == "https://example.com/google-avatar.png"
    identity = (
        await db_session.execute(
            select(OAuthIdentity).where(
                OAuthIdentity.provider == "google",
                OAuthIdentity.provider_subject == "google-avatar-subject",
            )
        )
    ).scalar_one()
    assert str(identity.user_id) == me.json()["id"]


async def test_google_callback_requires_subject_and_verified_email(client, monkeypatch):
    from app.api.routes import auth as auth_routes

    class _FakeGoogleClient:
        def __init__(self, claims):
            self.claims = claims

        async def authorize_access_token(self, request):
            _ = request
            return {"userinfo": self.claims}

        async def parse_id_token(self, request, token):
            _ = (request, token)
            return self.claims

        async def get(self, path, token=None):
            raise AssertionError(f"unexpected profile fetch: {path}")

    for claims, expected_reason in (
        (
            {
                "email": "missing-sub@example.com",
                "email_verified": True,
                "name": "Missing Sub",
                "picture": "https://example.com/a.png",
            },
            "google_subject_required",
        ),
        (
            {
                "email": "unverified@example.com",
                "sub": "unverified-subject",
                "email_verified": False,
                "name": "Unverified",
                "picture": "https://example.com/b.png",
            },
            "google_email_unverified",
        ),
    ):
        monkeypatch.setattr(
            auth_routes, "get_oauth_client", lambda provider, c=claims: _FakeGoogleClient(c)
        )
        callback = await client.get("/auth/google/callback", follow_redirects=False)
        assert callback.status_code == 302
        assert expected_reason in callback.headers["location"]


async def test_google_does_not_silently_link_existing_email(
    client, db_session, user_factory, monkeypatch
):
    from app.api.routes import auth as auth_routes

    existing = await user_factory(client, email="owned-email@example.com")
    claims = {
        "email": existing["email"],
        "sub": "new-google-subject",
        "email_verified": True,
        "name": "Provider Name",
        "picture": "https://example.com/provider.png",
    }

    class _FakeGoogleClient:
        async def authorize_access_token(self, request):
            _ = request
            return {"userinfo": claims}

        async def parse_id_token(self, request, token):
            _ = (request, token)
            return claims

    monkeypatch.setattr(auth_routes, "get_oauth_client", lambda provider: _FakeGoogleClient())

    callback = await client.get("/auth/google/callback", follow_redirects=False)
    assert callback.status_code == 302
    assert "google_identity_conflict" in callback.headers["location"]
    identity = (
        await db_session.execute(
            select(OAuthIdentity).where(
                OAuthIdentity.provider_subject == "new-google-subject"
            )
        )
    ).scalar_one_or_none()
    assert identity is None


async def test_magic_link_request_sends_email_when_configured(client, monkeypatch):
    from app.api.routes import auth as auth_routes

    sent_payload: dict[str, str] = {}

    async def _fake_send_magic_link_email(*, to_email: str, magic_link_url: str):
        sent_payload["to_email"] = to_email
        sent_payload["magic_link_url"] = magic_link_url

    async def _allow_rate_limit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_routes.settings, "resend_api_key", "test-key")
    monkeypatch.setattr(auth_routes.settings, "resend_from_email", "Arbiter <no-reply@example.com>")
    monkeypatch.setattr(auth_routes.settings, "env", "production")
    monkeypatch.setattr(
        auth_routes.settings,
        "magic_link_verify_url",
        "https://www.arbitertv.com/auth/magic-link/verify",
    )
    monkeypatch.setattr(auth_routes, "send_magic_link_email", _fake_send_magic_link_email)
    monkeypatch.setattr(auth_routes, "enforce_auth_rate_limit", _allow_rate_limit)

    response = await client.post(
        "/auth/magic-link/request",
        json={"email": "magic@example.com"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    assert sent_payload["to_email"] == "magic@example.com"
    parsed = urlsplit(sent_payload["magic_link_url"])
    assert parsed.query == ""
    assert parsed.fragment.startswith("grant=")
    assert client.cookies.get("magic_link_intent")


async def test_magic_link_verify_creates_user_and_authenticates(
    client, monkeypatch
):
    from app.api.routes import auth as auth_routes

    sent: dict[str, str] = {}

    async def _send(*, to_email: str, magic_link_url: str):
        sent["email"] = to_email
        sent["url"] = magic_link_url

    monkeypatch.setattr(auth_routes.settings, "resend_api_key", "test-key")
    monkeypatch.setattr(auth_routes.settings, "resend_from_email", "sender@example.com")
    monkeypatch.setattr(auth_routes, "send_magic_link_email", _send)

    requested = await client.post(
        "/auth/magic-link/request", json={"email": "new.magic@example.com"}
    )
    assert requested.status_code == 200
    grant = parse_qs(urlsplit(sent["url"]).fragment)["grant"][0]

    response = await client.post(
        "/auth/magic-link/verify",
        json={"grant": grant},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    me = await client.get("/me")
    assert me.status_code == 200, me.text
    data = me.json()
    assert data["email"] == "new.magic@example.com"
    assert data["username"]


async def test_magic_link_verify_rejects_invalid_grant(client):
    client.cookies.set(
        "magic_link_intent", "x" * 43, path="/auth/magic-link/verify"
    )
    response = await client.post(
        "/auth/magic-link/verify",
        json={"grant": "y" * 43},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "magic_link_invalid"}


async def test_magic_link_verify_only_accepts_strict_post_json(client):
    query_attempt = await client.get(
        "/auth/magic-link/verify?grant=" + "x" * 43,
        follow_redirects=False,
    )
    assert query_attempt.status_code == 405

    extra_field = await client.post(
        "/auth/magic-link/verify",
        json={"grant": "x" * 43, "intent": "must-come-from-cookie"},
        follow_redirects=False,
    )
    assert extra_field.status_code == 422

    coerced = await client.post(
        "/auth/magic-link/verify",
        json={"grant": 12345678901234567890123456789012},
        follow_redirects=False,
    )
    assert coerced.status_code == 422


async def test_magic_link_is_intent_bound_hashed_and_one_time(
    client, client_factory, db_session, monkeypatch
):
    from app.api.routes import auth as auth_routes

    sent: dict[str, str] = {}

    async def _send(*, to_email: str, magic_link_url: str):
        sent["url"] = magic_link_url

    monkeypatch.setattr(auth_routes.settings, "resend_api_key", "test-key")
    monkeypatch.setattr(auth_routes.settings, "resend_from_email", "sender@example.com")
    monkeypatch.setattr(auth_routes, "send_magic_link_email", _send)

    requested = await client.post(
        "/auth/magic-link/request", json={"email": "bound.magic@example.com"}
    )
    assert requested.status_code == 200
    grant = parse_qs(urlsplit(sent["url"]).fragment)["grant"][0]
    intent = client.cookies.get("magic_link_intent")
    assert intent

    stored = (
        await db_session.execute(
            select(MagicLinkGrant).where(MagicLinkGrant.email == "bound.magic@example.com")
        )
    ).scalar_one()
    assert stored.grant_hash != grant
    assert stored.intent_hash != intent
    assert len(stored.grant_hash) == len(stored.intent_hash) == 64

    async with client_factory() as other_browser:
        mismatch = await other_browser.post(
            "/auth/magic-link/verify",
            json={"grant": grant},
            follow_redirects=False,
        )
    assert mismatch.status_code == 400
    assert mismatch.json() == {"detail": "magic_link_intent_required"}

    accepted = await client.post(
        "/auth/magic-link/verify",
        json={"grant": grant},
        follow_redirects=False,
    )
    assert accepted.status_code == 200

    client.cookies.set(
        "magic_link_intent", intent, path="/auth/magic-link/verify"
    )
    replay = await client.post(
        "/auth/magic-link/verify",
        json={"grant": grant},
        follow_redirects=False,
    )
    assert replay.status_code == 400
    assert replay.json() == {"detail": "magic_link_invalid"}


async def test_local_auth_bypass_requires_configuration(client, monkeypatch):
    from app.api.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes.settings, "env", "test")
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_token", None)
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_email", None)
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_secondary_token", None)
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_secondary_email", None)

    response = await client.post("/auth/local-bypass", json={"token": "test-token"})

    assert response.status_code == 503


async def test_local_auth_bypass_rejects_invalid_token(client, monkeypatch):
    from app.api.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes.settings, "env", "test")
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_token", "expected-token")
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_email", "local@example.com")

    response = await client.post("/auth/local-bypass", json={"token": "wrong-token"})

    assert response.status_code == 401


async def test_local_auth_bypass_is_hidden_outside_local_env(client, monkeypatch):
    from app.api.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes.settings, "env", "production")
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_token", "expected-token")
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_email", "local@example.com")

    response = await client.post(
        "/auth/local-bypass",
        json={"token": "expected-token"},
        headers={"Origin": "http://localhost:5173"},
    )

    assert response.status_code == 404


async def test_local_auth_bypass_creates_user_and_authenticates(
    client, db_session, monkeypatch
):
    from app.api.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes.settings, "env", "test")
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_token", "expected-token")
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_email", "local@example.com")
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_display_name", "Local Tester")
    monkeypatch.setattr(
        auth_routes.settings,
        "local_auth_bypass_avatar_url",
        "https://example.com/local.png",
    )

    response = await client.post("/auth/local-bypass", json={"token": "expected-token"})
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}

    me = await client.get("/me")
    assert me.status_code == 200, me.text
    data = me.json()
    assert data["email"] == "local@example.com"
    assert data["display_name"] == "Local Tester"
    assert data["avatar_url"] == "https://example.com/local.png"
    identities = (
        await db_session.execute(
            select(OAuthIdentity).where(OAuthIdentity.user_id == data["id"])
        )
    ).scalars().all()
    assert identities == []


async def test_local_auth_bypass_supports_secondary_user(client, client_factory, monkeypatch):
    from app.api.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes.settings, "env", "test")
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_token", "primary-token")
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_email", "primary@example.com")
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_display_name", "Primary Tester")
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_avatar_url", None)
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_secondary_token", "secondary-token")
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_secondary_email", "secondary@example.com")
    monkeypatch.setattr(
        auth_routes.settings,
        "local_auth_bypass_secondary_display_name",
        "Secondary Tester",
    )
    monkeypatch.setattr(auth_routes.settings, "local_auth_bypass_secondary_avatar_url", None)

    primary_response = await client.post(
        "/auth/local-bypass",
        json={"token": "primary-token"},
    )
    assert primary_response.status_code == 200, primary_response.text
    primary_me = await client.get("/me")
    assert primary_me.status_code == 200, primary_me.text
    assert primary_me.json()["email"] == "primary@example.com"

    async with client_factory() as secondary_client:
        secondary_response = await secondary_client.post(
            "/auth/local-bypass",
            json={"token": "secondary-token"},
        )
        assert secondary_response.status_code == 200, secondary_response.text
        secondary_me = await secondary_client.get("/me")
        assert secondary_me.status_code == 200, secondary_me.text
        secondary_data = secondary_me.json()
        assert secondary_data["email"] == "secondary@example.com"
        assert secondary_data["display_name"] == "Secondary Tester"
        assert secondary_data["id"] != primary_me.json()["id"]
