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
