"""Integration tests for auto_reply retry cancellation via reply_gen."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from state import history, reply_gen, timers
import handlers.timer as timer_mod
import services.llm as llm_mod


@pytest.fixture(autouse=True)
def _reset_state():
    history.clear()
    reply_gen.clear()
    timers.clear()
    yield
    history.clear()
    reply_gen.clear()
    timers.clear()


@pytest.mark.asyncio
async def test_auto_reply_aborts_llm_retry_when_new_message_arrives(in_memory_training_db):
    chat_id = 100
    gen = 1
    reply_gen[chat_id] = gen
    history[chat_id] = [{"role": "user", "content": "hola"}]

    sleep_calls = 0

    async def sleep_side_effect(_delay):
        nonlocal sleep_calls
        sleep_calls += 1
        # 1st sleep = timer delay; 2nd = LLM retry backoff → simulate new user message
        if sleep_calls == 2:
            reply_gen[chat_id] = gen + 1

    with (
        patch("asyncio.sleep", new_callable=AsyncMock, side_effect=sleep_side_effect),
        patch(
            "services.llm.raw_call", new_callable=AsyncMock,
            return_value=(None, "error_http_api"),
        ) as mock_raw,
        patch("handlers.timer.notify_diana_approval", new_callable=AsyncMock) as mock_notify,
        patch("handlers.timer.notify_diana_llm_failure", new_callable=AsyncMock) as mock_fail_notify,
        patch("handlers.timer.save_llm_failure") as mock_save_fail,
        patch("handlers.timer.save_example") as mock_save,
    ):
        task = asyncio.create_task(
            timer_mod.auto_reply(AsyncMock(), chat_id, "vip", "bc_test", gen),
        )
        timers[chat_id] = task
        await task

    assert mock_raw.await_count == 1
    assert mock_save.call_count == 0
    assert mock_notify.await_count == 0
    assert chat_id not in timers


@pytest.mark.asyncio
async def test_auto_reply_delivers_after_llm_retry_succeeds(in_memory_training_db):
    chat_id = 200
    gen = 1
    reply_gen[chat_id] = gen
    history[chat_id] = [{"role": "user", "content": "hola"}]

    payloads = [
        (None, "error_http_api"),
        ('{"response": "hey", "confidence": 85, "topic": "saludo"}', None),
    ]

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch("services.llm.raw_call", new_callable=AsyncMock) as mock_raw,
        patch("handlers.timer.notify_diana_approval", new_callable=AsyncMock) as mock_notify,
        patch("handlers.timer.save_example", return_value=42) as mock_save,
    ):
        mock_raw.side_effect = payloads
        task = asyncio.create_task(
            timer_mod.auto_reply(AsyncMock(), chat_id, "vip", "bc_test", gen),
        )
        timers[chat_id] = task
        await task

    assert mock_raw.await_count == 2
    assert mock_save.call_count == 1
    assert mock_notify.await_count == 1
    assert chat_id not in timers