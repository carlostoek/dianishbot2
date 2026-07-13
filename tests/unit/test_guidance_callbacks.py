"""Unit tests for guidance consult callbacks (g:) and free-text capture."""

import pytest
from unittest.mock import AsyncMock, patch

import auth_users
import state
from handlers.callbacks import handle_callback
from handlers.callbacks.guidance import (
    handle_diana_guidance_answer,
    handle_guidance_action,
    notify_diana_guidance,
)


ADMIN_ID = 555001
VIP_CHAT_ID = 777001
GID = 10


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
    state.pending_guidance.clear()
    state.awaiting_guidance_answer.clear()
    state.awaiting_note.clear()
    state.awaiting_correction.clear()
    state.pending_approval.clear()
    state.reply_gen.clear()
    state.history.clear()
    yield
    state.pending_guidance.clear()
    state.awaiting_guidance_answer.clear()
    state.awaiting_note.clear()
    state.awaiting_correction.clear()
    state.pending_approval.clear()
    state.reply_gen.clear()
    state.history.clear()


@pytest.fixture
def admin_user(make_user):
    return make_user(user_id=ADMIN_ID, username="diana_admin", first_name="Diana")


@pytest.fixture
def guidance_pending():
    return {
        "chat_id": VIP_CHAT_ID,
        "bc_id": "bc_test",
        "username": "testvip",
        "gen": 1,
        "topic": "limites_contenido",
        "gap_question": "¿Puedo ofrecer videollamada fuera de tarifa?",
        "draft_response": "Mmm déjame pensarlo un toque",
        "confidence": 65,
        "created_at": "2026-07-13T12:00:00",
    }


def _seed_pending(guidance_pending):
    state.pending_guidance[GID] = guidance_pending.copy()
    state.reply_gen[VIP_CHAT_ID] = 1
    state.history[VIP_CHAT_ID] = [
        {"role": "user", "content": "me haces una videollamada privada?"},
    ]


# ── notify UI ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notify_diana_guidance_has_g_buttons(bot, guidance_pending):
    with patch("handlers.callbacks.guidance.DIANA_ADMIN_CHAT_ID", ADMIN_ID):
        await notify_diana_guidance(
            bot,
            guidance_id=GID,
            pending=guidance_pending,
            context=[{"role": "user", "content": "hola"}],
        )
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == ADMIN_ID
    assert "zona gris" in kwargs["text"].lower() or "criterio" in kwargs["text"].lower()
    assert "videollamada" in kwargs["text"] or guidance_pending["gap_question"] in kwargs["text"]
    markup = kwargs["reply_markup"]
    callbacks = [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
    ]
    assert f"g:answer:{GID}" in callbacks
    assert f"g:use_draft:{GID}" in callbacks
    assert f"g:skip:{GID}" in callbacks


# ── g:answer ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_g_answer_arms_free_text(
    make_mock_callback_update, make_context, admin_user, guidance_pending,
):
    _seed_pending(guidance_pending)
    update = make_mock_callback_update(data=f"g:answer:{GID}", user=admin_user)

    handled = await handle_callback(update, make_context())

    assert handled is True
    assert state.awaiting_guidance_answer[ADMIN_ID] == GID
    assert GID in state.pending_guidance
    update.callback_query.edit_message_text.assert_awaited()
    text = update.callback_query.edit_message_text.await_args[0][0]
    assert "escrib" in text.lower() or "respuesta" in text.lower() or "criterio" in text.lower()


@pytest.mark.asyncio
async def test_g_answer_clears_note_and_correction(
    make_mock_callback_update, make_context, admin_user, guidance_pending,
):
    _seed_pending(guidance_pending)
    state.awaiting_note[ADMIN_ID] = {"user_id": VIP_CHAT_ID, "username": "x"}
    state.awaiting_correction[ADMIN_ID] = 99
    update = make_mock_callback_update(data=f"g:answer:{GID}", user=admin_user)

    with patch(
        "handlers.callbacks.shared._clear_awaiting_note_with_prompt_restore",
        new_callable=AsyncMock,
    ):
        await handle_callback(update, make_context())

    assert ADMIN_ID not in state.awaiting_note
    assert ADMIN_ID not in state.awaiting_correction
    assert state.awaiting_guidance_answer[ADMIN_ID] == GID


# ── g:skip ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_g_skip_closes_without_vip_send(
    make_mock_callback_update, make_context, admin_user, guidance_pending,
    in_memory_training_db,
):
    _seed_pending(guidance_pending)
    update = make_mock_callback_update(data=f"g:skip:{GID}", user=admin_user)

    with (
        patch("handlers.timer.deliver_vip_response", new_callable=AsyncMock) as mock_deliver,
        patch("handlers.timer.save_example") as mock_save,
    ):
        await handle_callback(update, make_context())

    assert GID not in state.pending_guidance
    mock_deliver.assert_not_awaited()
    mock_save.assert_not_called()
    update.callback_query.edit_message_text.assert_awaited()


