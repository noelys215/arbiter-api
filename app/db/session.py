import app.db.base  # noqa: F401

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings


def engine_options(env: str) -> dict[str, object]:
    options: dict[str, object] = {
        "echo": env == "local",
        "pool_pre_ping": True,
    }
    if env == "test":
        options["poolclass"] = NullPool
    elif env not in {"local", "test"}:
        options.update(
            {
                "connect_args": {
                    "ssl": "require",
                    "command_timeout": 30,
                    "server_settings": {"statement_timeout": "30000"},
                },
                "pool_size": 5,
                "max_overflow": 5,
                "pool_timeout": 10,
            }
        )
    return options


engine_kwargs = engine_options(settings.env)

engine = create_async_engine(
    settings.database_url,
    **engine_kwargs,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
