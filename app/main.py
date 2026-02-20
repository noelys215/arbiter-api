import app.db.base  # noqa: F401
import app.models  # noqa: F401

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP
try:
    from starlette.middleware.sessions import SessionMiddleware
except ModuleNotFoundError:  # pragma: no cover - depends on optional dependency
    SessionMiddleware = None

from app.core.config import settings
from app.api.routes.health import router as health_router
from app.api.routes.auth import router as auth_router
from app.api.routes.me import router as me_router

from fastapi import Request
from fastapi.responses import PlainTextResponse
import traceback
from app.api.routes.friends import router as friends_router

from app.api.routes.groups import router as groups_router

from app.api.routes.tmdb import router as tmdb_router
from app.api.routes.watchlist import router as watchlist_router

from app.api.routes.sessions import router as sessions_router


logger = logging.getLogger(__name__)
app = FastAPI(title="Watch Picker API", version="0.1.0")

local_cors_origin_regex = (
    r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    if settings.env in {"local", "test"}
    else None
)

@app.exception_handler(Exception)
async def debug_exception_handler(request: Request, exc: Exception):
    if settings.env in {"local", "test"}:
        return PlainTextResponse(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            status_code=500,
        )
    logger.exception("Unhandled exception for %s %s", request.method, request.url.path)
    return PlainTextResponse("Internal Server Error", status_code=500)

if SessionMiddleware is not None:
    app.add_middleware(
        SessionMiddleware,
        secret_key=(settings.oauth_session_secret or settings.jwt_secret),
        same_site=settings.auth_cookie_samesite,
        https_only=settings.auth_cookie_secure_value(),
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_origin_regex=local_cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(friends_router)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(me_router)
app.include_router(groups_router)
app.include_router(tmdb_router)
app.include_router(watchlist_router)
app.include_router(sessions_router)

mcp = FastApiMCP(app)
mcp.mount_http()
