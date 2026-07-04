"""Tests for per-user auto-send mode (bypass supervised approval)."""

import json
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

import auth_users
from state import history, reply_gen, timers, chat_meta
import handlers.timer as timer_mod


ADMIN_ID = 555003
VIP_ID = 888002


@pytest.fixture(autouse=True)
def _configure(tmp_path):
    users_file = tmp_path / "authorized.json"
    auth_users.configure(users_file=str(users_file), max_users=5, seed_user_ids=[])
    auth_users.add_user(VIP_ID, "vip_auto", "VIP Auto")
    auth_users.set_admin_id(ADMIN_ID)
    yield


@pytest.fixture(autouse=True)
def _reset_state():
    history.clear()
    reply_gen.clear()
    timers.clear()
    chat_meta.clear()
    yield
    history.clear()
    reply_gen.clear()
    timers.clear()
    chat_meta.clear()


def test_is_auto_send_defaults_false():
    assert auth_users.is_auto_send_enabled(VIP_ID) is False


def test_set_auto_send_persists(tmp_path):
    users_file = tmp_path / "authorized.json"
    auth_users.configure(users_file=str(users_file), max_users=5, seed_user_ids=[])
    auth_users.add_user(VIP_ID, "vip", "VIP")

    assert auth_users.set_auto_send(VIP_ID, True) is True
    assert auth_users.is_auto_send_enabled(VIP_ID) is True

    data = json.loads(users_file.read_text(encoding="utf-8"))
    assert data["users"][str(VIP_ID)]["auto_send"] is True

    assert auth_users.set_auto_send(VIP_ID, False) is True
    assert auth_users.is_auto_send_enabled(VIP_ID) is False
    data = json.loads(users_file.read_text(encoding="utf-8"))
    assert "auto_send" not in data["users"][str(VIP_ID)]


def test_set_auto_send_unknown_user():
    assert auth_users.set_auto_send(999999, True) is False


def test_compute_reply_delay_auto_send_user(monkeypatch):
    monkeypatch.setattr(timer_mod, "APPROVAL_MODE", True)
    monkeypatch.setattr(timer_mod, "SILENCE_MINUTES", 2)
    monkeypatch.setattr(timer_mod, "RESPONSE_DELAY_MIN", 1)
    monkeypatch.setattr(timer_mod, "RESPONSE_DELAY_MAX", 3)

    auth_users.set_auto_send(VIP_ID, True)
    delay = timer_mod.compute_reply_delay(VIP_ID)
    assert 60 <= delay <= 180

    auth_users.set_auto_send(VIP_ID, False)
    assert timer_mod.compute_reply_delay(VIP_ID) == 120.0


@pytest.mark.asyncio
async def test_auto_reply_delivers_when_auto_send_enabled(
    in_memory_training_db, monkeypatch,
):
    monkeypatch.setattr(timer_mod, "APPROVAL_MODE", True)
    auth_users.set_auto_send(VIP_ID, True)

    chat_id = VIP_ID
    gen = 1
    reply_gen[chat_id] = gen
    history[chat_id] = [{"role": "user", "content": "hola"}]

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch(
            "services.llm.raw_call", new_callable=AsyncMock,
            return_value=('{"response": "hey", "confidence": 85, "topic": "saludo"}', None),
        ),
        patch("handlers.timer.notify_diana_approval", new_callable=AsyncMock) as mock_approval,
        patch("handlers.timer.deliver_vip_response", new_callable=AsyncMock, return_value=True) as mock_deliver,
        patch("handlers.timer.save_example", return_value=42) as mock_save,
    ):
        task = asyncio.create_task(
            timer_mod.auto_reply(AsyncMock(), chat_id, "vip", "bc_test", gen),
        )
        timers[chat_id] = task
        await task

    assert mock_save.call_count == 1
    assert mock_approval.await_count == 0
    assert mock_deliver.await_count == 1
    assert chat_id not in timers


@pytest.mark.asyncio
async def test_auto_reply_approval_when_auto_send_disabled(
    in_memory_training_db, monkeypatch,
):
    monkeypatch.setattr(timer_mod, "APPROVAL_MODE", True)

    chat_id = VIP_ID
    gen = 1
    reply_gen[chat_id] = gen
    history[chat_id] = [{"role": "user", "content": "hola"}]

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch(
            "services.llm.raw_call", new_callable=AsyncMock,
            return_value=('{"response": "hey", "confidence": 85, "topic": "saludo"}', None),
        ),
        patch("handlers.timer.notify_diana_approval", new_callable=AsyncMock) as mock_approval,
        patch("handlers.timer.deliver_vip_response", new_callable=AsyncMock) as mock_deliver,
        patch("handlers.timer.save_example", return_value=42),
    ):
        task = asyncio.create_task(
            timer_mod.auto_reply(AsyncMock(), chat_id, "vip", "bc_test", gen),
        )
        timers[chat_id] = task
        await task

    assert mock_approval.await_count == 1
    assert mock_deliver.await_count == 0


@pytest.mark.asyncio
async def test_au_auto_send_toggle_callback(
    make_mock_callback_update, make_context, make_user,
):
    admin = make_user(user_id=ADMIN_ID, username="diana_admin")
    update = make_mock_callback_update(
        data=f"au:auto_send:{VIP_ID}",
        user=admin,
    )

    result = await auth_users.handle_callback(update, make_context())
    assert result is True
    assert auth_users.is_auto_send_enabled(VIP_ID) is True
    update.callback_query.answer.assert_awaited_with("Envío automático activado")

    text = update.callback_query.edit_message_text.await_args[0][0]
    assert "🤖 Automático" in text
    kwargs = update.callback_query.edit_message_text.await_args[1]
    callbacks = [
        btn.callback_data
        for row in kwargs["reply_markup"].inline_keyboard
        for btn in row
    ]
    assert f"au:auto_send:{VIP_ID}" in callbacks