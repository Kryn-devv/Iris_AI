"""Integration tests for Phase 5 Language Intelligence in Agent Kernel."""

import pytest
from httpx import AsyncClient, ASGITransport
from iris.app.main import app
from iris.app.agent.kernel import AgentKernel
from iris.app.schemas.messages import ChatRequest


@pytest.mark.asyncio
async def test_integration_english_flow():
    kernel = AgentKernel()
    res = await kernel.process_request(user_input="Hello IRIS")
    assert res.status == "COMPLETED"
    assert res.language == "en"
    assert "iris" in res.response.lower()


@pytest.mark.asyncio
async def test_integration_hindi_devanagari_flow():
    kernel = AgentKernel()
    res = await kernel.process_request(user_input="नमस्ते IRIS")
    assert res.status == "COMPLETED"
    assert res.language == "hi"
    assert res.response_language == "hi"
    assert "नमस्ते" in res.response or "IRIS" in res.response


@pytest.mark.asyncio
async def test_integration_hinglish_flow():
    kernel = AgentKernel()
    res = await kernel.process_request(user_input="bhai kya haal hai")
    assert res.status == "COMPLETED"
    assert res.language == "hinglish"
    assert res.response_language == "hinglish"
    assert "bhai" in res.response.lower() or "ready" in res.response.lower()


@pytest.mark.asyncio
async def test_integration_hindi_calculator_tool():
    kernel = AgentKernel()
    res = await kernel.process_request(user_input="25 को 40 से गुणा करो")
    assert res.status == "COMPLETED"
    assert len(res.tools_executed) == 1
    assert res.tools_executed[0].tool_name == "calculator"
    assert res.tools_executed[0].result["result"] == 1000


@pytest.mark.asyncio
async def test_integration_hinglish_calculator_tool():
    kernel = AgentKernel()
    res = await kernel.process_request(user_input="25 ko 40 se multiply karo")
    assert res.status == "COMPLETED"
    assert len(res.tools_executed) == 1
    assert res.tools_executed[0].tool_name == "calculator"
    assert res.tools_executed[0].result["result"] == 1000


@pytest.mark.asyncio
async def test_integration_calculator_regression_multi_op():
    kernel = AgentKernel()
    res = await kernel.process_request(user_input="what is 78*23*7")
    assert res.status == "COMPLETED"
    assert "12558" in res.response


@pytest.mark.asyncio
async def test_integration_explicit_language_switch():
    kernel = AgentKernel()
    conv_id = "conv_lang_test_123"

    res1 = await kernel.process_request(user_input="Explain recursion in Hindi", conversation_id=conv_id)
    assert res1.response_language == "hi"
    assert "रिकर्शन" in res1.response or "फ़ंक्शन" in res1.response

    res2 = await kernel.process_request(user_input="Ab English mein explain karo", conversation_id=conv_id)
    assert res2.response_language == "en"
    assert "Recursion" in res2.response

    res3 = await kernel.process_request(user_input="Hinglish mein samjha", conversation_id=conv_id)
    assert res3.response_language == "hinglish"
    assert "concept" in res3.response.lower() or "call" in res3.response.lower()


@pytest.mark.asyncio
async def test_integration_chat_api_multilingual_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/v1/chat", json={"message": "bhai kya haal hai"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "COMPLETED"
        assert data["language"] == "hinglish"
        assert data["response_language"] == "hinglish"
        assert "bhai" in data["response"].lower() or "ready" in data["response"].lower()
