import os
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

# IMPORTANT:
# Set env vars BEFORE importing app.settings/app.main (pydantic settings often load at import time)
os.environ["ENV"] = "test"
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://watchpicker:watchpicker@localhost:5432/watchpicker_test",
)
os.environ.setdefault("JWT_SECRET", "dev-test-secret")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:5173")

from app.main import app as fastapi_app  # noqa: E402
from app.db.base_class import Base  # noqa: E402
import app.db.base  # noqa: F401,E402  (register models)
from app.db.session import engine, AsyncSessionLocal, get_db_session  # noqa: E402

# If you have a dependency function like get_db in app.api.deps, we can override it.
# We'll do it safely with a try/except so tests still work even if you rename it later.
try:
    from app.api.deps import get_db  # type: ignore
except Exception:
    get_db = None  # type: ignore


@pytest.fixture(scope="session", autouse=True)
async def _create_test_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session):
    """
    Overrides app.db.session.get_db_session so both:
    - Depends(get_db_session)
    - anything wrapping get_db_session
    will use the same test session.
    """

    async def _override_get_db_session():
        yield db_session

    fastapi_app.dependency_overrides[get_db_session] = _override_get_db_session

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    fastapi_app.dependency_overrides.pop(get_db_session, None)

# --- Small helpers for your spine tests ---

async def register_user(client: AsyncClient, *, email: str, username: str, display_name: str, password: str):
    return await client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "display_name": display_name,
            "password": password,
        },
    )


async def login_user(client: AsyncClient, *, email: str, password: str):
    return await client.post("/auth/login", json={"email": email, "password": password})
