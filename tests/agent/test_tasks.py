"""Tests for TaskManager and task tracking."""

import pytest
from iris.app.agent.task_manager import TaskManager
from iris.app.schemas.tasks import TaskStatus


@pytest.mark.asyncio
async def test_task_creation_and_retrieval():
    tm = TaskManager()
    state = tm.create_task("Calculate 10 + 20")
    assert state.task_id is not None
    assert state.status == TaskStatus.PENDING

    fetched = tm.get_task(state.task_id)
    assert fetched == state

    resp = tm.get_task_response(state.task_id)
    assert resp is not None
    assert resp.task_id == state.task_id
    assert resp.user_input == "Calculate 10 + 20"


@pytest.mark.asyncio
async def test_task_cancellation():
    tm = TaskManager()
    state = tm.create_task("Long running task")

    success = await tm.cancel_task(state.task_id)
    assert success is True
    assert state.status == TaskStatus.CANCELLED

    # Re-cancelling terminal task returns False
    second_attempt = await tm.cancel_task(state.task_id)
    assert second_attempt is False
