"""Unit tests for approval-draft note button and handle_diana_note."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.memory import MemoryService

import auth_users
import state
from handlers.callbacks import (
    MAX_APPROVAL_VARIANTS,
    handle_callback,
    handle_diana_correction,
    handle_diana_note,
    notify_diana,
    notify_diana_approval,
)


ADMIN_ID = 555001
VIP_CHAT_ID = 777001
DRAFT_CHAT_ID = 12345
DRAFT_MESSAGE_ID = 999


@pytest.fixture(autouse=True)
def _set_admin(tmp_path):
    users_file = tmp_path / "authorized.json"
    auth_users.configure(
        users_file=str(users_file), max_users=5, seed_user_ids=[], admin_id=ADMIN_ID,
    )
    auth_users.set_admin_id(ADMIN_ID)
    yield


@pytest.fixture(autouse=True)
def _reset_state():
    state.awaiting_note.clear()
    state.awaiting_correction.clear()
    state.pending_approval.clear()
    state.reply_gen.clear()
    state.history.clear()
    yield
    state.awaiting_note.clear()
    state.awaiting_correction.clear()
    state.pending_approval.clear()
    state.reply_gen.clear()
    state.history.clear()


@pytest.fixture
def admin_user(make_user):
    return make_user(user_id=ADMIN_ID, username="diana_admin", first_name="Diana")


@pytest.fixture
def pending_entry():
    return {
        "chat_id": VIP_CHAT_ID,
        "bc_id": "bc_test",
        "username": "testvip",
        "gen": 1,
        "variants": [{"response": "hola", "confidence": 90, "topic": "general"}],
        "selected": 0,
        "regenerating": False,
    }


@pytest.mark.asyncio
async def test_a_note_sets_awaiting_note(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 42
    state.pending_approval[ex_id] = pending_entry.copy()
    update = make_mock_callback_update(data=f"a:note:{ex_id}", user=admin_user)

    await handle_callback(update, make_context())

    assert ADMIN_ID in state.awaiting_note
    assert state.awaiting_note[ADMIN_ID]["user_id"] == VIP_CHAT_ID
    assert state.awaiting_note[ADMIN_ID]["example_id"] == ex_id
    assert ex_id in state.pending_approval


@pytest.mark.asyncio
async def test_a_note_clears_awaiting_correction(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 43
    state.pending_approval[ex_id] = pending_entry.copy()
    state.awaiting_correction[ADMIN_ID] = ex_id
    update = make_mock_callback_update(data=f"a:note:{ex_id}", user=admin_user)

    await handle_callback(update, make_context())

    assert ADMIN_ID not in state.awaiting_correction
    assert ADMIN_ID in state.awaiting_note


@pytest.mark.asyncio
async def test_a_note_expired_draft(make_mock_callback_update, make_context, admin_user):
    update = make_mock_callback_update(data="a:note:999", user=admin_user)

    await handle_callback(update, make_context())

    update.callback_query.edit_message_text.assert_awaited_once()
    assert "expiró" in update.callback_query.edit_message_text.await_args[0][0]


@pytest.mark.asyncio
async def test_handle_diana_note_saves(make_mock_update, make_context, admin_user):
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True
    update = make_mock_update(text="Es muy sensible", user=admin_user)

    with patch("handlers.callbacks.llm_mod.memory_service", mock_svc):
        result = await handle_diana_note(update, make_context())

    assert result is True
    mock_svc.add_note.assert_called_once_with(VIP_CHAT_ID, "Es muy sensible")
    assert ADMIN_ID not in state.awaiting_note


@pytest.mark.asyncio
async def test_cancelar_nota_preserves_pending(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 44
    state.pending_approval[ex_id] = pending_entry.copy()
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
    }
    update = make_mock_update(text="/cancelar_nota", user=admin_user)

    result = await handle_diana_note(update, make_context())

    assert result is True
    assert ex_id in state.pending_approval
    assert ADMIN_ID not in state.awaiting_note


@pytest.mark.asyncio
async def test_notify_diana_no_note_button():
    bot = AsyncMock()
    with patch("handlers.callbacks.DIANA_ADMIN_CHAT_ID", 12345):
        await notify_diana(
            bot,
            example_id=8,
            username="testvip",
            context=[{"role": "user", "content": "hola"}],
            response="respuesta",
            confidence=40,
            topic="general",
        )
    markup = bot.send_message.await_args.kwargs["reply_markup"]
    callback_data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert "a:note" not in "".join(callback_data)
    assert all(d.startswith("t:") for d in callback_data)


@pytest.mark.asyncio
async def test_notify_diana_approval_has_note_button():
    bot = AsyncMock()
    with patch("handlers.callbacks.DIANA_ADMIN_CHAT_ID", 12345):
        await notify_diana_approval(
            bot,
            example_id=7,
            username="testvip",
            context=[{"role": "user", "content": "hola"}],
            response="respuesta",
            confidence=90,
            topic="general",
            chat_id=VIP_CHAT_ID,
            gen=1,
        )
    bot.send_message.assert_awaited_once()
    markup = bot.send_message.await_args.kwargs["reply_markup"]
    callback_data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert "a:note:7" in callback_data
    assert "a:regen:7" in callback_data
    assert len(callback_data) == 6


@pytest.mark.asyncio
async def test_handle_diana_note_memory_unavailable(
    make_mock_update, make_context, admin_user,
):
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
    }
    update = make_mock_update(text="Nota sin memoria", user=admin_user)

    with patch("handlers.callbacks.llm_mod.memory_service", None):
        result = await handle_diana_note(update, make_context())

    assert result is True
    update.message.reply_text.assert_awaited_once()
    assert "Memoria no disponible" in update.message.reply_text.await_args[0][0]
    assert ADMIN_ID in state.awaiting_note


@pytest.mark.asyncio
async def test_t_fix_clears_awaiting_note(make_mock_callback_update, make_context, admin_user):
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
    }
    update = make_mock_callback_update(data="t:fix:88", user=admin_user)

    await handle_callback(update, make_context())

    assert ADMIN_ID not in state.awaiting_note
    assert state.awaiting_correction[ADMIN_ID] == 88


@pytest.mark.asyncio
async def test_a_fix_clears_awaiting_note(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 45
    state.pending_approval[ex_id] = pending_entry.copy()
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
    }
    update = make_mock_callback_update(data=f"a:fix:{ex_id}", user=admin_user)

    await handle_callback(update, make_context())

    assert ADMIN_ID not in state.awaiting_note
    assert state.awaiting_correction[ADMIN_ID] == ex_id


@pytest.mark.asyncio
async def test_a_approve_clears_awaiting_note(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 46
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
    }
    update = make_mock_callback_update(data=f"a:approve:{ex_id}", user=admin_user)

    with (
        patch(
            "handlers.callbacks.deliver_vip_response",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("handlers.callbacks.update_rating"),
        patch("handlers.callbacks.schedule_memory_extract"),
    ):
        await handle_callback(update, make_context())

    assert ADMIN_ID not in state.awaiting_note


@pytest.mark.asyncio
async def test_t_good_clears_awaiting_note(make_mock_callback_update, make_context, admin_user):
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
    }
    update = make_mock_callback_update(data="t:good:77", user=admin_user)

    with patch("handlers.callbacks.update_rating"):
        await handle_callback(update, make_context())

    assert ADMIN_ID not in state.awaiting_note


@pytest.mark.asyncio
async def test_t_bad_clears_awaiting_note(make_mock_callback_update, make_context, admin_user):
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
    }
    update = make_mock_callback_update(data="t:bad:78", user=admin_user)

    with patch("handlers.callbacks.update_rating"):
        await handle_callback(update, make_context())

    assert ADMIN_ID not in state.awaiting_note


@pytest.mark.asyncio
async def test_handle_diana_note_not_awaiting_returns_false(
    make_mock_update, make_context, admin_user,
):
    update = make_mock_update(text="texto", user=admin_user)
    assert await handle_diana_note(update, make_context()) is False


@pytest.mark.asyncio
async def test_handle_diana_note_no_text_returns_false(make_context, admin_user):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = None
    update.message.from_user = admin_user
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
    }
    assert await handle_diana_note(update, make_context()) is False


@pytest.mark.asyncio
async def test_handle_diana_note_whitespace_keeps_state(
    make_mock_update, make_context, admin_user,
):
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = False
    update = make_mock_update(text="   ", user=admin_user)

    with patch("handlers.callbacks.llm_mod.memory_service", mock_svc):
        result = await handle_diana_note(update, make_context())

    assert result is True
    assert ADMIN_ID in state.awaiting_note
    assert "vacía" in update.message.reply_text.await_args[0][0]


@pytest.mark.asyncio
async def test_handle_diana_note_slash_command_fallthrough(
    make_mock_update, make_context, admin_user,
):
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
    }
    update = make_mock_update(text="/notas 123", user=admin_user)

    result = await handle_diana_note(update, make_context())

    assert result is False
    assert ADMIN_ID in state.awaiting_note


@pytest.mark.asyncio
async def test_cancelar_nota_with_bot_suffix(make_mock_update, make_context, admin_user):
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
    }
    update = make_mock_update(text="/cancelar_nota@DianaBot", user=admin_user)

    result = await handle_diana_note(update, make_context())

    assert result is True
    assert ADMIN_ID not in state.awaiting_note


@pytest.mark.asyncio
async def test_handle_diana_note_persist_error_keeps_state(
    make_mock_update, make_context, admin_user,
):
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.side_effect = RuntimeError("db locked")
    update = make_mock_update(text="Nota válida", user=admin_user)

    with patch("handlers.callbacks.llm_mod.memory_service", mock_svc):
        result = await handle_diana_note(update, make_context())

    assert result is True
    assert ADMIN_ID in state.awaiting_note
    assert "Error" in update.message.reply_text.await_args[0][0]


@pytest.mark.asyncio
async def test_handle_diana_note_no_message_returns_false(make_context):
    update = MagicMock()
    update.message = None
    assert await handle_diana_note(update, make_context()) is False


@pytest.mark.asyncio
async def test_handle_diana_correction_slash_fallthrough(
    make_mock_update, make_context, admin_user,
):
    state.awaiting_correction[ADMIN_ID] = 99
    update = make_mock_update(text="/notas 123", user=admin_user)

    result = await handle_diana_correction(update, make_context())

    assert result is False
    assert ADMIN_ID in state.awaiting_correction


@pytest.mark.asyncio
async def test_a_note_stores_draft_message_coords(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 80
    state.pending_approval[ex_id] = pending_entry.copy()
    update = make_mock_callback_update(data=f"a:note:{ex_id}", user=admin_user)
    update.callback_query.message = MagicMock()
    update.callback_query.message.chat_id = DRAFT_CHAT_ID
    update.callback_query.message.message_id = DRAFT_MESSAGE_ID

    await handle_callback(update, make_context())

    assert ADMIN_ID in state.awaiting_note
    assert state.awaiting_note[ADMIN_ID]["draft_chat_id"] == DRAFT_CHAT_ID
    assert state.awaiting_note[ADMIN_ID]["draft_message_id"] == DRAFT_MESSAGE_ID


@pytest.mark.asyncio
async def test_handle_diana_note_from_draft_regens_and_restores_ui(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 81
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True
    update = make_mock_update(text="Prefiere mensajes cortos", user=admin_user)
    ctx = make_context()

    with (
        patch("handlers.callbacks.llm_mod.memory_service", mock_svc),
        patch(
            "handlers.callbacks.get_diana_response",
            new_callable=AsyncMock,
            return_value=("respuesta con nota", 85, "general", False, "", None),
        ) as mock_llm,
    ):
        result = await handle_diana_note(update, ctx)

    assert result is True
    mock_llm.assert_awaited_once()
    pending = state.pending_approval[ex_id]
    assert len(pending["variants"]) == 2
    assert ex_id in state.pending_approval
    ctx.bot.edit_message_text.assert_awaited_once()
    edit_kwargs = ctx.bot.edit_message_text.await_args.kwargs
    assert edit_kwargs["chat_id"] == DRAFT_CHAT_ID
    assert edit_kwargs["message_id"] == DRAFT_MESSAGE_ID
    assert edit_kwargs["reply_markup"] is not None
    assert "Borrador regenerado" in update.message.reply_text.await_args[0][0]


@pytest.mark.asyncio
async def test_handle_diana_note_from_draft_selects_new_variant(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 82
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True
    update = make_mock_update(text="Nota de prueba", user=admin_user)

    with (
        patch("handlers.callbacks.llm_mod.memory_service", mock_svc),
        patch(
            "handlers.callbacks.get_diana_response",
            new_callable=AsyncMock,
            return_value=("nueva variante", 80, "general", False, "", None),
        ),
    ):
        await handle_diana_note(update, make_context())

    pending = state.pending_approval[ex_id]
    assert pending["selected"] == len(pending["variants"]) - 1


@pytest.mark.asyncio
async def test_handle_diana_note_from_draft_regen_failure_restores_ui(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 83
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True
    update = make_mock_update(text="Nota válida", user=admin_user)
    ctx = make_context()

    with (
        patch("handlers.callbacks.llm_mod.memory_service", mock_svc),
        patch(
            "handlers.callbacks.get_diana_response",
            new_callable=AsyncMock,
            return_value=(None, 0, "", False, "", None),
        ),
    ):
        await handle_diana_note(update, ctx)

    pending = state.pending_approval[ex_id]
    assert len(pending["variants"]) == 1
    edit_text = ctx.bot.edit_message_text.await_args[0][0]
    assert "Regeneración falló" in edit_text
    assert "No se pudo regenerar" in update.message.reply_text.await_args[0][0]


@pytest.mark.asyncio
async def test_handle_diana_note_from_draft_stale_gen_no_append(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 84
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 99
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True
    update = make_mock_update(text="Nota con gen stale", user=admin_user)
    ctx = make_context()

    with (
        patch("handlers.callbacks.llm_mod.memory_service", mock_svc),
        patch(
            "handlers.callbacks.get_diana_response",
            new_callable=AsyncMock,
        ) as mock_llm,
    ):
        await handle_diana_note(update, ctx)

    mock_llm.assert_not_called()
    assert len(state.pending_approval[ex_id]["variants"]) == 1
    edit_text = ctx.bot.edit_message_text.await_args[0][0]
    assert "obsoleto" in edit_text


@pytest.mark.asyncio
async def test_handle_diana_note_standalone_no_regen(
    make_mock_update, make_context, admin_user,
):
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True
    update = make_mock_update(text="Nota standalone", user=admin_user)
    ctx = make_context()

    with (
        patch("handlers.callbacks.llm_mod.memory_service", mock_svc),
        patch(
            "handlers.callbacks.get_diana_response",
            new_callable=AsyncMock,
        ) as mock_llm,
    ):
        await handle_diana_note(update, ctx)

    mock_llm.assert_not_called()
    ctx.bot.edit_message_text.assert_not_awaited()
    assert "próxima respuesta" in update.message.reply_text.await_args[0][0]


@pytest.mark.asyncio
async def test_handle_diana_note_expired_draft_no_regen(
    make_mock_update, make_context, admin_user,
):
    ex_id = 85
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True
    update = make_mock_update(text="Nota con draft expirado", user=admin_user)
    ctx = make_context()

    with (
        patch("handlers.callbacks.llm_mod.memory_service", mock_svc),
        patch(
            "handlers.callbacks.get_diana_response",
            new_callable=AsyncMock,
        ) as mock_llm,
    ):
        await handle_diana_note(update, ctx)

    mock_llm.assert_not_called()
    ctx.bot.edit_message_text.assert_awaited_once()
    edit_kwargs = ctx.bot.edit_message_text.await_args.kwargs
    assert edit_kwargs["chat_id"] == DRAFT_CHAT_ID
    assert edit_kwargs["message_id"] == DRAFT_MESSAGE_ID
    assert edit_kwargs["reply_markup"] is None
    assert "expiró" in ctx.bot.edit_message_text.await_args[0][0]
    assert "ya no está pendiente" in update.message.reply_text.await_args[0][0]


@pytest.mark.asyncio
async def test_handle_diana_note_from_draft_max_variants_restores_ui(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 87
    pending = pending_entry.copy()
    pending["variants"] = [
        {"response": f"v{i}", "confidence": 90, "topic": "general"}
        for i in range(MAX_APPROVAL_VARIANTS)
    ]
    state.pending_approval[ex_id] = pending
    state.reply_gen[VIP_CHAT_ID] = 1
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True
    update = make_mock_update(text="Nota al tope de variantes", user=admin_user)
    ctx = make_context()

    with (
        patch("handlers.callbacks.llm_mod.memory_service", mock_svc),
        patch(
            "handlers.callbacks.get_diana_response",
            new_callable=AsyncMock,
        ) as mock_llm,
    ):
        await handle_diana_note(update, ctx)

    mock_llm.assert_not_called()
    assert len(state.pending_approval[ex_id]["variants"]) == MAX_APPROVAL_VARIANTS
    assert ex_id in state.pending_approval
    ctx.bot.edit_message_text.assert_awaited_once()
    edit_kwargs = ctx.bot.edit_message_text.await_args.kwargs
    assert edit_kwargs["chat_id"] == DRAFT_CHAT_ID
    assert edit_kwargs["message_id"] == DRAFT_MESSAGE_ID
    assert edit_kwargs["reply_markup"] is not None
    reply_text = update.message.reply_text.await_args[0][0]
    assert "Máximo de variantes" in reply_text
    assert "sin regenerar" in reply_text


@pytest.mark.asyncio
async def test_cancelar_nota_restores_draft_ui(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 86
    state.pending_approval[ex_id] = pending_entry.copy()
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    update = make_mock_update(text="/cancelar_nota", user=admin_user)
    ctx = make_context()

    with patch(
        "handlers.callbacks.get_diana_response",
        new_callable=AsyncMock,
    ) as mock_llm:
        result = await handle_diana_note(update, ctx)

    assert result is True
    mock_llm.assert_not_called()
    ctx.bot.edit_message_text.assert_awaited_once()
    edit_kwargs = ctx.bot.edit_message_text.await_args.kwargs
    assert edit_kwargs["chat_id"] == DRAFT_CHAT_ID
    assert edit_kwargs["message_id"] == DRAFT_MESSAGE_ID
    edit_text = ctx.bot.edit_message_text.await_args[0][0]
    assert "Borrador 1/1" in edit_text
    assert edit_kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_handle_diana_note_expired_during_regen_no_crash(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 88
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True
    update = make_mock_update(text="Nota con draft que expira", user=admin_user)
    ctx = make_context()

    async def _regen_then_expire(*_args, **_kwargs):
        state.pending_approval.pop(ex_id, None)
        return ("nueva", 80, "general", False, "", None)

    with (
        patch("handlers.callbacks.llm_mod.memory_service", mock_svc),
        patch(
            "handlers.callbacks.get_diana_response",
            new_callable=AsyncMock,
            side_effect=_regen_then_expire,
        ),
    ):
        result = await handle_diana_note(update, ctx)

    assert result is True
    assert ADMIN_ID not in state.awaiting_note
    assert "ya no está pendiente" in update.message.reply_text.await_args[0][0]
    ctx.bot.edit_message_text.assert_awaited_once()
    assert "expiró" in ctx.bot.edit_message_text.await_args[0][0]
    assert ctx.bot.edit_message_text.await_args.kwargs["reply_markup"] is None


@pytest.mark.asyncio
async def test_handle_diana_note_edit_failure_still_confirms(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 89
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True
    update = make_mock_update(text="Nota con edit fallido", user=admin_user)
    ctx = make_context()
    ctx.bot.edit_message_text.side_effect = RuntimeError("message not found")

    with (
        patch("handlers.callbacks.llm_mod.memory_service", mock_svc),
        patch(
            "handlers.callbacks.get_diana_response",
            new_callable=AsyncMock,
            return_value=("respuesta ok", 85, "general", False, "", None),
        ),
    ):
        result = await handle_diana_note(update, ctx)

    assert result is True
    assert ADMIN_ID not in state.awaiting_note
    reply = update.message.reply_text.await_args[0][0]
    assert "Borrador regenerado" not in reply
    assert "No se pudo actualizar" in reply
    assert "Regenerar" in reply


@pytest.mark.asyncio
async def test_handle_diana_note_stale_blocked_reason_copy(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 90
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 99
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True
    update = make_mock_update(text="Nota stale", user=admin_user)

    with patch("handlers.callbacks.llm_mod.memory_service", mock_svc):
        await handle_diana_note(update, make_context())

    reply = update.message.reply_text.await_args[0][0]
    assert "sin regenerar" in reply
    assert "mensaje más reciente" in reply


@pytest.mark.asyncio
async def test_handle_diana_note_regenerating_blocked_reason_copy(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 91
    pending = pending_entry.copy()
    pending["regenerating"] = True
    state.pending_approval[ex_id] = pending
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True
    update = make_mock_update(text="Nota con regen en curso", user=admin_user)

    with (
        patch("handlers.callbacks.llm_mod.memory_service", mock_svc),
        patch(
            "handlers.callbacks.get_diana_response",
            new_callable=AsyncMock,
        ) as mock_llm,
    ):
        await handle_diana_note(update, make_context())

    mock_llm.assert_not_called()
    reply = update.message.reply_text.await_args[0][0]
    assert "regeneración en curso" in reply
    assert "Regenerar" in reply


@pytest.mark.asyncio
async def test_handle_diana_note_add_note_before_regen(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 92
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True
    update = make_mock_update(text="Orden de llamadas", user=admin_user)
    call_order = []

    async def _track_llm(*_args, **_kwargs):
        call_order.append("llm")
        return ("resp", 80, "general", False, "", None)

    def _track_add_note(*_args, **_kwargs):
        call_order.append("add_note")
        return True

    mock_svc.add_note.side_effect = _track_add_note

    with (
        patch("handlers.callbacks.llm_mod.memory_service", mock_svc),
        patch(
            "handlers.callbacks.get_diana_response",
            new_callable=AsyncMock,
            side_effect=_track_llm,
        ),
    ):
        await handle_diana_note(update, make_context())

    assert call_order == ["add_note", "llm"]


@pytest.mark.asyncio
async def test_a_note_to_save_e2e_chain(
    make_mock_callback_update, make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 93
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    cb_update = make_mock_callback_update(data=f"a:note:{ex_id}", user=admin_user)
    cb_update.callback_query.message = MagicMock()
    cb_update.callback_query.message.chat_id = DRAFT_CHAT_ID
    cb_update.callback_query.message.message_id = DRAFT_MESSAGE_ID
    ctx = make_context()
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True

    await handle_callback(cb_update, ctx)
    assert ADMIN_ID in state.awaiting_note

    msg_update = make_mock_update(text="Cadena e2e nota", user=admin_user)
    with (
        patch("handlers.callbacks.llm_mod.memory_service", mock_svc),
        patch(
            "handlers.callbacks.get_diana_response",
            new_callable=AsyncMock,
            return_value=("respuesta e2e", 88, "general", False, "", None),
        ),
    ):
        await handle_diana_note(msg_update, ctx)

    assert ADMIN_ID not in state.awaiting_note
    assert len(state.pending_approval[ex_id]["variants"]) == 2
    ctx.bot.edit_message_text.assert_awaited()


@pytest.mark.asyncio
async def test_handle_diana_note_partial_coords_standalone_copy(
    make_mock_update, make_context, admin_user,
):
    ex_id = 94
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True
    update = make_mock_update(text="Coords parciales", user=admin_user)
    ctx = make_context()

    with (
        patch("handlers.callbacks.llm_mod.memory_service", mock_svc),
        patch(
            "handlers.callbacks.get_diana_response",
            new_callable=AsyncMock,
        ) as mock_llm,
    ):
        await handle_diana_note(update, ctx)

    mock_llm.assert_not_called()
    ctx.bot.edit_message_text.assert_not_awaited()
    assert "próxima respuesta" in update.message.reply_text.await_args[0][0]


@pytest.mark.asyncio
async def test_cancelar_nota_expired_draft_copy(
    make_mock_update, make_context, admin_user,
):
    ex_id = 95
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    update = make_mock_update(text="/cancelar_nota", user=admin_user)
    ctx = make_context()

    result = await handle_diana_note(update, ctx)

    assert result is True
    ctx.bot.edit_message_text.assert_awaited_once()
    assert "expiró" in ctx.bot.edit_message_text.await_args[0][0]
    assert ctx.bot.edit_message_text.await_args.kwargs["reply_markup"] is None
    assert "ya expiró" in update.message.reply_text.await_args[0][0]


@pytest.mark.asyncio
async def test_cancelar_nota_edit_failure_still_replies(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 96
    state.pending_approval[ex_id] = pending_entry.copy()
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    update = make_mock_update(text="/cancelar_nota", user=admin_user)
    ctx = make_context()
    ctx.bot.edit_message_text.side_effect = RuntimeError("edit failed")

    result = await handle_diana_note(update, ctx)

    assert result is True
    assert "sigue pendiente" in update.message.reply_text.await_args[0][0]


@pytest.mark.asyncio
async def test_cancelar_nota_while_regenerating_skips_edit(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 97
    pending = pending_entry.copy()
    pending["regenerating"] = True
    state.pending_approval[ex_id] = pending
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    update = make_mock_update(text="/cancelar_nota", user=admin_user)
    ctx = make_context()

    result = await handle_diana_note(update, ctx)

    assert result is True
    ctx.bot.edit_message_text.assert_not_awaited()
    assert "regeneración" in update.message.reply_text.await_args[0][0]


@pytest.mark.asyncio
async def test_a_note_blocks_overwrite_existing_awaiting(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 98
    state.pending_approval[ex_id] = pending_entry.copy()
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "otrovip",
        "example_id": 1,
    }
    update = make_mock_callback_update(data=f"a:note:{ex_id}", user=admin_user)

    await handle_callback(update, make_context())

    assert state.awaiting_note[ADMIN_ID]["example_id"] == 1
    update.callback_query.answer.assert_awaited_with(
        "Ya estás escribiendo una nota. Termina o usa /cancelar_nota."
    )


@pytest.mark.asyncio
async def test_a_note_warns_when_reply_gen_stale(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 99
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 99
    update = make_mock_callback_update(data=f"a:note:{ex_id}", user=admin_user)

    await handle_callback(update, make_context())

    edit_text = update.callback_query.edit_message_text.await_args[0][0]
    assert "mensaje más reciente" in edit_text


@pytest.mark.asyncio
async def test_handle_diana_note_clears_awaiting_note_after_save(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 100
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True
    update = make_mock_update(text="Limpia awaiting", user=admin_user)

    with (
        patch("handlers.callbacks.llm_mod.memory_service", mock_svc),
        patch(
            "handlers.callbacks.get_diana_response",
            new_callable=AsyncMock,
            return_value=("ok", 80, "general", False, "", None),
        ),
    ):
        await handle_diana_note(update, make_context())

    assert ADMIN_ID not in state.awaiting_note


@pytest.mark.asyncio
async def test_handle_diana_correction_blocked_while_regenerating(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 101
    pending = pending_entry.copy()
    pending["regenerating"] = True
    state.pending_approval[ex_id] = pending
    state.awaiting_correction[ADMIN_ID] = ex_id
    update = make_mock_update(text="correccion bloqueada", user=admin_user)

    with patch(
        "handlers.callbacks.deliver_vip_response",
        new_callable=AsyncMock,
    ) as mock_deliver:
        result = await handle_diana_correction(update, make_context())

    assert result is True
    mock_deliver.assert_not_called()
    assert ADMIN_ID in state.awaiting_correction
    assert "regeneración" in update.message.reply_text.await_args[0][0]


@pytest.mark.asyncio
async def test_a_approve_restores_cross_draft_note_prompt(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    note_ex_id = 102
    approve_ex_id = 103
    state.pending_approval[note_ex_id] = pending_entry.copy()
    state.pending_approval[approve_ex_id] = {
        **pending_entry,
        "username": "otrovip",
    }
    state.reply_gen[VIP_CHAT_ID] = 1
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": note_ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    update = make_mock_callback_update(data=f"a:approve:{approve_ex_id}", user=admin_user)
    ctx = make_context()

    with (
        patch(
            "handlers.callbacks.deliver_vip_response",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("handlers.callbacks.update_rating"),
        patch("handlers.callbacks.schedule_memory_extract"),
    ):
        await handle_callback(update, ctx)

    assert ADMIN_ID not in state.awaiting_note
    assert note_ex_id in state.pending_approval
    ctx.bot.edit_message_text.assert_awaited()
    cross_edit = [
        c for c in ctx.bot.edit_message_text.await_args_list
        if c.kwargs.get("chat_id") == DRAFT_CHAT_ID
        and c.kwargs.get("message_id") == DRAFT_MESSAGE_ID
    ]
    assert len(cross_edit) == 1
    assert "Borrador 1/1" in cross_edit[0][0][0]


@pytest.mark.asyncio
async def test_t_good_clears_expired_cross_draft_note_prompt(
    make_mock_callback_update, make_context, admin_user,
):
    ex_id = 104
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    update = make_mock_callback_update(data="t:good:200", user=admin_user)
    ctx = make_context()

    with patch("handlers.callbacks.update_rating"):
        await handle_callback(update, ctx)

    assert ADMIN_ID not in state.awaiting_note
    ctx.bot.edit_message_text.assert_awaited_once()
    assert "expiró" in ctx.bot.edit_message_text.await_args[0][0]
    assert ctx.bot.edit_message_text.await_args.kwargs["reply_markup"] is None


@pytest.mark.asyncio
async def test_handle_diana_note_post_pop_error_sends_recovery(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 105
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    state.awaiting_note[ADMIN_ID] = {
        "user_id": VIP_CHAT_ID,
        "username": "testvip",
        "example_id": ex_id,
        "draft_chat_id": DRAFT_CHAT_ID,
        "draft_message_id": DRAFT_MESSAGE_ID,
    }
    mock_svc = MagicMock(spec=MemoryService)
    mock_svc.add_note.return_value = True
    update = make_mock_update(text="Nota con error post-pop", user=admin_user)

    with (
        patch("handlers.callbacks.llm_mod.memory_service", mock_svc),
        patch(
            "handlers.callbacks._regen_approval_variant",
            new_callable=AsyncMock,
            side_effect=RuntimeError("unexpected"),
        ),
    ):
        result = await handle_diana_note(update, make_context())

    assert result is True
    assert ADMIN_ID not in state.awaiting_note
    reply = update.message.reply_text.await_args[0][0]
    assert "Hubo un error" in reply
    assert "Regenerar" in reply