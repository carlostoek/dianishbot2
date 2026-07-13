"""Tests for per-VIP full blackout pause."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

import auth_users
import state
from handlers import business, timer as timer_mod
from services import auth_service, data_pause, sandbox
from services.chat_history import append_message, ensure_loaded, load_chat_history


ADMIN_ID = 555010
VIP_ID = 888010


@pytest.fixture(autouse=True)
def _configure(tmp_path):
    users_file = tmp_path / "authorized.json"
    auth_service.configure(
        users_file=str(users_file), max_users=5, seed_user_ids=[], admin_id=ADMIN_ID,
    )
    auth_service.set_admin_id(ADMIN_ID)
    auth_users.add_user(VIP_ID, "vip_pause", "VIP Pause")
    sandbox._active.clear()
    state.history.clear()
    state.reply_gen.clear()
    state.timers.clear()
    state.timer_schedule.clear()
    state.pending_approval.clear()
    state.pending_escalations.clear()
    state.chat_bc.clear()
    state.pending_msg.clear()
    state.chat_meta.clear()
    yield
    sandbox._active.clear()
    state.history.clear()
    state.reply_gen.clear()
    state.timers.clear()
    state.timer_schedule.clear()
    state.pending_approval.clear()
    state.pending_escalations.clear()
    state.chat_bc.clear()
    state.pending_msg.clear()
    state.chat_meta.clear()


def test_pause_indefinite_and_resume():
    ok, err = data_pause.pause(VIP_ID, days=None)
    assert ok and err is None
    assert data_pause.is_paused(VIP_ID)

    entry = auth_service.get_user_entry(VIP_ID)
    assert entry["data_paused_until"] == "indefinite"

    assert data_pause.resume(VIP_ID)
    assert not data_pause.is_paused(VIP_ID)
    assert "data_paused_until" not in auth_service.get_user_entry(VIP_ID)


def test_pause_clears_ram_state():
    state.history[VIP_ID] = [{"role": "user", "content": "viejo"}]
    state.reply_gen[VIP_ID] = 3
    state.timer_schedule[VIP_ID] = {"username": "x", "bc_id": "bc", "gen": 3, "fire_at": "x"}
    state.pending_approval[99] = {"chat_id": VIP_ID, "bc_id": "bc", "username": "x", "gen": 1}

    data_pause.pause(VIP_ID, days=1)

    assert VIP_ID not in state.history
    assert VIP_ID not in state.reply_gen
    assert VIP_ID not in state.timer_schedule
    assert 99 not in state.pending_approval


def test_pause_with_duration_auto_expires():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    auth_service.update_user_entry(
        VIP_ID,
        lambda e: e.update({
            "data_paused_at": past.isoformat(),
            "data_paused_until": past.isoformat(),
        }),
    )
    assert not data_pause.is_paused(VIP_ID)


def test_should_persist_respects_pause():
    data_pause.pause(VIP_ID, days=7)
    assert not sandbox.should_persist(VIP_ID)
    data_pause.resume(VIP_ID)
    assert sandbox.should_persist(VIP_ID)


def test_append_message_skips_ram_and_db_when_paused(chat_history_db):
    data_pause.pause(VIP_ID, days=1)
    append_message(VIP_ID, "user", "hola")
    assert VIP_ID not in state.history
    assert not load_chat_history(VIP_ID)


def test_ensure_loaded_returns_empty_when_paused(chat_history_db):
    from services import chat_history

    chat_history.append_message(VIP_ID, "user", "persistido")
    data_pause.pause(VIP_ID, days=1)

    assert ensure_loaded(VIP_ID) == []
    assert VIP_ID not in state.history


@pytest.mark.asyncio
async def test_business_ignores_paused_vip_completely():
    data_pause.pause(VIP_ID, days=3)
    with (
        patch("services.reengagement.touch_inbound") as mock_touch,
        patch("handlers.business.auto_reply", new_callable=AsyncMock) as mock_auto,
    ):
        msg = AsyncMock()
        msg.business_connection_id = "bc_test"
        msg.chat.id = VIP_ID
        msg.chat.type = "private"
        msg.from_user.id = VIP_ID
        msg.from_user.username = "vip_pause"
        msg.from_user.first_name = "VIP Pause"
        msg.message_id = 1
        msg.text = "hola"
        msg.caption = None
        msg.photo = None
        msg.voice = None
        msg.audio = None
        msg.video = None
        msg.document = None
        msg.sticker = None

        state.connections["bc_test"] = ADMIN_ID
        context = AsyncMock()

        await business._handle_business_message(msg, context, edited=False)

    mock_touch.assert_not_called()
    mock_auto.assert_not_called()
    assert VIP_ID not in state.history
    assert VIP_ID not in state.timers


@pytest.mark.asyncio
async def test_business_ignores_owner_messages_to_paused_chat():
    data_pause.pause(VIP_ID, days=1)
    msg = AsyncMock()
    msg.business_connection_id = "bc_test"
    msg.chat.id = VIP_ID
    msg.chat.type = "private"
    msg.from_user.id = ADMIN_ID
    msg.from_user.username = "diana"
    msg.from_user.first_name = "Diana"
    msg.message_id = 2
    msg.text = "hola manual"
    msg.caption = None
    msg.photo = None
    msg.voice = None
    msg.audio = None
    msg.video = None
    msg.document = None
    msg.sticker = None

    state.connections["bc_test"] = ADMIN_ID

    await business._handle_business_message(msg, AsyncMock(), edited=False)

    assert VIP_ID not in state.history
    assert VIP_ID not in state.timers


@pytest.mark.asyncio
async def test_resume_restores_db_history_on_next_message(chat_history_db):
    from services import chat_history

    chat_history.append_message(VIP_ID, "user", "antes de pausa")
    data_pause.pause(VIP_ID, days=1)
    data_pause.resume(VIP_ID)

    msgs = ensure_loaded(VIP_ID)
    assert msgs == [{"role": "user", "content": "antes de pausa"}]


@pytest.mark.asyncio
async def test_stale_timer_does_not_run_after_pause(in_memory_training_db):
    chat_id = VIP_ID
    gen = 1
    state.reply_gen[chat_id] = gen
    state.history[chat_id] = [{"role": "user", "content": "hola"}]
    data_pause.pause(chat_id, days=7)

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch("services.llm.raw_call", new_callable=AsyncMock) as mock_llm,
        patch("handlers.timer.save_example") as mock_save,
    ):
        await timer_mod.auto_reply(AsyncMock(), chat_id, "vip", "bc_test", gen)

    mock_llm.assert_not_called()
    mock_save.assert_not_called()
    assert not state.pending_approval


@pytest.fixture
def admin_user(make_user):
    return make_user(user_id=ADMIN_ID, username="diana_admin", first_name="Diana")


@pytest.mark.asyncio
async def test_pause_menu_callback(make_mock_callback_update, make_context, admin_user):
    update = make_mock_callback_update(
        data=f"au:pause_menu:{VIP_ID}",
        user=admin_user,
    )
    update.callback_query.edit_message_text = AsyncMock(
        side_effect=Exception("edit fail"),
    )
    update.callback_query.message.reply_text = AsyncMock()

    result = await auth_users.handle_callback(update, make_context())
    assert result is True
    text = update.callback_query.message.reply_text.await_args[0][0]
    assert "Pausar VIP" in text
    assert "sin respuestas automáticas" in text
    markup = update.callback_query.message.reply_text.await_args.kwargs["reply_markup"]
    callbacks = [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
    ]
    assert f"au:pause_days:{VIP_ID}:7" in callbacks


@pytest.mark.asyncio
async def test_pause_days_and_resume_callbacks(
    make_mock_callback_update, make_context, admin_user,
):
    update = make_mock_callback_update(
        data=f"au:pause_days:{VIP_ID}:3",
        user=admin_user,
    )
    update.callback_query.edit_message_text = AsyncMock(
        side_effect=Exception("edit fail"),
    )
    update.callback_query.message.reply_text = AsyncMock()

    assert await auth_users.handle_callback(update, make_context())
    assert data_pause.is_paused(VIP_ID)

    update2 = make_mock_callback_update(
        data=f"au:resume:{VIP_ID}",
        user=admin_user,
    )
    update2.callback_query.edit_message_text = AsyncMock(
        side_effect=Exception("edit fail"),
    )
    update2.callback_query.message.reply_text = AsyncMock()

    assert await auth_users.handle_callback(update2, make_context())
    assert not data_pause.is_paused(VIP_ID)