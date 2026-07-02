"""Tests for admin slash commands and estado display."""

import pytest
from unittest.mock import AsyncMock, patch

import auth_users
import state
from handlers import admin_menu
from services import llm_settings


ADMIN_ID = 555002


@pytest.fixture(autouse=True)
def _configure(tmp_path):
    users_file = tmp_path / "authorized.json"
    llm_file = tmp_path / "llm.json"
    auth_users.configure(users_file=str(users_file), max_users=5, seed_user_ids=[])
    auth_users.set_admin_id(ADMIN_ID)
    llm_settings.configure(settings_file=str(llm_file))
    yield


@pytest.fixture
def admin_user(make_user):
    return make_user(user_id=ADMIN_ID, username="diana_admin", first_name="Diana")


@pytest.mark.asyncio
async def test_start_clears_reply_keyboard(make_mock_update, make_context, admin_user):
    update = make_mock_update(text="/start", user=admin_user)

    with patch.object(auth_users, "send_main_menu", new_callable=AsyncMock) as mock_menu:
        result = await admin_menu.handle_admin_input(update, make_context())

    assert result is True
    markup = update.message.reply_text.await_args.kwargs["reply_markup"]
    assert markup.remove_keyboard is True
    mock_menu.assert_awaited_once()


@pytest.mark.asyncio
async def test_menu_routes_to_inline(make_mock_update, make_context, admin_user):
    update = make_mock_update(text="/menu", user=admin_user)

    with patch.object(auth_users, "send_main_menu", new_callable=AsyncMock) as mock_send:
        result = await admin_menu.handle_admin_input(update, make_context())

    assert result is True
    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_estado_shows_runtime_llm(make_mock_update, make_context, admin_user):
    with patch.object(llm_settings, "has_api_key", return_value=True):
        llm_settings.set_provider("deepseek")
        llm_settings.set_model("deepseek-v4-flash")

    label = llm_settings.get_display_label()
    update = make_mock_update(text="/estado", user=admin_user)

    result = await admin_menu.handle_admin_input(update, make_context())

    assert result is True
    text = update.message.reply_text.await_args[0][0]
    assert label in text
    assert "*LLM:*" in text
    assert auth_users.ESTADO_TITLE in text


@pytest.mark.asyncio
async def test_estado_shows_health_lines(
    make_mock_update, make_context, admin_user, in_memory_training_db,
):
    state.connections["bc_a"] = ADMIN_ID
    state.connections["bc_b"] = ADMIN_ID

    with patch.object(llm_settings, "has_api_key", return_value=True):
        update = make_mock_update(text="/estado", user=admin_user)
        result = await admin_menu.handle_admin_input(update, make_context())

    assert result is True
    text = update.message.reply_text.await_args[0][0]
    assert "*BD:* OK" in text
    assert "*LLM key:* Configurada" in text
    assert "*Conexiones business:* 2" in text