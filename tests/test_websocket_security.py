import pytest

from app.core.websocket_security import (
    normalize_websocket_origin,
    websocket_origin_is_allowed,
)


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://www.arbitertv.com",
    ],
)
def test_websocket_origin_allows_explicit_configured_origins(origin):
    assert websocket_origin_is_allowed(
        origin,
        allowed_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "https://www.arbitertv.com",
        ],
    )


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "",
        "null",
        "https://evil.example",
        "//www.arbitertv.com",
        "https://www.arbitertv.com/path",
        "https://www.arbitertv.com?next=evil",
        "https://user@www.arbitertv.com",
        " https://www.arbitertv.com",
        "https://www.arbitertv.com:bad",
    ],
)
def test_websocket_origin_rejects_absent_disallowed_and_malformed_values(origin):
    assert not websocket_origin_is_allowed(
        origin,
        allowed_origins=["https://www.arbitertv.com"],
    )


def test_websocket_origin_never_honors_wildcard_configuration():
    assert not websocket_origin_is_allowed(
        "https://unknown.example",
        allowed_origins=["*"],
    )


def test_origin_normalization_is_case_insensitive_for_scheme_and_host():
    assert normalize_websocket_origin("HTTPS://WWW.ARBITERTV.COM") == (
        "https://www.arbitertv.com"
    )
