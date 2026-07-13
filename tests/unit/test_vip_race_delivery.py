"""VIP reply_gen race: rapid messages bump gen; stale auto_reply must not deliver."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import auth_users
import state
from handlers import business, timer as timer_mod


VIP_ID = 777001
VIP_CHAT_ID = 777001
ADMIN_ID = 555001
BC_ID = "bc_test"


@pytest.fixture(autouse=True)
def _reset(tmp_path):
    users_file = tmp_path / "authorized.json"
    auth_users.configure(
        users_file=str(users_file),
        max_users=5,
        seed_user_ids=[VIP_ID],
        admin_id=ADMIN_ID,
    )
    auth_users.set_admin_id(ADMIN_ID)
    state.history.clear()
    state.reply_gen.clear()
    state.timers.clear()
    state.timer_schedule.clear()
    state.chat_bc.clear()
    state.pending_msg.clear()
    state.chat_meta.clear()
    state.connections.clear()
    yield
    state.history.clear()
    state.reply_gen.clear()
    state.timers.clear()
    state.timer_schedule.clear()
    state.chat_bc.clear()
    state.pending_msg.clear()
    state.chat_meta.clear()
    state.connections.clear()


def _vip_message(text: str, *, message_id: int) -> MagicMock:
    msg = MagicMock()
    msg.business_connection_id = BC_ID
    msg.chat.id = VIP_CHAT_ID
    msg.text = text
    msg.caption = None
    msg.from_user.id = VIP_ID
    msg.from_user.username = "testvip"
    msg.from_user.first_name = "Test"
    msg.message_id = message_id
    return msg


@pytest.mark.asyncio
async def test_rapid_vip_messages_increment_reply_gen(chat_history_db):
    """Two quick messages on the same chat bump reply_gen (1 → 2)."""
    created_tasks: list[asyncio.Task] = []
    real_create_task = asyncio.create_task

    def capture_task(coro):
        task = real_create_task(coro)
        created_tasks.append(task)
        return task

    context = AsyncMock()

    with (
        patch("handlers.business.asyncio.create_task", side_effect=capture_task),
        patch("handlers.business.compute_reply_delay", return_value=60.0),
        patch("handlers.business.auto_reply", new_callable=AsyncMock),
    ):
        await business._handle_business_message(
            _vip_message("hola", message_id=1), context, edited=False,
        )
        await business._handle_business_message(
            _vip_message("otra", message_id=2), context, edited=False,
        )

    assert state.reply_gen[VIP_CHAT_ID] == 2
    assert len(created_tasks) == 2
    assert state.timers[VIP_CHAT_ID] is created_tasks[1]
    await asyncio.sleep(0)
    assert created_tasks[0].cancelled() is True


@pytest.mark.asyncio
async def test_stale_auto_reply_does_not_deliver(monkeypatch, in_memory_training_db):
    """auto_reply started at gen=1 must not deliver after a newer message bumps gen."""
    monkeypatch.setattr(timer_mod, "APPROVAL_MODE", False)
    chat_id = VIP_CHAT_ID
    stale_gen = 1
    state.reply_gen[chat_id] = stale_gen
    state.history[chat_id] = [
        {"role": "user", "content": "hola"},
        {"role": "user", "content": "otra"},
    ]

    async def llm_then_bump(*_args, **_kwargs):
        state.reply_gen[chat_id] = stale_gen + 1
        return ("respuesta vieja", 90, "saludo", False, "", None)

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch(
            "handlers.timer.get_diana_response",
            side_effect=llm_then_bump,
        ) as mock_llm,
        patch("handlers.timer.deliver_vip_response", new_callable=AsyncMock) as mock_deliver,
        patch("handlers.timer.notify_diana", new_callable=AsyncMock),
        patch("handlers.timer.save_example", return_value=99),
    ):
        await timer_mod.auto_reply(
            AsyncMock(), chat_id, "testvip", BC_ID, stale_gen,
        )

    mock_llm.assert_awaited_once()
    mock_deliver.assert_not_awaited()
    assert chat_id not in state.timers