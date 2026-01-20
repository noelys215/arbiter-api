import app.db.base  # noqa: F401

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.base_class import Base


engine = create_async_engine(
    settings.database_url,
    echo=(settings.env == "local"),
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
