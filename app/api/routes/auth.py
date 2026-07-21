from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.api.auth_rate_limits import enforce_auth_rate_limit
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_auth_secret,
    hash_password,
    hash_auth_secret,
    verify_password,
)
from app.db.session import get_db_session
from app.models.auth_session import AuthSession
from app.models.magic_link_grant import MagicLinkGrant
from app.models.oauth_identity import OAuthIdentity
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LocalAuthBypassRequest,
    MagicLinkRequest,
    MagicLinkRequestResponse,
    MagicLinkVerifyRequest,
    LogoutResponse,
    RegisterRequest,
    RegisterResponse,
)
from app.services.magic_link_email import (
    build_magic_link,
    magic_link_email_configured,
    send_magic_link_email,
)
from app.services.oauth import get_oauth_client, oauth_error_cls
from app.services.account_realtime import account_realtime_hub
from app.services.session_realtime import session_realtime_hub
from app.services.watchlist_realtime import watchlist_realtime_hub
from app.services.users import (
    ensure_username_available,
    username_exists,
)

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "access_token"
OAUTH_SESSION_COOKIE_NAME = "session"
MAGIC_LINK_INTENT_COOKIE_NAME = "magic_link_intent"
_USERNAME_MAX_LEN = 50
_USERNAME_SAFE_RE = re.compile(r"[^a-z0-9_]+")
_DUMMY_PASSWORD_HASH = hash_password("arbiter-dummy-password-not-an-account")


def _auth_cookie_options() -> dict[str, object]:
    options: dict[str, object] = {
        "httponly": True,
        "secure": settings.auth_cookie_secure_value(),
        "samesite": settings.auth_cookie_samesite_value(),
        "path": "/",
    }
    domain = (settings.auth_cookie_domain or "").strip()
    if domain:
        options["domain"] = domain
    return options


async def _set_auth_cookie(
    response: Response,
    db: AsyncSession,
    user_id: uuid.UUID,
) -> None:
    jti = str(uuid.uuid4())
    token, expires_at = create_access_token(subject=str(user_id), jti=jti)
    db.add(AuthSession(user_id=user_id, jti=jti, expires_at=expires_at))
    await db.commit()
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


def _magic_link_intent_cookie_options() -> dict[str, object]:
    options = _auth_cookie_options()
    options["path"] = "/auth/magic-link/verify"
    return options


def _set_magic_link_intent_cookie(response: Response, intent: str) -> None:
    response.set_cookie(
        key=MAGIC_LINK_INTENT_COOKIE_NAME,
        value=intent,
        max_age=settings.magic_link_expire_minutes * 60,
        **_magic_link_intent_cookie_options(),
    )


def _clear_magic_link_intent_cookie(response: Response) -> None:
    response.set_cookie(
        key=MAGIC_LINK_INTENT_COOKIE_NAME,
        value="",
        max_age=0,
        expires=0,
        **_magic_link_intent_cookie_options(),
    )


def _oauth_failure_redirect(reason: str) -> RedirectResponse:
    failure_url = settings.oauth_frontend_failure_url_value()
    separator = "&" if "?" in failure_url else "?"
    destination = f"{failure_url}{separator}oauth_error={quote_plus(reason)}"
    return RedirectResponse(url=destination, status_code=status.HTTP_302_FOUND)


def _google_callback_url_for_request(request: Request) -> str:
    if settings.is_local_env():
        # Match whatever local host the browser used (localhost vs 127.0.0.1).
        return str(request.url_for("google_callback"))
    return settings.oauth_google_callback_url_value()


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


def _google_is_authoritative_for_email(
    email: str, userinfo: dict[str, object]
) -> bool:
    _, separator, domain = email.lower().rpartition("@")
    if not separator:
        return False
    if domain == "gmail.com":
        return True
    hosted_domain = _clean_text(userinfo.get("hd"))
    return (
        hosted_domain is not None
        and secrets.compare_digest(domain, hosted_domain.lower())
    )


def _merge_oauth_claims(base: dict[str, object], extra: dict[str, object]) -> dict[str, object]:
    merged = dict(base)
    for key, value in extra.items():
        existing = merged.get(key)
        if existing is None or existing == "":
            merged[key] = value
    return merged


