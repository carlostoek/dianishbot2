"""Unit tests for approval-draft regeneration and variant navigation."""

import pytest
from unittest.mock import AsyncMock, patch

import auth_users
import state
from handlers.callbacks import (
    MAX_APPROVAL_VARIANTS,
    _format_approval_text,
    handle_callback,
    handle_diana_correction,
    notify_diana_approval,
)
from services.llm import FAIL_ABORTED, LLMFailure, failure_label


ADMIN_ID = 555001
VIP_CHAT_ID = 777001


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
    yield
    state.awaiting_note.clear()
    state.awaiting_correction.clear()
    state.pending_approval.clear()
    state.reply_gen.clear()


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
async def test_notify_diana_approval_has_regen_and_nav_buttons():
    bot = AsyncMock()
    with patch("handlers.callbacks.DIANA_ADMIN_CHAT_ID", 12345):
        await notify_diana_approval(
            bot,
            example_id=9,
            username="testvip",
            context=[{"role": "user", "content": "hola"}],
            response="respuesta",
            confidence=90,
            topic="general",
            chat_id=VIP_CHAT_ID,
            gen=1,
        )
    markup = bot.send_message.await_args.kwargs["reply_markup"]
    callback_data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert len(callback_data) == 6
    assert "a:regen:9" in callback_data
    assert "a:prev:9" in callback_data
    assert "a:next:9" in callback_data
    texto = bot.send_message.await_args.kwargs["text"]
    assert "Borrador 1/1" in texto


@pytest.mark.asyncio
async def test_notify_shows_stale_banner_when_gen_differs():
    bot = AsyncMock()
    state.reply_gen[VIP_CHAT_ID] = 99
    with patch("handlers.callbacks.DIANA_ADMIN_CHAT_ID", 12345):
        await notify_diana_approval(
            bot,
            example_id=10,
            username="testvip",
            context=[{"role": "user", "content": "hola"}],
            response="respuesta",
            confidence=90,
            topic="general",
            chat_id=VIP_CHAT_ID,
            gen=1,
        )
    texto = bot.send_message.await_args.kwargs["text"]
    assert "obsoleto" in texto


