import pytest

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


async def test_social_oauth_endpoints_require_provider_config(client):
    google = await client.get("/auth/google/login")
    facebook = await client.get("/auth/facebook/login")

    assert google.status_code == 503
    assert facebook.status_code == 503


async def test_logout_revokes_auth_cookie(client, user_factory, login_helper):
    user = await user_factory(client, display_name="A")
    await login_helper(client, email=user["email"], password=user["password"])

    me_before = await client.get("/me")
    assert me_before.status_code == 200

    logout = await client.post("/auth/logout")
    assert logout.status_code == 200
    assert logout.json() == {"ok": True}

    me_after = await client.get("/me")
    assert me_after.status_code in (401, 403)


async def test_logout_clears_oauth_session_cookie(client):
    client.cookies.set("session", "oauth-state-cookie")
    assert client.cookies.get("session")

    logout = await client.post("/auth/logout")
    assert logout.status_code == 200
    assert logout.json() == {"ok": True}
    assert client.cookies.get("session") is None


async def test_google_callback_fetches_missing_avatar_from_userinfo(client, monkeypatch):
    from app.api.routes import auth as auth_routes

    class _FakeProfileResponse:
        is_success = True

        @staticmethod
        def json():
            return {
                "email": "google-avatar@example.com",
                "name": "Google Avatar",
                "picture": "https://example.com/google-avatar.png",
            }

    class _FakeGoogleClient:
        async def authorize_access_token(self, request):
            _ = request
            return {"userinfo": {"email": "google-avatar@example.com", "name": "Google Avatar"}}

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
