import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _settings_with_cors(value: str) -> Settings:
    return Settings(
        DATABASE_URL="sqlite+aiosqlite:///./test.db",
        JWT_SECRET="test-secret",
        TMDB_TOKEN="test-token",
        CORS_ORIGINS=value,
    )


def test_cors_origin_list_supports_comma_separated_values() -> None:
    settings = _settings_with_cors("http://localhost:5173,http://localhost:3000")
    assert settings.cors_origin_list() == [
        "http://localhost:5173",
        "http://localhost:3000",
    ]


def test_cors_origin_list_normalizes_quotes_and_trailing_slashes() -> None:
    settings = _settings_with_cors("'http://localhost:5173/'")
    assert settings.cors_origin_list() == ["http://localhost:5173"]


def test_cors_origin_list_supports_json_array_format() -> None:
    settings = _settings_with_cors(
        '["http://localhost:5173", "http://127.0.0.1:5173/"]'
    )
    assert settings.cors_origin_list() == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def test_production_requires_strong_secret_and_explicit_https_origins() -> None:
    common = {
        "ENV": "production",
        "DATABASE_URL": "postgresql://user:password@db.example/app",
        "TMDB_TOKEN": "test-token",
        "OAUTH_GOOGLE_CALLBACK_URL": "https://api.example/auth/google/callback",
        "OAUTH_FRONTEND_SUCCESS_URL": "https://www.arbitertv.com/app",
        "OAUTH_FRONTEND_FAILURE_URL": "https://www.arbitertv.com/login",
        "MAGIC_LINK_VERIFY_URL": "https://www.arbitertv.com/auth/magic-link/verify",
    }
    with pytest.raises(ValidationError):
        Settings(
            **common,
            JWT_SECRET="short",
            CORS_ORIGINS="https://www.arbitertv.com",
        )
    with pytest.raises(ValidationError):
        Settings(
            **common,
            JWT_SECRET="x" * 32,
            CORS_ORIGINS="*",
        )
    with pytest.raises(ValidationError):
        Settings(
            **common,
            JWT_SECRET="x" * 32,
            CORS_ORIGINS="http://www.arbitertv.com",
        )


def test_production_accepts_strong_explicit_https_configuration() -> None:
    configured = Settings(
        ENV="production",
        DATABASE_URL="postgresql://user:password@db.example/app",
        JWT_SECRET="x" * 32,
        TMDB_TOKEN="test-token",
        CORS_ORIGINS="https://www.arbitertv.com",
        OAUTH_GOOGLE_CALLBACK_URL="https://api.example/auth/google/callback",
        OAUTH_FRONTEND_SUCCESS_URL="https://www.arbitertv.com/app",
        OAUTH_FRONTEND_FAILURE_URL="https://www.arbitertv.com/login",
        MAGIC_LINK_VERIFY_URL="https://www.arbitertv.com/auth/magic-link/verify",
    )
    assert configured.cors_origin_list() == ["https://www.arbitertv.com"]
