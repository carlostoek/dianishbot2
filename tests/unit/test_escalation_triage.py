"""Unit tests for escalation triage callbacks and false-positive learning."""

import pytest
from unittest.mock import AsyncMock, patch

import auth_users
import state
from handlers.business import needs_escalation
from handlers.callbacks import (
    _build_escalation_keyboard,
    handle_callback,
    notify_diana_escalation,
)
from services.training import (
    format_escalation_report,
    is_known_false_positive,
    review_escalation,
    save_escalation_event,
)

ADMIN_ID = 555001
VIP_CHAT_ID = 777001
ESC_ID = 42


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
    state.pending_escalations.clear()
    state.pending_approval.clear()
    state.reply_gen.clear()
    state.history.clear()
    yield
    state.pending_escalations.clear()
    state.pending_approval.clear()
    state.reply_gen.clear()
    state.history.clear()


@pytest.fixture
def admin_user(make_user):
    return make_user(user_id=ADMIN_ID, username="diana_admin", first_name="Diana")


@pytest.fixture
def escalation_pending():
    return {
        "chat_id": VIP_CHAT_ID,
        "bc_id": "bc_test",
        "username": "testvip",
        "gen": 0,
        "source": "llm",
        "reason": "Tema LLM: 'escalado_humano'",
        "matched": "escalado_humano",
        "trigger_text": "Me mandas besitos para alguna sesión especial?",
        "verdict": None,
    }


def test_aprecio_does_not_escalate():
    assert needs_escalation("te aprecio mucho") is None


def test_precio_still_escalates():
    result = needs_escalation("cuál es el precio?")
    assert result is not None
    assert "precio" in result


def test_escalation_keyboard_initial(escalation_pending):
    kb = _build_escalation_keyboard(ESC_ID, escalation_pending)
    assert kb is not None
    row = kb.inline_keyboard[0]
    callbacks = [btn.callback_data for btn in row]
    assert f"e:valid:{ESC_ID}" in callbacks
    assert f"e:fp:{ESC_ID}" in callbacks


def test_escalation_keyboard_after_fp(escalation_pending):
    escalation_pending["verdict"] = "false_positive"
    kb = _build_escalation_keyboard(ESC_ID, escalation_pending)
    assert len(kb.inline_keyboard) == 1
    assert kb.inline_keyboard[0][0].callback_data == f"e:gen:{ESC_ID}"


def test_format_escalation_report_empty(in_memory_training_db):
    report = format_escalation_report(days=7)
    assert "Sin escalaciones" in report


def test_format_escalation_report_with_events(in_memory_training_db):
    esc_id = save_escalation_event(
        chat_id=VIP_CHAT_ID,
        username="mickydomu",
        source="llm",
        reason="Tema LLM: 'escalado_humano'",
        matched="escalado_humano",
        trigger_text="Me mandas besitos?",
        context=[{"role": "user", "content": "Me mandas besitos?"}],
    )
    review_escalation(esc_id, "false_positive")
    report = format_escalation_report(days=7)
    assert "mickydomu" in report
    assert "falso positivo" in report
    assert "Me mandas besitos" in report


@pytest.mark.asyncio
async def test_au_escalaciones_callback(
    make_mock_callback_update, make_context, admin_user,
):
    update = make_mock_callback_update(data="au:escalaciones", user=admin_user)
    context = make_context()
    handled = await auth_users.handle_callback(update, context)
    assert handled is True
    cq = update.callback_query
    cq.edit_message_text.assert_awaited()
    text = cq.edit_message_text.await_args[0][0]
    assert "Escalaciones" in text


def test_is_known_false_positive_after_review(in_memory_training_db):
    esc_id = save_escalation_event(
        chat_id=VIP_CHAT_ID,
        username="vip",
        source="keyword",
        reason="Keyword detectada: 'precio'",
        matched="precio",
        trigger_text="Te aprecio",
        context=[{"role": "user", "content": "Te aprecio"}],
    )
    review_escalation(esc_id, "false_positive")
    assert is_known_false_positive("keyword", "precio", "Te aprecio") is True
    assert is_known_false_positive("keyword", "precio", "otro texto") is False


@pytest.mark.asyncio
async def test_notify_diana_escalation_has_buttons(bot, escalation_pending):
    with patch("handlers.callbacks.DIANA_ADMIN_CHAT_ID", ADMIN_ID):
        await notify_diana_escalation(
            bot,
            esc_id=ESC_ID,
            user_id=VIP_CHAT_ID,
            username="testvip",
            chat_id=VIP_CHAT_ID,
            reason=escalation_pending["reason"],
            trigger_text=escalation_pending["trigger_text"],
            context=[{"role": "user", "content": escalation_pending["trigger_text"]}],
            pending=escalation_pending,
        )
    bot.send_message.assert_awaited_once()
    _, kwargs = bot.send_message.await_args
    assert kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_e_valid_marks_and_closes(
    make_mock_callback_update, make_context, admin_user, escalation_pending,
):
    state.pending_escalations[ESC_ID] = escalation_pending.copy()
    state.history[VIP_CHAT_ID] = [
        {"role": "user", "content": escalation_pending["trigger_text"]},
    ]
    update = make_mock_callback_update(data=f"e:valid:{ESC_ID}", user=admin_user)
    context = make_context()
    with patch("handlers.callbacks.review_escalation") as mock_review:
        handled = await handle_callback(update, context)
    assert handled is True
    assert ESC_ID not in state.pending_escalations
    mock_review.assert_called_once_with(ESC_ID, "valid")


@pytest.mark.asyncio
async def test_e_fp_shows_generate_button(
    make_mock_callback_update, make_context, admin_user, escalation_pending,
):
    state.pending_escalations[ESC_ID] = escalation_pending.copy()
    state.history[VIP_CHAT_ID] = [
        {"role": "user", "content": escalation_pending["trigger_text"]},
    ]
    update = make_mock_callback_update(data=f"e:fp:{ESC_ID}", user=admin_user)
    context = make_context()
    with patch("handlers.callbacks.review_escalation") as mock_review:
        handled = await handle_callback(update, context)
    assert handled is True
    assert state.pending_escalations[ESC_ID]["verdict"] == "false_positive"
    mock_review.assert_called_once_with(ESC_ID, "false_positive")
    cq = update.callback_query
    _, kwargs = cq.edit_message_text.await_args
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == f"e:gen:{ESC_ID}"


@pytest.mark.asyncio
async def test_e_gen_creates_approval_draft(
    make_mock_callback_update, make_context, admin_user, escalation_pending,
):
    pending = escalation_pending.copy()
    pending["verdict"] = "false_positive"
    state.pending_escalations[ESC_ID] = pending
    state.history[VIP_CHAT_ID] = [
        {"role": "user", "content": pending["trigger_text"]},
    ]
    update = make_mock_callback_update(data=f"e:gen:{ESC_ID}", user=admin_user)
    context = make_context()
    with (
        patch(
            "handlers.callbacks.get_diana_response",
            new_callable=AsyncMock,
            return_value=("hola besito", 90, "coqueteo", False, "", None),
        ),
        patch("handlers.callbacks.save_example", return_value=101) as mock_save,
        patch(
            "handlers.callbacks.notify_diana_approval", new_callable=AsyncMock,
        ) as mock_notify,
    ):
        handled = await handle_callback(update, context)
    assert handled is True
    assert ESC_ID not in state.pending_escalations
    assert 101 in state.pending_approval
    mock_save.assert_called_once()
    mock_notify.assert_awaited_once()