from __future__ import annotations

import httpx
import pytest

import main


async def _preflight(origin: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.options(
            "/api/settings",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )


@pytest.mark.asyncio
async def test_packaged_electron_origin_is_allowed() -> None:
    response = await _preflight("app://.")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "app://."


@pytest.mark.asyncio
async def test_untrusted_origin_is_rejected() -> None:
    response = await _preflight("https://example.com")

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
