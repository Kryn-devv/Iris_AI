"""Comprehensive test suite for Phase 4 — Advanced Memory, Personal Context & RAG."""

import pytest
from httpx import AsyncClient, ASGITransport
from iris.app.schemas.memory import MemoryType, ConfidenceLevel, MemoryRecord
from iris.app.memory.sanitizer import MemorySanitizer
from iris.app.memory.service import MemoryService
from iris.app.memory.extractor import MemoryExtractor
from iris.app.agent.kernel import AgentKernel
from iris.app.main import app


# 1. Memory CRUD Tests
@pytest.mark.asyncio
async def test_memory_crud_operations():
    service = MemoryService()

    # Create / Remember
    await service.remember("user_skill", "Python", memory_type=MemoryType.SEMANTIC)

    # Retrieve
    val = await service.retrieve("user_skill", memory_type=MemoryType.SEMANTIC)
    assert val == "Python"

    # Update (Conflict Resolution)
    await service.remember("user_skill", "Python and Rust", memory_type=MemoryType.SEMANTIC)
    updated_val = await service.retrieve("user_skill", memory_type=MemoryType.SEMANTIC)
    assert updated_val == "Python and Rust"

    # Forget / Delete
    forgot = await service.forget("user_skill", memory_type=MemoryType.SEMANTIC)
    assert forgot is True

    # Verify retrieval after forget
    after_val = await service.retrieve("user_skill", memory_type=MemoryType.SEMANTIC)
    assert after_val is None


# 2. Privacy & Redaction Tests
@pytest.mark.asyncio
async def test_memory_privacy_sanitizer():
    sanitizer = MemorySanitizer()

    # Redact OpenAI API Key
    text_with_sk = "Here is my secret key sk-abc1234567890abcdef1234567890 for testing."
    clean_sk = sanitizer.sanitize_text(text_with_sk)
    assert "sk-abc" not in clean_sk
    assert "[REDACTED]" in clean_sk

    # Redact Bearer token
    text_with_bearer = "Authorization: Bearer mysecrettoken1234567890abcdef"
    clean_bearer = sanitizer.sanitize_text(text_with_bearer)
    assert "mysecrettoken" not in clean_bearer

    # Redact Dict secrets
    secret_dict = {"api_key": "sk-12345", "password": "supersecretpassword", "normal": "public_data"}
    clean_dict = sanitizer.sanitize_value(secret_dict)
    assert clean_dict["api_key"] == "[REDACTED]"
    assert clean_dict["password"] == "[REDACTED]"
    assert clean_dict["normal"] == "public_data"


# 3. Relevance Scoring & Retrieval Tests
@pytest.mark.asyncio
async def test_memory_relevance_search():
    service = MemoryService()

    await service.remember("robot_budget", "₹15,000", memory_type=MemoryType.PROJECT, metadata={"project_id": "robot_project", "importance": 0.9})
    await service.remember("robot_microcontroller", "ESP32", memory_type=MemoryType.PROJECT, metadata={"project_id": "robot_project", "importance": 0.9})
    await service.remember("lunch_preference", "Pizza", memory_type=MemoryType.SEMANTIC, metadata={"importance": 0.2})

    # Search query matching "robot budget"
    results = await service.search("What is my robot budget?", project_id="robot_project", limit=5)
    assert len(results) >= 1
    top_record, top_score = results[0]
    assert top_record.key in ["robot_budget", "robot_microcontroller"]
    assert top_score > 0.3


# 4. Memory Extractor & Natural Command Tests
@pytest.mark.asyncio
async def test_memory_extractor_commands():
    # Explicit Remember
    cmd, payload = MemoryExtractor.parse_command("Remember that my robot budget is ₹15,000.")
    assert cmd == "remember"
    assert payload["key"] == "robot_budget"
    assert "15,000" in str(payload["value"])

    # Explicit Forget
    cmd_f, payload_f = MemoryExtractor.parse_command("Forget my robot budget.")
    assert cmd_f == "forget"
    assert payload_f["key"] == "robot_budget"

    # Explicit Recall
    cmd_r, payload_r = MemoryExtractor.parse_command("What is my robot budget?")
    assert cmd_r == "recall"
    assert payload_r["key"] == "robot_budget"

    # Non-memory transient query
    cmd_n, payload_n = MemoryExtractor.parse_command("What time is it?")
    assert cmd_n is None


# 5. Agent Kernel End-to-End Follow-Up Context Test
@pytest.mark.asyncio
async def test_agent_kernel_memory_followup_flow():
    kernel = AgentKernel()
    await kernel.memory_service.clear()

    # Step 1: Tell IRIS to remember budget
    res1 = await kernel.process_request("Remember that my robot project budget is ₹15,000.")
    assert res1.status == "COMPLETED"
    assert "remember" in res1.response.lower() or "robot project" in res1.response.lower()

    # Step 2: Ask IRIS for budget (retrieval)
    res2 = await kernel.process_request("What is my robot budget?")
    assert res2.status == "COMPLETED"
    assert "15,000" in res2.response or "15000" in res2.response

    # Step 3: Tell IRIS to remember microcontroller
    res3 = await kernel.process_request("Remember that my robot uses ESP32.")
    assert res3.status == "COMPLETED"

    # Step 4: Ask IRIS for microcontroller (retrieval)
    res4 = await kernel.process_request("What microcontroller does my robot use?")
    assert res4.status == "COMPLETED"
    assert "ESP32" in res4.response

    # Step 5: Forget budget
    res5 = await kernel.process_request("Forget my robot budget.")
    assert res5.status == "COMPLETED"

    # Step 6: Ask IRIS for budget again (must indicate no longer available)
    res6 = await kernel.process_request("What is my robot budget?")
    assert res6.status == "COMPLETED"
    assert "don't have" in res6.response.lower() or "no record" in res6.response.lower() or "not" in res6.response.lower()


# 6. REST API Memory Endpoints Test
@pytest.mark.asyncio
async def test_memory_rest_api_endpoints():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Store Memory
        post_res = await client.post("/api/v1/memory", json={
            "type": "semantic",
            "key": "test_api_fact",
            "value": "FastAPI is awesome",
            "importance": 0.8,
        })
        assert post_res.status_code == 200
        assert post_res.json()["status"] == "success"

        # Search Memory
        search_res = await client.get("/api/v1/memory/search", params={"query": "FastAPI"})
        assert search_res.status_code == 200
        assert search_res.json()["total_found"] >= 1

        # Project Memory API
        proj_post = await client.post("/api/v1/projects/robot_1/memory", json={
            "type": "project",
            "key": "sensor_type",
            "value": "LiDAR",
        })
        assert proj_post.status_code == 200

        proj_get = await client.get("/api/v1/projects/robot_1/memory")
        assert proj_get.status_code == 200
        assert proj_get.json()["total_records"] >= 1

        # Delete Memory
        del_res = await client.delete("/api/v1/memory/test_api_fact")
        assert del_res.status_code == 200
