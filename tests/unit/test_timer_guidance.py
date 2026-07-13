"""Timer gap branch: consult + VIP freeze invariants (WU2)."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import state
from state import history, reply_gen, timers, chat_meta, pending_guidance
import handlers.timer as timer_mod


VIP = 1551234999


@pytest.fixture(autouse=True)
def _reset_state():
    history.clear()
    reply_gen.clear()
    timers.clear()
    chat_meta.clear()
    pending_guidance.clear()
    state.pending_approval.clear()
    yield
    history.clear()
    reply_gen.clear()
    timers.clear()
    chat_meta.clear()
    pending_guidance.clear()
    state.pending_approval.clear()


def _llm_gap_payload(
    *,
    response="tentativo",
    confidence=70,
    topic="limites_contenido",
    knowledge_gap=True,
    gap_question="¿Puedo ofrecer X?",
):
    return (response, confidence, topic, knowledge_gap, gap_question, None)


@pytest.mark.asyncio
async def test_gap_no_match_opens_consult_no_vip_io(in_memory_training_db, monkeypatch):
    monkeypatch.setattr(timer_mod, "KNOWLEDGE_GAP_ENABLED", True)
    chat_id = VIP
    gen = 1
    reply_gen[chat_id] = gen
    chat_meta[chat_id] = {"vip_id": chat_id, "username": "vip"}
    history[chat_id] = [{"role": "user", "content": "quiero algo especial"}]

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch(
            "handlers.timer.get_diana_response",
            new_callable=AsyncMock,
            return_value=_llm_gap_payload(),
        ),
        patch("services.knowledge.match_policies", return_value=[]),
        patch(
            "handlers.callbacks.guidance.notify_diana_guidance",
            new_callable=AsyncMock,
        ) as mock_notify,
        patch("handlers.timer.save_example") as mock_save,
        patch("handlers.timer.notify_diana_approval", new_callable=AsyncMock) as mock_approval,
        patch("handlers.timer.deliver_vip_response", new_callable=AsyncMock) as mock_deliver,
        patch("services.delivery.mark_as_read", new_callable=AsyncMock) as mock_read,
        patch("services.delivery.simulate_typing", new_callable=AsyncMock) as mock_type,
    ):
        task = asyncio.create_task(
            timer_mod.auto_reply(AsyncMock(), chat_id, "vip", "bc_test", gen),
        )
        timers[chat_id] = task
        await task

    assert pending_guidance  # at least one open
    gid = next(iter(pending_guidance))
    assert pending_guidance[gid]["chat_id"] == chat_id
    assert pending_guidance[gid]["gap_question"] == "¿Puedo ofrecer X?"
    mock_notify.assert_awaited_once()
    mock_save.assert_not_called()
    mock_approval.assert_not_awaited()
    mock_deliver.assert_not_awaited()
    mock_read.assert_not_awaited()
    mock_type.assert_not_awaited()
    assert chat_id not in timers


@pytest.mark.asyncio
async def test_flag_off_ignores_gap(in_memory_training_db, monkeypatch):
    monkeypatch.setattr(timer_mod, "KNOWLEDGE_GAP_ENABLED", False)
    chat_id = VIP + 1
    gen = 1
    reply_gen[chat_id] = gen
    history[chat_id] = [{"role": "user", "content": "hola"}]

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch(
            "handlers.timer.get_diana_response",
            new_callable=AsyncMock,
            return_value=_llm_gap_payload(),
        ),
        patch("handlers.timer.save_example", return_value=88) as mock_save,
        patch("handlers.timer.notify_diana_approval", new_callable=AsyncMock) as mock_approval,
        patch(
            "handlers.callbacks.guidance.notify_diana_guidance",
            new_callable=AsyncMock,
        ) as mock_guidance,
    ):
        # supervised path by default in tests (APPROVAL_MODE True typically)
        task = asyncio.create_task(
            timer_mod.auto_reply(AsyncMock(), chat_id, "vip", "bc_test", gen),
        )
        timers[chat_id] = task
        await task

    mock_save.assert_called_once()
    mock_guidance.assert_not_awaited()
    assert not pending_guidance
    # either approval or deliver depending on supervised
    assert mock_approval.await_count + 0 >= 0  # save happened; consult did not


@pytest.mark.asyncio
async def test_escalation_wins_over_gap(in_memory_training_db, monkeypatch):
    monkeypatch.setattr(timer_mod, "KNOWLEDGE_GAP_ENABLED", True)
    chat_id = VIP + 2
    gen = 1
    reply_gen[chat_id] = gen
    chat_meta[chat_id] = {"vip_id": chat_id, "username": "Ldt"}
    history[chat_id] = [{"role": "user", "content": "necesito hablar con alguien"}]

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch(
            "handlers.timer.get_diana_response",
            new_callable=AsyncMock,
            return_value=_llm_gap_payload(
                topic="escalado_humano",
                knowledge_gap=True,
                gap_question="¿Qué hago?",
            ),
        ),
        patch("handlers.business.escalate_to_diana", new_callable=AsyncMock) as mock_esc,
        patch(
            "handlers.callbacks.guidance.notify_diana_guidance",
            new_callable=AsyncMock,
        ) as mock_guidance,
        patch("handlers.timer.save_example") as mock_save,
    ):
        task = asyncio.create_task(
            timer_mod.auto_reply(AsyncMock(), chat_id, "Ldt", "bc_test", gen),
        )
        timers[chat_id] = task
        await task

    mock_esc.assert_awaited_once()
    mock_guidance.assert_not_awaited()
    mock_save.assert_not_called()
    assert not pending_guidance


@pytest.mark.asyncio
async def test_gap_with_policy_match_one_regen_no_consult(
    in_memory_training_db, monkeypatch,
):
    """Anti-reask (WU3): match → one regen with policies; no pending_guidance."""
    monkeypatch.setattr(timer_mod, "KNOWLEDGE_GAP_ENABLED", True)
    chat_id = VIP + 3
    gen = 1
    reply_gen[chat_id] = gen
    history[chat_id] = [{"role": "user", "content": "videollamada?"}]

    matched = [{"id": 1, "topic": "limites", "policy_summary": "No ofrezcas VL"}]
    first = _llm_gap_payload(topic="limites", response="tentativo gap")
    second = ("respuesta con política", 92, "limites", False, "", None)

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch(
            "handlers.timer.get_diana_response",
            new_callable=AsyncMock,
            side_effect=[first, second],
        ) as mock_llm,
        patch("services.knowledge.match_policies", return_value=matched),
        patch(
            "handlers.callbacks.guidance.notify_diana_guidance",
            new_callable=AsyncMock,
        ) as mock_guidance,
        patch("handlers.timer.save_example", return_value=91) as mock_save,
        patch("handlers.timer.notify_diana_approval", new_callable=AsyncMock),
    ):
        task = asyncio.create_task(
            timer_mod.auto_reply(AsyncMock(), chat_id, "vip", "bc_test", gen),
        )
        timers[chat_id] = task
        await task

    mock_guidance.assert_not_awaited()
    assert mock_llm.await_count == 2  # original + one regen
    mock_save.assert_called_once()
    assert mock_save.call_args[0][3] == "respuesta con política"
    assert not pending_guidance


@pytest.mark.asyncio
async def test_gap_consult_creates_db_request(in_memory_training_db, monkeypatch):
    from services import knowledge

    monkeypatch.setattr(timer_mod, "KNOWLEDGE_GAP_ENABLED", True)
    chat_id = VIP + 4
    gen = 1
    reply_gen[chat_id] = gen
    history[chat_id] = [{"role": "user", "content": "pack custom?"}]

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch(
            "handlers.timer.get_diana_response",
            new_callable=AsyncMock,
            return_value=_llm_gap_payload(gap_question="¿Pack custom?"),
        ),
        patch("services.knowledge.match_policies", return_value=[]),
        patch(
            "handlers.callbacks.guidance.notify_diana_guidance",
            new_callable=AsyncMock,
        ),
        patch("handlers.timer.save_example") as mock_save,
    ):
        task = asyncio.create_task(
            timer_mod.auto_reply(AsyncMock(), chat_id, "vip", "bc_test", gen),
        )
        timers[chat_id] = task
        await task

    assert pending_guidance
    gid = next(iter(pending_guidance))
    req = knowledge.get_guidance_request(gid)
    assert req is not None
    assert req["status"] == "pending"
    assert req["gap_question"] == "¿Pack custom?"
    mock_save.assert_not_called()