@pytest.mark.asyncio
async def test_g_skip_marks_request_skipped(
    make_mock_callback_update, make_context, admin_user, guidance_pending,
    in_memory_training_db,
):
    from services import knowledge
    real_gid = knowledge.create_guidance_request(
        chat_id=VIP_CHAT_ID,
        username="testvip",
        topic="limites",
        gap_question="¿X?",
        draft_response="draft",
    )
    state.pending_guidance[real_gid] = {
        **guidance_pending,
        "chat_id": VIP_CHAT_ID,
    }
    update = make_mock_callback_update(data=f"g:skip:{real_gid}", user=admin_user)

    await handle_callback(update, make_context())

    req = knowledge.get_guidance_request(real_gid)
    assert req["status"] == "skipped"
    assert real_gid not in state.pending_guidance


# ── g:use_draft ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_g_use_draft_supervised_enters_approval(
    make_mock_callback_update, make_context, admin_user, guidance_pending,
    in_memory_training_db,
):
    from services import knowledge
    real_gid = knowledge.create_guidance_request(
        chat_id=VIP_CHAT_ID,
        username="testvip",
        topic="limites",
        gap_question="¿X?",
        draft_response="Mmm déjame pensarlo un toque",
    )
    state.pending_guidance[real_gid] = {
        **guidance_pending,
        "draft_response": "Mmm déjame pensarlo un toque",
        "confidence": 65,
        "topic": "limites",
        "gen": 1,
    }
    state.reply_gen[VIP_CHAT_ID] = 1
    state.history[VIP_CHAT_ID] = [{"role": "user", "content": "q"}]
    update = make_mock_callback_update(data=f"g:use_draft:{real_gid}", user=admin_user)

    with (
        patch("handlers.timer._is_supervised_for_chat", return_value=True),
        patch("handlers.timer.notify_diana_approval", new_callable=AsyncMock) as mock_notify,
        patch("handlers.timer.deliver_vip_response", new_callable=AsyncMock) as mock_deliver,
        patch("handlers.timer.save_example", return_value=501) as mock_save,
    ):
        await handle_callback(update, make_context())

    mock_save.assert_called_once()
    mock_notify.assert_awaited_once()
    mock_deliver.assert_not_awaited()
    assert real_gid not in state.pending_guidance
    assert 501 in state.pending_approval
    req = knowledge.get_guidance_request(real_gid)
    assert req["status"] == "skipped"