def _extract_avatar_url(payload: dict[str, object]) -> str | None:
    direct = _clean_text(payload.get("picture"))
    if direct:
        return direct

    avatar_direct = _clean_text(payload.get("avatar_url"))
    if avatar_direct:
        return avatar_direct

    picture = payload.get("picture")
    if isinstance(picture, dict):
        picture_data = picture.get("data")
        if isinstance(picture_data, dict):
            nested = _clean_text(picture_data.get("url"))
            if nested:
                return nested
    return None


def _configured_local_bypass_accounts() -> list[dict[str, str | None]]:
    return [
        {
            "token": (settings.local_auth_bypass_token or "").strip(),
            "email": _clean_email(settings.local_auth_bypass_email),
            "display_name": _clean_text(settings.local_auth_bypass_display_name),
            "avatar_url": _clean_text(settings.local_auth_bypass_avatar_url),
        },
        {
            "token": (settings.local_auth_bypass_secondary_token or "").strip(),
            "email": _clean_email(settings.local_auth_bypass_secondary_email),
            "display_name": _clean_text(settings.local_auth_bypass_secondary_display_name),
            "avatar_url": _clean_text(settings.local_auth_bypass_secondary_avatar_url),
        },
    ]


def _default_display_name_from_email(email: str) -> str:
    local_part = email.split("@", 1)[0].strip()
    if not local_part:
        return "User"
    return local_part[:120]


async def _generate_unique_username(db: AsyncSession, seed: str) -> str:
    base = _USERNAME_SAFE_RE.sub("_", seed.lower()).strip("_")
    if not base:
        base = "user"
    base = base[:_USERNAME_MAX_LEN]

    if not await username_exists(db, base):
        return base

    prefix_len = _USERNAME_MAX_LEN - 9  # underscore + 8-char suffix
    prefix = base[:prefix_len].rstrip("_") or "user"
    for _ in range(30):
        suffix = secrets.token_hex(4)
        candidate = f"{prefix}_{suffix}"
        if not await username_exists(db, candidate):
            return candidate

    raise HTTPException(status_code=500, detail="Unable to generate username")


async def _upsert_oauth_user(
    db: AsyncSession,
    *,
    email: str,
    display_name: str,
    avatar_url: str | None,
) -> User:
    result = await db.execute(
        select(User).where(sa.func.lower(User.email) == email.lower())
    )
    user = result.scalar_one_or_none()

    if user is not None:
        changed = False
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


class _OAuthIdentityConflict(Exception):
    pass


async def _resolve_google_user(
    db: AsyncSession,
    *,
    subject: str,
    email: str,
    display_name: str,
    avatar_url: str | None,
    allow_authoritative_email_link: bool,
) -> User:
    identity_result = await db.execute(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == "google",
            OAuthIdentity.provider_subject == subject,
        )
    )
    identity = identity_result.scalar_one_or_none()
    if identity is not None:
        user_result = await db.execute(select(User).where(User.id == identity.user_id))
        user = user_result.scalar_one()
        changed = False
        if identity.provider_email != email:
            identity.provider_email = email
            changed = True
        if avatar_url and user.avatar_url != avatar_url:
            user.avatar_url = avatar_url
            changed = True
        if changed:
            await db.commit()
            await db.refresh(user)
        return user

    email_result = await db.execute(
        select(User).where(sa.func.lower(User.email) == email.lower())
    )
    existing_user = email_result.scalar_one_or_none()
    if existing_user is not None:
        if not allow_authoritative_email_link:
            raise _OAuthIdentityConflict
        existing_user_id = existing_user.id
        db.add(
            OAuthIdentity(
                user_id=existing_user_id,
                provider="google",
                provider_subject=subject,
                provider_email=email,
            )
        )
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            linked_identity = (
                await db.execute(
                    select(OAuthIdentity).where(
                        OAuthIdentity.provider == "google",
                        OAuthIdentity.provider_subject == subject,
                    )
                )
            ).scalar_one_or_none()
            if (
                linked_identity is None
                or linked_identity.user_id != existing_user_id
            ):
                raise _OAuthIdentityConflict from exc
            existing_user = (
                await db.execute(
                    select(User).where(User.id == linked_identity.user_id)
                )
            ).scalar_one()
        else:
            await db.refresh(existing_user)
        return existing_user

    username = await _generate_unique_username(db, email.split("@", 1)[0])
    user = User(
        email=email,
        username=username,
        display_name=display_name,
        avatar_url=avatar_url,
        password_hash=hash_password(secrets.token_urlsafe(32)),
    )
    db.add(user)
    await db.flush()
    db.add(
        OAuthIdentity(
            user_id=user.id,
            provider="google",
            provider_subject=subject,
            provider_email=email,
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _OAuthIdentityConflict from exc
    await db.refresh(user)
    return user


@router.post("/magic-link/request", response_model=MagicLinkRequestResponse)
async def request_magic_link(
    payload: MagicLinkRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
):
    if not magic_link_email_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Magic link email is not configured",
        )

    normalized_email = payload.email.strip().lower()
    await enforce_auth_rate_limit(
        request,
        action="magic_link",
        subject=normalized_email,
    )
    grant = generate_auth_secret()
    intent = generate_auth_secret()
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.magic_link_expire_minutes
    )
    db.add(
        MagicLinkGrant(
            email=normalized_email,
            grant_hash=hash_auth_secret(grant),
            intent_hash=hash_auth_secret(intent),
            expires_at=expires_at,
        )
    )
    await db.commit()

    magic_link_url = build_magic_link(grant)
    try:
        await send_magic_link_email(to_email=normalized_email, magic_link_url=magic_link_url)
    except Exception as exc:
        await db.execute(
            sa.delete(MagicLinkGrant).where(
                MagicLinkGrant.grant_hash == hash_auth_secret(grant)
            )
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to send sign-in email",
        ) from exc

    _set_magic_link_intent_cookie(response, intent)
    return MagicLinkRequestResponse(ok=True)


