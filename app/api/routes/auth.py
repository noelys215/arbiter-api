from __future__ import annotations

import re
import secrets
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db_session
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    RegisterRequest,
    RegisterResponse,
)
from app.services.oauth import get_oauth_client, oauth_error_cls

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "access_token"
OAUTH_SESSION_COOKIE_NAME = "session"
_USERNAME_MAX_LEN = 50
_USERNAME_SAFE_RE = re.compile(r"[^a-z0-9_]+")


def _auth_cookie_options() -> dict[str, object]:
    return {
        "httponly": True,
        "secure": settings.env not in {"local", "test"},
        "samesite": "lax",
        "path": "/",
    }


def _set_auth_cookie(response: Response, user_id: str) -> None:
    token = create_access_token(subject=user_id)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=settings.access_token_expire_minutes * 60,
        **_auth_cookie_options(),
    )


def _clear_auth_cookie(response: Response) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value="",
        max_age=0,
        expires=0,
        **_auth_cookie_options(),
    )


def _clear_oauth_session_cookie(response: Response) -> None:
    response.set_cookie(
        key=OAUTH_SESSION_COOKIE_NAME,
        value="",
        max_age=0,
        expires=0,
        **_auth_cookie_options(),
    )


def _oauth_failure_redirect(reason: str) -> RedirectResponse:
    separator = "&" if "?" in settings.oauth_frontend_failure_url else "?"
    destination = f"{settings.oauth_frontend_failure_url}{separator}oauth_error={quote_plus(reason)}"
    return RedirectResponse(url=destination, status_code=status.HTTP_302_FOUND)


def _require_oauth_client(provider: str):
    client = get_oauth_client(provider)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{provider.capitalize()} OAuth is not configured",
        )
    return client


def _require_oauth_session(request: Request) -> None:
    if "session" not in request.scope:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OAuth session support unavailable (install itsdangerous)",
        )


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _clean_email(value: object) -> str | None:
    normalized = _clean_text(value)
    if not normalized:
        return None
    return normalized.lower()


async def _username_exists(db: AsyncSession, username: str) -> bool:
    result = await db.execute(select(User.id).where(User.username == username))
    return result.scalar_one_or_none() is not None


async def _generate_unique_username(db: AsyncSession, seed: str) -> str:
    base = _USERNAME_SAFE_RE.sub("_", seed.lower()).strip("_")
    if not base:
        base = "user"
    base = base[:_USERNAME_MAX_LEN]

    if not await _username_exists(db, base):
        return base

    prefix_len = _USERNAME_MAX_LEN - 9  # underscore + 8-char suffix
    prefix = base[:prefix_len].rstrip("_") or "user"
    for _ in range(30):
        suffix = secrets.token_hex(4)
        candidate = f"{prefix}_{suffix}"
        if not await _username_exists(db, candidate):
            return candidate

    raise HTTPException(status_code=500, detail="Unable to generate username")


async def _upsert_oauth_user(
    db: AsyncSession,
    *,
    email: str,
    display_name: str,
    avatar_url: str | None,
) -> User:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is not None:
        changed = False
        if display_name and user.display_name != display_name:
            user.display_name = display_name
            changed = True
        if avatar_url and user.avatar_url != avatar_url:
            user.avatar_url = avatar_url
            changed = True
        if changed:
            await db.commit()
            await db.refresh(user)
        return user

    username_seed = email.split("@", 1)[0]
    username = await _generate_unique_username(db, username_seed)
    social_password = secrets.token_urlsafe(32)
    user = User(
        email=email,
        username=username,
        display_name=display_name,
        avatar_url=avatar_url,
        password_hash=hash_password(social_password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        select(User).where((User.email == payload.email) | (User.username == payload.username))
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Email or username already in use")

    user = User(
        email=payload.email,
        username=payload.username,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return RegisterResponse(id=str(user.id))


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, response: Response, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    _set_auth_cookie(response, str(user.id))
    return LoginResponse(ok=True)


@router.get("/google/login")
async def google_login(request: Request):
    client = _require_oauth_client("google")
    _require_oauth_session(request)
    return await client.authorize_redirect(request, settings.oauth_google_callback_url)


@router.get("/google/callback")
async def google_callback(request: Request, db: AsyncSession = Depends(get_db_session)):
    client = _require_oauth_client("google")
    _require_oauth_session(request)

    try:
        token = await client.authorize_access_token(request)
        userinfo = token.get("userinfo")
        if not isinstance(userinfo, dict):
            userinfo = {}

        if not userinfo:
            try:
                parsed = await client.parse_id_token(request, token)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                userinfo = parsed

        if not userinfo:
            profile_response = await client.get("userinfo", token=token)
            if profile_response.is_success:
                profile_data = profile_response.json()
                if isinstance(profile_data, dict):
                    userinfo = profile_data
    except oauth_error_cls:
        return _oauth_failure_redirect("google_oauth_failed")
    except Exception:
        return _oauth_failure_redirect("google_oauth_failed")

    email = _clean_email(userinfo.get("email"))
    if not email:
        return _oauth_failure_redirect("google_email_required")

    display_name = _clean_text(userinfo.get("name")) or email.split("@", 1)[0]
    avatar_url = _clean_text(userinfo.get("picture"))

    user = await _upsert_oauth_user(
        db,
        email=email,
        display_name=display_name,
        avatar_url=avatar_url,
    )

    response = RedirectResponse(url=settings.oauth_frontend_success_url, status_code=status.HTTP_302_FOUND)
    _set_auth_cookie(response, str(user.id))
    return response


@router.get("/facebook/login")
async def facebook_login(request: Request):
    client = _require_oauth_client("facebook")
    _require_oauth_session(request)
    return await client.authorize_redirect(request, settings.oauth_facebook_callback_url)


@router.get("/facebook/callback")
async def facebook_callback(request: Request, db: AsyncSession = Depends(get_db_session)):
    client = _require_oauth_client("facebook")
    _require_oauth_session(request)

    try:
        token = await client.authorize_access_token(request)
        profile_response = await client.get(
            "me",
            token=token,
            params={"fields": "id,name,email,picture.type(large)"},
        )
        if not profile_response.is_success:
            return _oauth_failure_redirect("facebook_profile_failed")
        profile = profile_response.json()
        if not isinstance(profile, dict):
            return _oauth_failure_redirect("facebook_profile_failed")
    except oauth_error_cls:
        return _oauth_failure_redirect("facebook_oauth_failed")
    except Exception:
        return _oauth_failure_redirect("facebook_oauth_failed")

    email = _clean_email(profile.get("email"))
    if not email:
        return _oauth_failure_redirect("facebook_email_required")

    display_name = _clean_text(profile.get("name")) or email.split("@", 1)[0]
    picture = profile.get("picture")
    avatar_url = None
    if isinstance(picture, dict):
        picture_data = picture.get("data")
        if isinstance(picture_data, dict):
            avatar_url = _clean_text(picture_data.get("url"))

    user = await _upsert_oauth_user(
        db,
        email=email,
        display_name=display_name,
        avatar_url=avatar_url,
    )

    response = RedirectResponse(url=settings.oauth_frontend_success_url, status_code=status.HTTP_302_FOUND)
    _set_auth_cookie(response, str(user.id))
    return response


@router.post("/logout", response_model=LogoutResponse)
async def logout(request: Request, response: Response):
    if "session" in request.scope:
        request.session.clear()
    _clear_auth_cookie(response)
    _clear_oauth_session_cookie(response)
    return LogoutResponse(ok=True)