@pytest.mark.asyncio
async def test_a_regen_appends_variant_and_selects_new(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 50
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    update = make_mock_callback_update(data=f"a:regen:{ex_id}", user=admin_user)

    with patch(
        "handlers.callbacks.get_diana_response",
        new_callable=AsyncMock,
        return_value=("nueva respuesta", 80, "saludo", False, "", None),
    ):
        await handle_callback(update, make_context())

    pending = state.pending_approval[ex_id]
    assert len(pending["variants"]) == 2
    assert pending["selected"] == 1
    assert pending["variants"][1]["response"] == "nueva respuesta"
    update.callback_query.edit_message_text.assert_awaited()


@pytest.mark.asyncio
async def test_a_regen_does_not_call_save_example(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 51
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    update = make_mock_callback_update(data=f"a:regen:{ex_id}", user=admin_user)

    with (
        patch(
            "handlers.callbacks.get_diana_response",
            new_callable=AsyncMock,
            return_value=("otra", 75, "general", False, "", None),
        ),
        patch("services.training.save_example") as mock_save,
    ):
        await handle_callback(update, make_context())

    mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_a_prev_next_navigation(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 52
    pending = pending_entry.copy()
    pending["variants"] = [
        {"response": "v1", "confidence": 90, "topic": "general"},
        {"response": "v2", "confidence": 85, "topic": "general"},
        {"response": "v3", "confidence": 80, "topic": "general"},
    ]
    pending["selected"] = 0
    state.pending_approval[ex_id] = pending

    update_prev = make_mock_callback_update(data=f"a:prev:{ex_id}", user=admin_user)
    await handle_callback(update_prev, make_context())
    assert state.pending_approval[ex_id]["selected"] == 0
    update_prev.callback_query.answer.assert_awaited_with("Primera opción")

    update_next = make_mock_callback_update(data=f"a:next:{ex_id}", user=admin_user)
    await handle_callback(update_next, make_context())
    assert state.pending_approval[ex_id]["selected"] == 1
    update_next.callback_query.edit_message_text.assert_awaited()

    update_next2 = make_mock_callback_update(data=f"a:next:{ex_id}", user=admin_user)
    await handle_callback(update_next2, make_context())
    assert state.pending_approval[ex_id]["selected"] == 2

    update_prev2 = make_mock_callback_update(data=f"a:prev:{ex_id}", user=admin_user)
    await handle_callback(update_prev2, make_context())
    assert state.pending_approval[ex_id]["selected"] == 1


@pytest.mark.asyncio
async def test_a_approve_sends_selected_variant(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 53
    pending = pending_entry.copy()
    pending["variants"] = [
        {"response": "primera", "confidence": 90, "topic": "general"},
        {"response": "segunda", "confidence": 85, "topic": "general"},
    ]
    pending["selected"] = 1
    state.pending_approval[ex_id] = pending
    state.reply_gen[VIP_CHAT_ID] = 1
    update = make_mock_callback_update(data=f"a:approve:{ex_id}", user=admin_user)

    with (
        patch(
            "handlers.callbacks.deliver_vip_response",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_deliver,
        patch("handlers.callbacks.update_rating"),
        patch("handlers.callbacks.update_bot_response"),
        patch("handlers.callbacks.schedule_memory_extract"),
    ):
        await handle_callback(update, make_context())

    mock_deliver.assert_awaited_once()
    assert mock_deliver.await_args.kwargs["text"] == "segunda"
    assert ex_id not in state.pending_approval


@pytest.mark.asyncio
async def test_a_approve_syncs_bot_response_when_not_first_variant(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 54
    pending = pending_entry.copy()
    pending["variants"] = [
        {"response": "primera", "confidence": 90, "topic": "general"},
        {"response": "segunda", "confidence": 85, "topic": "general"},
    ]
    pending["selected"] = 1
    state.pending_approval[ex_id] = pending
    state.reply_gen[VIP_CHAT_ID] = 1
    update = make_mock_callback_update(data=f"a:approve:{ex_id}", user=admin_user)

    with (
        patch(
            "handlers.callbacks.deliver_vip_response",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("handlers.callbacks.update_rating"),
        patch("handlers.callbacks.update_bot_response") as mock_update_bot,
        patch("handlers.callbacks.schedule_memory_extract"),
    ):
        await handle_callback(update, make_context())

    mock_update_bot.assert_called_once_with(ex_id, "segunda")


@pytest.mark.asyncio
async def test_a_fix_preview_uses_selected_variant(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 55
    pending = pending_entry.copy()
    pending["variants"] = [
        {"response": "primera opcion larga", "confidence": 90, "topic": "general"},
        {"response": "segunda opcion visible", "confidence": 85, "topic": "general"},
    ]
    pending["selected"] = 1
    state.pending_approval[ex_id] = pending
    update = make_mock_callback_update(data=f"a:fix:{ex_id}", user=admin_user)

    await handle_callback(update, make_context())

    edit_text = update.callback_query.edit_message_text.await_args[0][0]
    assert "segunda opcion visible" in edit_text


@pytest.mark.asyncio
async def test_a_regen_aborts_when_gen_stale(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 56
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 99
    update = make_mock_callback_update(data=f"a:regen:{ex_id}", user=admin_user)

    with patch(
        "handlers.callbacks.get_diana_response",
        new_callable=AsyncMock,
    ) as mock_llm:
        await handle_callback(update, make_context())

    mock_llm.assert_not_called()
    assert len(state.pending_approval[ex_id]["variants"]) == 1
    update.callback_query.answer.assert_awaited_with("Chat actualizado — borrador obsoleto")


@pytest.mark.asyncio
async def test_a_regen_ignores_while_regenerating(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 57
    pending = pending_entry.copy()
    pending["regenerating"] = True
    state.pending_approval[ex_id] = pending
    update = make_mock_callback_update(data=f"a:regen:{ex_id}", user=admin_user)

    with patch(
        "handlers.callbacks.get_diana_response",
        new_callable=AsyncMock,
    ) as mock_llm:
        await handle_callback(update, make_context())

    mock_llm.assert_not_called()
    update.callback_query.answer.assert_awaited_with("Ya generando...")


@pytest.mark.asyncio
async def test_a_note_still_preserves_pending_with_variants(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 58
    pending = pending_entry.copy()
    pending["variants"] = [
        {"response": "v1", "confidence": 90, "topic": "general"},
        {"response": "v2", "confidence": 85, "topic": "general"},
    ]
    pending["selected"] = 1
    state.pending_approval[ex_id] = pending
    update = make_mock_callback_update(data=f"a:note:{ex_id}", user=admin_user)

    await handle_callback(update, make_context())

    assert ex_id in state.pending_approval
    assert len(state.pending_approval[ex_id]["variants"]) == 2
    assert state.pending_approval[ex_id]["selected"] == 1


@pytest.mark.asyncio
async def test_a_regen_keeps_pending_when_no_response(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 59
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    update = make_mock_callback_update(data=f"a:regen:{ex_id}", user=admin_user)

    with patch(
        "handlers.callbacks.get_diana_response",
        new_callable=AsyncMock,
        return_value=(None, 0, "", False, "", None),
    ):
        await handle_callback(update, make_context())

    pending = state.pending_approval[ex_id]
    assert len(pending["variants"]) == 1
    assert pending["selected"] == 0
    assert pending["regenerating"] is False
    edit_text = update.callback_query.edit_message_text.await_args[0][0]
    assert "Regeneración falló" in edit_text


@pytest.mark.asyncio
async def test_a_approve_skips_update_bot_response_when_first_variant(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 60
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    update = make_mock_callback_update(data=f"a:approve:{ex_id}", user=admin_user)

    with (
        patch(
            "handlers.callbacks.deliver_vip_response",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("handlers.callbacks.update_rating"),
        patch("handlers.callbacks.update_bot_response") as mock_update_bot,
        patch("handlers.callbacks.schedule_memory_extract"),
    ):
        await handle_callback(update, make_context())

    mock_update_bot.assert_not_called()


@pytest.mark.asyncio
async def test_a_approve_blocked_while_regenerating(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 61
    pending = pending_entry.copy()
    pending["regenerating"] = True
    state.pending_approval[ex_id] = pending
    state.reply_gen[VIP_CHAT_ID] = 1
    update = make_mock_callback_update(data=f"a:approve:{ex_id}", user=admin_user)

    with patch(
        "handlers.callbacks.deliver_vip_response",
        new_callable=AsyncMock,
    ) as mock_deliver:
        await handle_callback(update, make_context())

    mock_deliver.assert_not_called()
    assert ex_id in state.pending_approval
    update.callback_query.answer.assert_awaited_with(
        "Espera a que termine la regeneración"
    )


@pytest.mark.asyncio
async def test_a_regen_skips_append_when_gen_stale_after_llm(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 62
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    update = make_mock_callback_update(data=f"a:regen:{ex_id}", user=admin_user)

    async def _llm_then_bump(*_args, **_kwargs):
        state.reply_gen[VIP_CHAT_ID] = 99
        return ("nueva", 80, "general", False, "", None)

    with patch(
        "handlers.callbacks.get_diana_response",
        new_callable=AsyncMock,
        side_effect=_llm_then_bump,
    ):
        await handle_callback(update, make_context())

    assert len(state.pending_approval[ex_id]["variants"]) == 1
    edit_text = update.callback_query.edit_message_text.await_args[0][0]
    assert "Regeneración cancelada" in edit_text


@pytest.mark.asyncio
async def test_a_regen_expired_calls_answer(make_mock_callback_update, make_context, admin_user):
    update = make_mock_callback_update(data="a:regen:999", user=admin_user)

    await handle_callback(update, make_context())

    update.callback_query.answer.assert_awaited()
    update.callback_query.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_regen_failure_shows_banner_in_message(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 63
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    update = make_mock_callback_update(data=f"a:regen:{ex_id}", user=admin_user)
    failure = LLMFailure("error_http_api", 3, "timeout")

    with patch(
        "handlers.callbacks.get_diana_response",
        new_callable=AsyncMock,
        return_value=(None, 0, "", False, "", failure),
    ):
        await handle_callback(update, make_context())

    edit_text = update.callback_query.edit_message_text.await_args[0][0]
    assert "Regeneración falló" in edit_text
    assert failure_label("error_http_api") in edit_text


@pytest.mark.asyncio
async def test_a_regen_fail_aborted_shows_stale_banner(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 64
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    update = make_mock_callback_update(data=f"a:regen:{ex_id}", user=admin_user)
    failure = LLMFailure(FAIL_ABORTED, 1, "nuevo mensaje del usuario")

    with patch(
        "handlers.callbacks.get_diana_response",
        new_callable=AsyncMock,
        return_value=(None, 0, "", False, "", failure),
    ):
        await handle_callback(update, make_context())

    edit_text = update.callback_query.edit_message_text.await_args[0][0]
    assert "llegó un mensaje nuevo" in edit_text


@pytest.mark.asyncio
async def test_a_approve_keeps_pending_on_delivery_failure(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 65
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    update = make_mock_callback_update(data=f"a:approve:{ex_id}", user=admin_user)

    with (
        patch(
            "handlers.callbacks.deliver_vip_response",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("handlers.callbacks.update_rating") as mock_rating,
    ):
        await handle_callback(update, make_context())

    assert ex_id in state.pending_approval
    mock_rating.assert_not_called()
    edit_text = update.callback_query.edit_message_text.await_args[0][0]
    assert "No enviado" in edit_text


@pytest.mark.asyncio
async def test_a_next_at_last_variant(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 66
    pending = pending_entry.copy()
    pending["variants"] = [
        {"response": "v1", "confidence": 90, "topic": "general"},
        {"response": "v2", "confidence": 85, "topic": "general"},
    ]
    pending["selected"] = 1
    state.pending_approval[ex_id] = pending
    update = make_mock_callback_update(data=f"a:next:{ex_id}", user=admin_user)

    await handle_callback(update, make_context())

    assert state.pending_approval[ex_id]["selected"] == 1
    update.callback_query.answer.assert_awaited_with("Última opción")


@pytest.mark.asyncio
async def test_a_fix_blocked_while_regenerating(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 67
    pending = pending_entry.copy()
    pending["regenerating"] = True
    state.pending_approval[ex_id] = pending
    update = make_mock_callback_update(data=f"a:fix:{ex_id}", user=admin_user)

    await handle_callback(update, make_context())

    assert ADMIN_ID not in state.awaiting_correction
    update.callback_query.answer.assert_awaited_once_with(
        "Espera a que termine la regeneración"
    )
    update.callback_query.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_note_blocked_while_regenerating(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 68
    pending = pending_entry.copy()
    pending["regenerating"] = True
    state.pending_approval[ex_id] = pending
    update = make_mock_callback_update(data=f"a:note:{ex_id}", user=admin_user)

    await handle_callback(update, make_context())

    assert ADMIN_ID not in state.awaiting_note
    update.callback_query.answer.assert_awaited_once_with(
        "Espera a que termine la regeneración"
    )


@pytest.mark.asyncio
async def test_a_approve_blocked_when_gen_stale(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 69
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 99
    update = make_mock_callback_update(data=f"a:approve:{ex_id}", user=admin_user)

    with patch(
        "handlers.callbacks.deliver_vip_response",
        new_callable=AsyncMock,
    ) as mock_deliver:
        await handle_callback(update, make_context())

    mock_deliver.assert_not_called()
    assert ex_id in state.pending_approval
    update.callback_query.answer.assert_awaited_with("Chat actualizado — borrador obsoleto")


@pytest.mark.asyncio
async def test_a_regen_exception_shows_failure_banner(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 70
    state.pending_approval[ex_id] = pending_entry.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    update = make_mock_callback_update(data=f"a:regen:{ex_id}", user=admin_user)

    with patch(
        "handlers.callbacks.get_diana_response",
        new_callable=AsyncMock,
        side_effect=RuntimeError("api down"),
    ):
        await handle_callback(update, make_context())

    assert len(state.pending_approval[ex_id]["variants"]) == 1
    assert state.pending_approval[ex_id]["regenerating"] is False
    edit_text = update.callback_query.edit_message_text.await_args[0][0]
    assert "error inesperado" in edit_text


@pytest.mark.asyncio
async def test_selected_variant_clamps_out_of_range_index(pending_entry):
    pending = pending_entry.copy()
    pending["selected"] = 99
    texto = _format_approval_text("testvip", [], pending)
    assert "Borrador 1/1" in texto


@pytest.mark.asyncio
async def test_handle_diana_correction_keeps_pending_on_delivery_failure(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 71
    state.pending_approval[ex_id] = pending_entry.copy()
    state.awaiting_correction[ADMIN_ID] = ex_id
    update = make_mock_update(text="texto corregido", user=admin_user)

    with (
        patch(
            "handlers.callbacks.deliver_vip_response",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("handlers.callbacks.update_rating") as mock_rating,
    ):
        await handle_diana_correction(update, make_context())

    assert ex_id in state.pending_approval
    mock_rating.assert_not_called()
    assert "sigue pendiente" in update.message.reply_text.await_args[0][0]


@pytest.mark.asyncio
async def test_handle_diana_correction_sends_selected_variant(
    make_mock_update, make_context, pending_entry, admin_user,
):
    ex_id = 72
    pending = pending_entry.copy()
    pending["variants"] = [
        {"response": "primera", "confidence": 90, "topic": "general"},
        {"response": "segunda", "confidence": 85, "topic": "general"},
    ]
    pending["selected"] = 1
    state.pending_approval[ex_id] = pending
    state.awaiting_correction[ADMIN_ID] = ex_id
    update = make_mock_update(text="correccion final", user=admin_user)

    with (
        patch(
            "handlers.callbacks.deliver_vip_response",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_deliver,
        patch("handlers.callbacks.update_rating") as mock_rating,
        patch("handlers.callbacks.schedule_memory_extract"),
    ):
        await handle_diana_correction(update, make_context())

    mock_deliver.assert_awaited_once()
    assert mock_deliver.await_args.kwargs["text"] == "correccion final"
    mock_rating.assert_called_once_with(ex_id, "corrected", "correccion final")
    assert ex_id not in state.pending_approval


@pytest.mark.asyncio
async def test_a_regen_blocked_at_max_variants(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 73
    pending = pending_entry.copy()
    pending["variants"] = [
        {"response": f"v{i}", "confidence": 90, "topic": "general"}
        for i in range(MAX_APPROVAL_VARIANTS)
    ]
    state.pending_approval[ex_id] = pending
    state.reply_gen[VIP_CHAT_ID] = 1
    update = make_mock_callback_update(data=f"a:regen:{ex_id}", user=admin_user)

    with patch(
        "handlers.callbacks.get_diana_response",
        new_callable=AsyncMock,
    ) as mock_llm:
        await handle_callback(update, make_context())

    mock_llm.assert_not_called()
    assert len(state.pending_approval[ex_id]["variants"]) == MAX_APPROVAL_VARIANTS
    update.callback_query.answer.assert_awaited_with("Máximo de variantes alcanzado")


@pytest.mark.asyncio
async def test_a_prev_blocked_while_regenerating(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 74
    pending = pending_entry.copy()
    pending["variants"] = [
        {"response": "v1", "confidence": 90, "topic": "general"},
        {"response": "v2", "confidence": 85, "topic": "general"},
    ]
    pending["selected"] = 1
    pending["regenerating"] = True
    state.pending_approval[ex_id] = pending
    update = make_mock_callback_update(data=f"a:prev:{ex_id}", user=admin_user)

    await handle_callback(update, make_context())

    assert state.pending_approval[ex_id]["selected"] == 1
    update.callback_query.answer.assert_awaited_with(
        "Espera a que termine la regeneración"
    )
    update.callback_query.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_next_blocked_while_regenerating(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    ex_id = 75
    pending = pending_entry.copy()
    pending["variants"] = [
        {"response": "v1", "confidence": 90, "topic": "general"},
        {"response": "v2", "confidence": 85, "topic": "general"},
    ]
    pending["selected"] = 0
    pending["regenerating"] = True
    state.pending_approval[ex_id] = pending
    update = make_mock_callback_update(data=f"a:next:{ex_id}", user=admin_user)

    await handle_callback(update, make_context())

    assert state.pending_approval[ex_id]["selected"] == 0
    update.callback_query.answer.assert_awaited_with(
        "Espera a que termine la regeneración"
    )
    update.callback_query.edit_message_text.assert_not_awaited()