@router.post("/magic-link/verify")
async def verify_magic_link(
    payload: MagicLinkVerifyRequest,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
    intent: str | None = Cookie(default=None, alias=MAGIC_LINK_INTENT_COOKIE_NAME),
):
    if not intent:
        raise HTTPException(status_code=400, detail="magic_link_intent_required")

    consumed_at = datetime.now(timezone.utc)
    consumed = await db.execute(
        sa.update(MagicLinkGrant)
        .where(
            MagicLinkGrant.grant_hash == hash_auth_secret(payload.grant),
            MagicLinkGrant.intent_hash == hash_auth_secret(intent),
            MagicLinkGrant.consumed_at.is_(None),
            MagicLinkGrant.expires_at > consumed_at,
        )
        .values(consumed_at=consumed_at)
        .returning(MagicLinkGrant.email)
    )
    email = consumed.scalar_one_or_none()
    if email is None:
        await db.rollback()
        raise HTTPException(status_code=400, detail="magic_link_invalid")

    result = await db.execute(
        select(User).where(sa.func.lower(User.email) == email.lower())
    )
    user = result.scalar_one_or_none()
    if user is None:
        username = await _generate_unique_username(db, email.split("@", 1)[0])
        user = User(
            email=email,
            username=username,
            display_name=_default_display_name_from_email(email),
            password_hash=hash_password(secrets.token_urlsafe(32)),
        )
        db.add(user)
        await db.flush()

    await _set_auth_cookie(response, db, user.id)
    _clear_magic_link_intent_cookie(response)
    return LoginResponse(ok=True)


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(
    payload: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    await enforce_auth_rate_limit(request, action="register")
    normalized_email = str(payload.email).strip().lower()
    result = await db.execute(
        select(User).where(sa.func.lower(User.email) == normalized_email)
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Email already in use")
    try:
        await ensure_username_available(db, username=payload.username)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Username already in use") from exc

    user = User(
        email=normalized_email,
        username=payload.username,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Email or username already in use",
        ) from exc
    await db.refresh(user)

    return RegisterResponse(id=str(user.id))


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
):
    await enforce_auth_rate_limit(
        request,
        action="login",
        subject=str(payload.email),
    )
    result = await db.execute(
        select(User).where(sa.func.lower(User.email) == str(payload.email).lower())
    )
    user = result.scalar_one_or_none()
    password_matches = verify_password(
        payload.password,
        user.password_hash if user is not None else _DUMMY_PASSWORD_HASH,
    )
    if user is None or not password_matches:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    await _set_auth_cookie(response, db, user.id)
    return LoginResponse(ok=True)


@router.post("/local-bypass", response_model=LoginResponse)
async def local_auth_bypass(
    payload: LocalAuthBypassRequest,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
):
    if not settings.is_local_env():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    configured_accounts = [
        account
        for account in _configured_local_bypass_accounts()
        if account["token"] and account["email"]
    ]
    if not configured_accounts:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Local auth bypass is not configured",
        )

    matched_account = next(
        (
            account
            for account in configured_accounts
            if secrets.compare_digest(payload.token, str(account["token"]))
        ),
        None,
    )
    if matched_account is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid local auth bypass token",
        )

    email = str(matched_account["email"])
    display_name = str(matched_account["display_name"] or _default_display_name_from_email(email))
    avatar_url = matched_account["avatar_url"]
    user = await _upsert_oauth_user(
        db,
        email=email,
        display_name=display_name,
        avatar_url=avatar_url,
    )

    await _set_auth_cookie(response, db, user.id)
    return LoginResponse(ok=True)


