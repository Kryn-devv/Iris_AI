"""Tests for Phase 4A Developer Chat UI and static asset serving."""

import pytest
from httpx import AsyncClient, ASGITransport
from iris.app.main import app


@pytest.mark.asyncio
async def test_chat_ui_endpoint_returns_200():
    """Verify GET /chat returns 200 OK and HTML document."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/chat")
        assert res.status_code == 200
        assert "text/html" in res.headers.get("content-type", "")
        assert "IRIS" in res.text
        assert "app.js" in res.text


@pytest.mark.asyncio
async def test_static_assets_loading():
    """Verify static CSS and JS files serve cleanly."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        css_res = await client.get("/static/style.css")
        assert css_res.status_code == 200
        assert "text/css" in css_res.headers.get("content-type", "")

        js_res = await client.get("/static/app.js")
        assert js_res.status_code == 200
        assert "application/javascript" in js_res.headers.get("content-type", "") or "text/javascript" in js_res.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_chat_api_validation_handling():
    """Verify validation responses for empty or malformed chat payloads."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Empty message string returns 400
        empty_res = await client.post("/api/v1/chat", json={"message": "   "})
        assert empty_res.status_code == 400
        assert "Message field cannot be empty" in empty_res.json()["detail"]

        # Missing required field returns 422
        invalid_res = await client.post("/api/v1/chat", json={"invalid_field": 123})
        assert invalid_res.status_code == 422
