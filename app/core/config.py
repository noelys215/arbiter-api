from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = Field(default="local", alias="ENV")
    database_url: str = Field(alias="DATABASE_URL")

    jwt_secret: str = Field(alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(default=60 * 24 * 30, alias="ACCESS_TOKEN_EXPIRE_MINUTES")

    cors_origins: str = Field(default="http://localhost:5173", alias="CORS_ORIGINS")

    tmdb_token: str = Field(alias="TMDB_TOKEN")

    # ─────────────────────────────────────────────
    # OpenAI (Phase 5.2+)
    # ─────────────────────────────────────────────
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5-mini", alias="OPENAI_MODEL")

    # ─────────────────────────────────────────────
    # OAuth (Authlib)
    # ─────────────────────────────────────────────
    oauth_google_client_id: str | None = Field(default=None, alias="OAUTH_GOOGLE_CLIENT_ID")
    oauth_google_client_secret: str | None = Field(default=None, alias="OAUTH_GOOGLE_CLIENT_SECRET")
    oauth_facebook_client_id: str | None = Field(default=None, alias="OAUTH_FACEBOOK_CLIENT_ID")
    oauth_facebook_client_secret: str | None = Field(default=None, alias="OAUTH_FACEBOOK_CLIENT_SECRET")
    oauth_google_callback_url: str = Field(default="http://localhost:8000/auth/google/callback", alias="OAUTH_GOOGLE_CALLBACK_URL")
    oauth_facebook_callback_url: str = Field(default="http://localhost:8000/auth/facebook/callback", alias="OAUTH_FACEBOOK_CALLBACK_URL")
    oauth_frontend_success_url: str = Field(default="http://localhost:5173/app", alias="OAUTH_FRONTEND_SUCCESS_URL")
    oauth_frontend_failure_url: str = Field(default="http://localhost:5173/login", alias="OAUTH_FRONTEND_FAILURE_URL")
    oauth_session_secret: str | None = Field(default=None, alias="OAUTH_SESSION_SECRET")

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

settings = Settings()
