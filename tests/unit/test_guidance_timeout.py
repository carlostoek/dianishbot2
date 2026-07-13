"""Guidance timeout ≡ g:use_draft (WU3)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

import state
from handlers.callbacks.guidance import process_guidance_timeouts
from config import GUIDANCE_TIMEOUT_HOURS


VIP = 155200


@pytest.fixture(autouse=True)
def _reset():
    state.pending_guidance.clear()
    state.pending_approval.clear()
    state.reply_gen.clear()
    state.history.clear()
    yield
    state.pending_guidance.clear()
    state.pending_approval.clear()
    state.reply_gen.clear()
    state.history.clear()


def _old_ts(hours_ago: float) -> str:
    return (datetime.now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


@pytest.mark.asyncio
async def test_timeout_opens_draft_path_supervised(in_memory_training_db):
    from services import knowledge
    gid = knowledge.create_guidance_request(
        chat_id=VIP,
        username="vip",
        topic="limites",
        gap_question="¿X?",
        draft_response="borrador viejo",
    )
    state.pending_guidance[gid] = {
        "chat_id": VIP,
        "bc_id": "bc",
        "username": "vip",
        "gen": 3,
        "topic": "limites",
        "gap_question": "¿X?",
        "draft_response": "borrador viejo",
        "confidence": 70,
        "created_at": _old_ts(GUIDANCE_TIMEOUT_HOURS + 1),
    }
    state.reply_gen[VIP] = 3
    state.history[VIP] = [{"role": "user", "content": "q"}]
    bot = AsyncMock()

    with (
        patch("handlers.timer._is_supervised_for_chat", return_value=True),
        patch("handlers.timer.notify_diana_approval", new_callable=AsyncMock) as mock_approval,
        patch("handlers.timer.deliver_vip_response", new_callable=AsyncMock) as mock_deliver,
        patch("handlers.timer.save_example", return_value=900) as mock_save,
        patch(
            "handlers.callbacks.guidance.DIANA_ADMIN_CHAT_ID", 555,
        ),
    ):
        n = await process_guidance_timeouts(bot)

    assert n == 1
    assert gid not in state.pending_guidance
    req = knowledge.get_guidance_request(gid)
    assert req["status"] == "timeout"
    mock_save.assert_called_once()
    assert mock_save.call_args[0][3] == "borrador viejo"
    mock_approval.assert_awaited_once()
    mock_deliver.assert_not_awaited()
    # Diana notified about timeout
    bot.send_message.assert_awaited()
    notify_text = bot.send_message.await_args.kwargs.get("text") or bot.send_message.await_args[0][0]
    assert "timeout" in notify_text.lower() or "expir" in notify_text.lower() or "12" in notify_text


@pytest.mark.asyncio
async def test_fresh_guidance_not_timed_out(in_memory_training_db):
    from services import knowledge
    gid = knowledge.create_guidance_request(
        chat_id=VIP + 1,
        username="vip",
        topic="limites",
        gap_question="¿Y?",
        draft_response="draft",
    )
    state.pending_guidance[gid] = {
        "chat_id": VIP + 1,
        "bc_id": "bc",
        "username": "vip",
        "gen": 1,
        "topic": "limites",
        "gap_question": "¿Y?",
        "draft_response": "draft",
        "confidence": 70,
        "created_at": _old_ts(1),  # only 1h old
    }
    state.reply_gen[VIP + 1] = 1
    bot = AsyncMock()

    with patch("handlers.timer.save_example") as mock_save:
        n = await process_guidance_timeouts(bot)

    assert n == 0
    assert gid in state.pending_guidance
    mock_save.assert_not_called()
    req = knowledge.get_guidance_request(gid)
    assert req["status"] == "pending"


@pytest.mark.asyncio
async def test_timeout_stale_gen_no_send(in_memory_training_db):
    from services import knowledge
    gid = knowledge.create_guidance_request(
        chat_id=VIP + 2,
        username="vip",
        topic="limites",
        gap_question="¿Z?",
        draft_response="old draft",
    )
    state.pending_guidance[gid] = {
        "chat_id": VIP + 2,
        "bc_id": "bc",
        "username": "vip",
        "gen": 1,
        "topic": "limites",
        "gap_question": "¿Z?",
        "draft_response": "old draft",
        "confidence": 70,
        "created_at": _old_ts(GUIDANCE_TIMEOUT_HOURS + 2),
    }
    state.reply_gen[VIP + 2] = 9  # stale
    bot = AsyncMock()

    with (
        patch("handlers.timer.deliver_vip_response", new_callable=AsyncMock) as mock_deliver,
        patch("handlers.timer.save_example") as mock_save,
        patch("handlers.callbacks.guidance.DIANA_ADMIN_CHAT_ID", 555),
    ):
        n = await process_guidance_timeouts(bot)

    assert n == 1
    assert gid not in state.pending_guidance
    req = knowledge.get_guidance_request(gid)
    assert req["status"] == "timeout"
    mock_deliver.assert_not_awaited()
    mock_save.assert_not_called()
