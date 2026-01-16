from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password, create_access_token
from app.db.session import get_db_session
from app.models.user import User
from app.schemas.auth import (
    RegisterRequest,
    RegisterResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "access_token"



@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db_session)):
    # email OR username already exists?

    
    print("### payload.password repr:", repr(payload.password))
    print("### payload.password bytes:", len(payload.password.encode("utf-8")))

    print("PASSWORD TYPE:", type(payload.password))
    print("PASSWORD LEN:", len(payload.password))
    print("PASSWORD REPR:", repr(payload.password)[:120])

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

    token = create_access_token(subject=str(user.id))

    # Dev cookies: secure=False (must be True on HTTPS in prod)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
        path="/",
    )
    return LoginResponse(ok=True)


@router.post("/logout", response_model=LogoutResponse)
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return LogoutResponse(ok=True)
