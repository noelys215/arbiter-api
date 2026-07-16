import json

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator, model_validator

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
    # Email / Magic Link
    # ─────────────────────────────────────────────
    resend_api_key: str | None = Field(default=None, alias="RESEND_API_KEY")
    resend_from_email: str | None = Field(default=None, alias="RESEND_FROM_EMAIL")
    feedback_recipient_email: str | None = Field(
        default=None, alias="FEEDBACK_RECIPIENT_EMAIL"
    )
    feedback_from_email: str | None = Field(default=None, alias="FEEDBACK_FROM_EMAIL")
    feedback_public_enabled: bool = Field(
        default=False, alias="FEEDBACK_PUBLIC_ENABLED"
    )
    feedback_authenticated_enabled: bool = Field(
        default=False, alias="FEEDBACK_AUTHENTICATED_ENABLED"
    )
    magic_link_verify_url: str = Field(
        default="https://www.arbitertv.com/auth/magic-link/verify",
        alias="MAGIC_LINK_VERIFY_URL",
    )
    magic_link_expire_minutes: int = Field(default=15, alias="MAGIC_LINK_EXPIRE_MINUTES")

    # ─────────────────────────────────────────────
    # OAuth (Authlib)
    # ─────────────────────────────────────────────
    oauth_google_client_id: str | None = Field(default=None, alias="OAUTH_GOOGLE_CLIENT_ID")
    oauth_google_client_secret: str | None = Field(default=None, alias="OAUTH_GOOGLE_CLIENT_SECRET")
    oauth_google_callback_url: str = Field(default="http://localhost:8000/auth/google/callback", alias="OAUTH_GOOGLE_CALLBACK_URL")
    oauth_frontend_success_url: str = Field(default="https://www.arbitertv.com/app", alias="OAUTH_FRONTEND_SUCCESS_URL")
    oauth_frontend_failure_url: str = Field(default="https://www.arbitertv.com/login", alias="OAUTH_FRONTEND_FAILURE_URL")
    oauth_session_secret: str | None = Field(default=None, alias="OAUTH_SESSION_SECRET")
    auth_cookie_samesite: str = Field(default="lax", alias="AUTH_COOKIE_SAMESITE")
    auth_cookie_secure: bool | None = Field(default=None, alias="AUTH_COOKIE_SECURE")
    auth_cookie_domain: str | None = Field(default=None, alias="AUTH_COOKIE_DOMAIN")
    local_auth_bypass_token: str | None = Field(default=None, alias="LOCAL_AUTH_BYPASS_TOKEN")
    local_auth_bypass_email: str | None = Field(default=None, alias="LOCAL_AUTH_BYPASS_EMAIL")
    local_auth_bypass_display_name: str = Field(
        default="Arbiter Test User",
        alias="LOCAL_AUTH_BYPASS_DISPLAY_NAME",
    )
    local_auth_bypass_avatar_url: str | None = Field(
        default=None,
        alias="LOCAL_AUTH_BYPASS_AVATAR_URL",
    )
    local_auth_bypass_secondary_token: str | None = Field(
        default=None,
        alias="LOCAL_AUTH_BYPASS_SECONDARY_TOKEN",
    )
    local_auth_bypass_secondary_email: str | None = Field(
        default=None,
        alias="LOCAL_AUTH_BYPASS_SECONDARY_EMAIL",
    )
    local_auth_bypass_secondary_display_name: str = Field(
        default="Arbiter Test User 2",
        alias="LOCAL_AUTH_BYPASS_SECONDARY_DISPLAY_NAME",
    )
    local_auth_bypass_secondary_avatar_url: str | None = Field(
        default=None,
        alias="LOCAL_AUTH_BYPASS_SECONDARY_AVATAR_URL",
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        cleaned = value.strip()
        if cleaned.startswith("postgres://"):
            cleaned = f"postgresql://{cleaned[len('postgres://'):]}"
        if cleaned.startswith("postgresql://") and not cleaned.startswith("postgresql+"):
            cleaned = cleaned.replace("postgresql://", "postgresql+asyncpg://", 1)
        return cleaned

    @field_validator("auth_cookie_samesite", mode="before")
    @classmethod
    def normalize_auth_cookie_samesite(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip().lower()

    @model_validator(mode="after")
    def validate_cookie_settings(self) -> "Settings":
        if self.auth_cookie_samesite_value() not in {"lax", "strict", "none"}:
            raise ValueError("AUTH_COOKIE_SAMESITE must be one of: lax, strict, none")
        if self.auth_cookie_samesite_value() == "none" and not self.auth_cookie_secure_value():
            raise ValueError("AUTH_COOKIE_SAMESITE=none requires AUTH_COOKIE_SECURE=true")
        return self

    def is_local_env(self) -> bool:
        return self.env in {"local", "test"}

    def feedback_public_enabled_value(self) -> bool:
        return self.is_local_env() or self.feedback_public_enabled

    def feedback_authenticated_enabled_value(self) -> bool:
        return self.is_local_env() or self.feedback_authenticated_enabled

    def oauth_google_callback_url_value(self) -> str:
        if self.is_local_env():
            return "http://localhost:8000/auth/google/callback"
        return self.oauth_google_callback_url

    def oauth_frontend_success_url_value(self) -> str:
        if self.is_local_env():
            return "http://localhost:5173/app"
        return self.oauth_frontend_success_url

    def oauth_frontend_failure_url_value(self) -> str:
        if self.is_local_env():
            return "http://localhost:5173/login"
        return self.oauth_frontend_failure_url

    def magic_link_verify_url_value(self) -> str:
        if self.is_local_env():
            return "http://localhost:8000/auth/magic-link/verify"
        return self.magic_link_verify_url

    def auth_cookie_samesite_value(self) -> str:
        if self.is_local_env():
            return "lax"
        return self.auth_cookie_samesite

    def auth_cookie_secure_value(self) -> bool:
        if self.is_local_env():
            return False
        if self.auth_cookie_secure is not None:
            return self.auth_cookie_secure
        return True

    def cors_origin_list(self) -> list[str]:
        raw = (self.cors_origins or "").strip()
        if not raw:
            return []

        values: list[str]
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                values = [str(v) for v in parsed if isinstance(v, str)]
            else:
                values = [raw]
        else:
            values = raw.split(",")

        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = value.strip().strip("\"'")
            if not cleaned:
                continue
            # CORS origins are scheme + host (+ optional port) with no path slash.
            if cleaned != "*" and cleaned.endswith("/"):
                cleaned = cleaned.rstrip("/")
            if cleaned in seen:
                continue
            seen.add(cleaned)
            normalized.append(cleaned)

        return normalized

settings = Settings()
