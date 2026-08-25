"""Tests for health and status endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint(async_client: AsyncClient):
    response = await async_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["app"] == "IRIS"


@pytest.mark.asyncio
async def test_system_status_endpoint(async_client: AsyncClient):
    response = await async_client.get("/api/v1/status")
    assert response.status_code == 200
    data = response.json()
    assert data["app_name"] == "IRIS"
    assert data["offline_mode"] is True
    assert "calculator" in data["registered_tools"]
    assert "system_info" in data["registered_tools"]
    assert "time" in data["registered_tools"]
