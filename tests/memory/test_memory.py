"""Tests for IRIS memory abstraction layers."""

import pytest
from iris.app.memory.working import WorkingMemory
from iris.app.memory.conversation import ConversationMemory
from iris.app.memory.long_term import LongTermMemory
from iris.app.memory.project import ProjectMemory


@pytest.mark.asyncio
async def test_working_memory():
    mem = WorkingMemory()
    await mem.remember("temp_key", {"val": 123})
    res = await mem.retrieve("temp_key")
    assert res == {"val": 123}

    forgot = await mem.forget("temp_key")
    assert forgot is True
    assert await mem.retrieve("temp_key") is None


@pytest.mark.asyncio
async def test_conversation_memory():
    mem = ConversationMemory()
    cid = "conv_123"
    await mem.remember(cid, {"role": "user", "content": "Hello"})
    await mem.remember(cid, {"role": "assistant", "content": "Hi there"})

    history = await mem.retrieve(cid)
    assert len(history) == 2
    assert history[0]["content"] == "Hello"


@pytest.mark.asyncio
async def test_project_memory():
    mem = ProjectMemory()
    await mem.remember("custom_flag", True)
    assert await mem.retrieve("custom_flag") is True
    assert await mem.retrieve("name") == "IRIS Project"


@pytest.mark.asyncio
async def test_long_term_memory_in_memory_cache():
    mem = LongTermMemory(session=None)
    await mem.remember("user_pref_theme", "dark")
    val = await mem.retrieve("user_pref_theme")
    assert val == "dark"
