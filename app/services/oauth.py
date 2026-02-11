from __future__ import annotations

from typing import Any

from app.core.config import settings

try:
    from authlib.integrations.base_client.errors import OAuthError as AuthlibOAuthError
    from authlib.integrations.starlette_client import OAuth
except ModuleNotFoundError:  # pragma: no cover - exercised only when dependency is missing
    OAuth = None

    class AuthlibOAuthError(Exception):
        pass


oauth_error_cls = AuthlibOAuthError
_oauth = OAuth() if OAuth is not None else None
_registered = False


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _is_configured(client_id: str | None, client_secret: str | None) -> bool:
    return bool(_clean(client_id) and _clean(client_secret))


def _ensure_clients_registered() -> None:
    global _registered

    if _registered or _oauth is None:
        _registered = True
        return

    if _is_configured(settings.oauth_google_client_id, settings.oauth_google_client_secret):
        _oauth.register(
            name="google",
            client_id=_clean(settings.oauth_google_client_id),
            client_secret=_clean(settings.oauth_google_client_secret),
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )

    if _is_configured(settings.oauth_facebook_client_id, settings.oauth_facebook_client_secret):
        _oauth.register(
            name="facebook",
            client_id=_clean(settings.oauth_facebook_client_id),
            client_secret=_clean(settings.oauth_facebook_client_secret),
            access_token_url="https://graph.facebook.com/v20.0/oauth/access_token",
            authorize_url="https://www.facebook.com/v20.0/dialog/oauth",
            api_base_url="https://graph.facebook.com/v20.0/",
            client_kwargs={"scope": "email public_profile"},
        )

    _registered = True


def get_oauth_client(provider: str) -> Any | None:
    _ensure_clients_registered()
    if _oauth is None:
        return None
    return _oauth.create_client(provider)
