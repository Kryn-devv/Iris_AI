"""Integration tests for IRIS REST API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_chat_endpoint_calculator(async_client: AsyncClient):
    payload = {"message": "What is 25 multiplied by 47?"}
    response = await async_client.post("/api/v1/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "COMPLETED"
    assert data["intent_detected"] == "calculator"
    assert "1175" in data["response"]
    assert len(data["tools_executed"]) == 1
    assert data["tools_executed"][0]["tool_name"] == "calculator"
    assert "provider" in data
    assert "model" in data


@pytest.mark.asyncio
async def test_chat_endpoint_system_info(async_client: AsyncClient):
    payload = {"message": "What operating system am I running?"}
    response = await async_client.post("/api/v1/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "COMPLETED"
    assert data["intent_detected"] == "system_info"
    assert len(data["tools_executed"]) == 1
    assert data["tools_executed"][0]["tool_name"] == "system_info"


@pytest.mark.asyncio
async def test_chat_endpoint_time(async_client: AsyncClient):
    payload = {"message": "What time is it?"}
    response = await async_client.post("/api/v1/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "COMPLETED"
    assert data["intent_detected"] == "time"
    assert len(data["tools_executed"]) == 1
    assert data["tools_executed"][0]["tool_name"] == "time"


@pytest.mark.asyncio
async def test_chat_endpoint_empty_message(async_client: AsyncClient):
    payload = {"message": "  "}
    response = await async_client.post("/api/v1/chat", json=payload)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_tools_list_endpoint(async_client: AsyncClient):
    response = await async_client.get("/api/v1/tools")
    assert response.status_code == 200
    tools = response.json()
    tool_names = [t["name"] for t in tools]
    assert "calculator" in tool_names
    assert "system_info" in tool_names
    assert "time" in tool_names


@pytest.mark.asyncio
async def test_tasks_crud_endpoints(async_client: AsyncClient):
    payload = {"user_input": "What is 10 + 20?"}
    create_res = await async_client.post("/api/v1/tasks", json=payload)
    assert create_res.status_code == 200
    task_data = create_res.json()
    task_id = task_data["task_id"]
    assert task_data["status"] == "COMPLETED"

    get_res = await async_client.get(f"/api/v1/tasks/{task_id}")
    assert get_res.status_code == 200
    assert get_res.json()["task_id"] == task_id


@pytest.mark.asyncio
async def test_memory_endpoint(async_client: AsyncClient):
    store_res = await async_client.post("/api/v1/memory", json={"memory_type": "working", "key": "test_k", "value": "test_v"})
    assert store_res.status_code == 200

    get_res = await async_client.get("/api/v1/memory?memory_type=working&key=test_k")
    assert get_res.status_code == 200
    assert get_res.json()["value"] == "test_v"


@pytest.mark.asyncio
async def test_llm_status_endpoint(async_client: AsyncClient):
    response = await async_client.get("/api/v1/llm/status")
    assert response.status_code == 200
    data = response.json()
    assert "mode" in data
    assert "provider" in data
    assert "available" in data
    assert "capabilities" in data