@router.get("/google/login")
async def google_login(request: Request):
    await enforce_auth_rate_limit(request, action="oauth_start")
    client = _require_oauth_client("google")
    _require_oauth_session(request)
    return await client.authorize_redirect(request, _google_callback_url_for_request(request))


@router.get("/google/callback")
async def google_callback(request: Request, db: AsyncSession = Depends(get_db_session)):
    client = _require_oauth_client("google")
    _require_oauth_session(request)

    try:
        token = await client.authorize_access_token(request)
        userinfo: dict[str, object] = {}

        token_userinfo = token.get("userinfo")
        if isinstance(token_userinfo, dict):
            userinfo = _merge_oauth_claims(userinfo, token_userinfo)

        try:
            parsed = await client.parse_id_token(request, token)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            userinfo = _merge_oauth_claims(userinfo, parsed)

        needs_profile_fetch = (
            not _clean_email(userinfo.get("email"))
            or not _clean_text(userinfo.get("name"))
            or not _extract_avatar_url(userinfo)
        )
        if needs_profile_fetch:
            profile_response = await client.get("userinfo", token=token)
            if profile_response.is_success:
                profile_data = profile_response.json()
                if isinstance(profile_data, dict):
                    userinfo = _merge_oauth_claims(userinfo, profile_data)
    except oauth_error_cls:
        return _oauth_failure_redirect("google_oauth_failed")
    except Exception:
        return _oauth_failure_redirect("google_oauth_failed")

    email = _clean_email(userinfo.get("email"))
    if not email:
        return _oauth_failure_redirect("google_email_required")
    subject = _clean_text(userinfo.get("sub"))
    if not subject:
        return _oauth_failure_redirect("google_subject_required")
    if userinfo.get("email_verified") is not True:
        return _oauth_failure_redirect("google_email_unverified")

    display_name = _clean_text(userinfo.get("name")) or email.split("@", 1)[0]
    avatar_url = _extract_avatar_url(userinfo)

    try:
        user = await _resolve_google_user(
            db,
            subject=subject,
            email=email,
            display_name=display_name,
            avatar_url=avatar_url,
            allow_authoritative_email_link=_google_is_authoritative_for_email(
                email, userinfo
            ),
        )
    except _OAuthIdentityConflict:
        return _oauth_failure_redirect("google_identity_conflict")

    response = RedirectResponse(
        url=settings.oauth_frontend_success_url_value(),
        status_code=status.HTTP_302_FOUND,
    )
    await _set_auth_cookie(response, db, user.id)
    return response


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
):
    if "session" in request.scope:
        request.session.clear()
    access_token = request.cookies.get(COOKIE_NAME)
    claims = decode_access_token(access_token) if access_token else None
    if claims is not None:
        subject, jti = claims
        await db.execute(
            sa.update(AuthSession)
            .where(AuthSession.jti == jti, AuthSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await db.commit()
        try:
            user_id = uuid.UUID(subject)
        except ValueError:
            user_id = None
        if user_id is not None:
            await account_realtime_hub.disconnect_user(user_id)
            await watchlist_realtime_hub.disconnect_user_everywhere(user_id)
            await session_realtime_hub.disconnect_user_everywhere(user_id)
    _clear_auth_cookie(response)
    _clear_oauth_session_cookie(response)
    return LogoutResponse(ok=True)