@pytest.mark.asyncio
async def test_g_use_draft_autonomous_delivers(
    make_mock_callback_update, make_context, admin_user, guidance_pending,
    in_memory_training_db,
):
    from services import knowledge
    real_gid = knowledge.create_guidance_request(
        chat_id=VIP_CHAT_ID,
        username="testvip",
        topic="limites",
        gap_question="¿X?",
        draft_response="ok",
    )
    state.pending_guidance[real_gid] = {
        **guidance_pending,
        "draft_response": "ok",
        "confidence": 90,
        "topic": "limites",
        "gen": 1,
    }
    state.reply_gen[VIP_CHAT_ID] = 1
    update = make_mock_callback_update(data=f"g:use_draft:{real_gid}", user=admin_user)

    with (
        patch("handlers.timer._is_supervised_for_chat", return_value=False),
        patch("handlers.timer.notify_diana_approval", new_callable=AsyncMock) as mock_notify,
        patch(
            "handlers.timer.deliver_vip_response",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_deliver,
        patch("handlers.timer.save_example", return_value=502),
    ):
        await handle_callback(update, make_context())

    mock_deliver.assert_awaited_once()
    mock_notify.assert_not_awaited()
    assert real_gid not in state.pending_guidance


# ── free-text answer ───────────────────────────────────────


@pytest.mark.asyncio
async def test_free_text_saves_policy_and_regens(
    make_mock_update, make_context, admin_user, guidance_pending,
    in_memory_training_db,
):
    """WU3: free-text → distill → policy → regen → normal draft path."""
    from services import knowledge
    real_gid = knowledge.create_guidance_request(
        chat_id=VIP_CHAT_ID,
        username="testvip",
        topic="limites",
        gap_question="¿X?",
        draft_response="draft tentativo",
    )
    state.pending_guidance[real_gid] = {
        **guidance_pending,
        "draft_response": "draft tentativo",
        "confidence": 70,
        "topic": "limites",
        "gen": 1,
    }
    state.reply_gen[VIP_CHAT_ID] = 1
    state.awaiting_guidance_answer[ADMIN_ID] = real_gid
    update = make_mock_update(
        text="No ofrezcas videollamada fuera de tarifa. Redirigí a packs.",
        user=admin_user,
    )
    distilled = {
        "topic": "limites",
        "policy_summary": "No ofrezcas videollamada fuera de tarifa.",
        "example_response": "no lo hago",
        "keywords": ["videollamada"],
        "priority": 100,
        "degraded": False,
    }
    regen = ("respuesta regenerada", 88, "limites", False, "", None)

    with (
        patch(
            "handlers.callbacks.guidance.knowledge.distill_guidance",
            new_callable=AsyncMock,
            return_value=distilled,
        ),
        patch(
            "handlers.callbacks.guidance.get_diana_response",
            new_callable=AsyncMock,
            return_value=regen,
        ),
        patch("handlers.timer._is_supervised_for_chat", return_value=True),
        patch("handlers.timer.notify_diana_approval", new_callable=AsyncMock) as mock_notify,
        patch("handlers.timer.save_example", return_value=600) as mock_save,
        patch("handlers.timer.deliver_vip_response", new_callable=AsyncMock) as mock_deliver,
    ):
        handled = await handle_diana_guidance_answer(update, make_context())

    assert handled is True
    assert ADMIN_ID not in state.awaiting_guidance_answer
    assert real_gid not in state.pending_guidance
    req = knowledge.get_guidance_request(real_gid)
    assert req["status"] == "answered"
    assert "videollamada" in (req["diana_answer_raw"] or "")
    assert req["policy_id"] is not None
    mock_save.assert_called_once()
    assert mock_save.call_args[0][3] == "respuesta regenerada"
    mock_notify.assert_awaited_once()
    mock_deliver.assert_not_awaited()


# ── mutual exclusion from note/fix ────────────────────────


@pytest.mark.asyncio
async def test_a_note_clears_awaiting_guidance(
    make_mock_callback_update, make_context, admin_user,
):
    state.pending_approval[42] = {
        "chat_id": VIP_CHAT_ID,
        "bc_id": "bc",
        "username": "testvip",
        "gen": 1,
        "variants": [{"response": "hola", "confidence": 90, "topic": "g"}],
        "selected": 0,
        "regenerating": False,
    }
    state.awaiting_guidance_answer[ADMIN_ID] = GID
    update = make_mock_callback_update(data="a:note:42", user=admin_user)

    await handle_callback(update, make_context())

    assert ADMIN_ID not in state.awaiting_guidance_answer
    assert ADMIN_ID in state.awaiting_note


@pytest.mark.asyncio
async def test_a_fix_clears_awaiting_guidance(
    make_mock_callback_update, make_context, admin_user,
):
    state.pending_approval[43] = {
        "chat_id": VIP_CHAT_ID,
        "bc_id": "bc",
        "username": "testvip",
        "gen": 1,
        "variants": [{"response": "hola", "confidence": 90, "topic": "g"}],
        "selected": 0,
        "regenerating": False,
    }
    state.awaiting_guidance_answer[ADMIN_ID] = GID
    update = make_mock_callback_update(data="a:fix:43", user=admin_user)

    await handle_callback(update, make_context())

    assert ADMIN_ID not in state.awaiting_guidance_answer
    assert state.awaiting_correction[ADMIN_ID] == 43


# ── expired / unauthorized ─────────────────────────────────


@pytest.mark.asyncio
async def test_g_expired_guidance(make_mock_callback_update, make_context, admin_user):
    update = make_mock_callback_update(data="g:skip:99999", user=admin_user)
    await handle_callback(update, make_context())
    update.callback_query.edit_message_text.assert_awaited()
    text = update.callback_query.edit_message_text.await_args[0][0]
    assert "expir" in text.lower() or "procesad" in text.lower()


@pytest.mark.asyncio
async def test_g_unauthorized_rejected(make_mock_callback_update, make_context, make_user):
    other = make_user(user_id=111, username="notadmin")
    state.pending_guidance[GID] = {
        "chat_id": VIP_CHAT_ID,
        "bc_id": "bc",
        "username": "v",
        "gen": 1,
        "topic": "t",
        "gap_question": "q",
        "draft_response": "d",
        "confidence": 50,
        "created_at": "x",
    }
    update = make_mock_callback_update(data=f"g:answer:{GID}", user=other)
    handled = await handle_callback(update, make_context())
    assert handled is True
    update.callback_query.answer.assert_awaited()
    assert ADMIN_ID not in state.awaiting_guidance_answer
    assert other.id not in state.awaiting_guidance_answer
