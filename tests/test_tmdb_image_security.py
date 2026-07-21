from __future__ import annotations

import httpx
import pytest

from app.services import tmdb


pytestmark = pytest.mark.asyncio


async def test_image_fetch_rejects_url_and_path_injection():
    for path in (
        "http://127.0.0.1/private",
        "//169.254.169.254/latest/meta-data",
        "/../secret",
        "/poster.svg",
        "/poster?url=https://example.com",
    ):
        with pytest.raises(ValueError, match="invalid"):
            await tmdb.fetch_tmdb_image(path=path)


async def test_image_fetch_rejects_oversized_declared_response(monkeypatch):
    original_client = httpx.AsyncClient

    def client_factory(*, base_url, **kwargs):
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={
                    "content-type": "image/jpeg",
                    "content-length": str(10 * 1024 * 1024 + 1),
                },
                content=b"small-test-body",
            )
        )
        return original_client(base_url=base_url, transport=transport, **kwargs)

    monkeypatch.setattr(tmdb.httpx, "AsyncClient", client_factory)
    with pytest.raises(ValueError, match="unavailable"):
        await tmdb.fetch_tmdb_image(path="/poster.jpg")


async def test_image_fetch_accepts_only_raster_mime_types(monkeypatch):
    original_client = httpx.AsyncClient

    def client_factory(*, base_url, **kwargs):
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"content-type": "image/svg+xml"},
                content=b"<svg onload='alert(1)'/>",
            )
        )
        return original_client(base_url=base_url, transport=transport, **kwargs)

    monkeypatch.setattr(tmdb.httpx, "AsyncClient", client_factory)
    with pytest.raises(ValueError, match="unavailable"):
        await tmdb.fetch_tmdb_image(path="/poster.jpg")
