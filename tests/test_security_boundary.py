from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.middleware.security_boundary import SecurityBoundaryMiddleware


def _test_app(max_body_bytes: int = 64) -> FastAPI:
    app = FastAPI()

    @app.post("/mutation")
    async def mutation(request: Request):
        return {"size": len(await request.body())}

    @app.get("/private")
    async def private():
        return {"ok": True}

    app.add_middleware(SecurityBoundaryMiddleware, max_body_bytes=max_body_bytes)
    return app


@pytest.mark.asyncio
async def test_allowed_origin_can_mutate(monkeypatch):
    monkeypatch.setattr(
        "app.middleware.security_boundary.settings.cors_origins",
        "https://www.arbitertv.com",
    )
    app = _test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://api.example"
    ) as client:
        response = await client.post(
            "/mutation",
            content=b"ok",
            headers={"Origin": "https://www.arbitertv.com"},
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; frame-ancestors 'none'"
    )


@pytest.mark.asyncio
async def test_disallowed_and_malformed_origins_are_rejected(monkeypatch):
    monkeypatch.setattr(
        "app.middleware.security_boundary.settings.cors_origins",
        "https://www.arbitertv.com",
    )
    app = _test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://api.example"
    ) as client:
        for origin in (
            "https://evil.example",
            "null",
            "https://www.arbitertv.com.evil.example",
            "https://www.arbitertv.com/path",
        ):
            response = await client.post(
                "/mutation", content=b"ok", headers={"Origin": origin}
            )
            assert response.status_code == 403


@pytest.mark.asyncio
async def test_missing_origin_is_rejected_outside_local_and_test(monkeypatch):
    monkeypatch.setattr("app.middleware.security_boundary.settings.env", "production")
    app = _test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://api.example"
    ) as client:
        response = await client.post("/mutation", content=b"ok")

    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; frame-ancestors 'none'"
    )
    assert response.headers["strict-transport-security"] == (
        "max-age=63072000; includeSubDomains"
    )


@pytest.mark.asyncio
async def test_oversized_declared_and_streamed_bodies_are_rejected(monkeypatch):
    monkeypatch.setattr(
        "app.middleware.security_boundary.settings.cors_origins",
        "https://www.arbitertv.com",
    )
    app = _test_app(max_body_bytes=4)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://api.example"
    ) as client:
        headers = {"Origin": "https://www.arbitertv.com"}
        declared = await client.post("/mutation", content=b"12345", headers=headers)

        async def chunks():
            yield b"123"
            yield b"45"

        streamed = await client.post("/mutation", content=chunks(), headers=headers)

    assert declared.status_code == 413
    assert streamed.status_code == 413
