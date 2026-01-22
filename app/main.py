import app.db.base  # noqa: F401
import app.models  # noqa: F401

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

from app.api.routes.sessions import router as sessions_router


app = FastAPI(title="Watch Picker API", version="0.1.0")

@app.exception_handler(Exception)
async def debug_exception_handler(request: Request, exc: Exception):
    return PlainTextResponse("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), status_code=500)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
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
app.include_router(sessions_router)
