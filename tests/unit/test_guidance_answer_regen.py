"""Post-answer distill → regen → enter_draft_pipeline; stale gen handling (WU3)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import auth_users
import state
from handlers.callbacks.guidance import handle_diana_guidance_answer


ADMIN_ID = 555002
VIP_CHAT_ID = 777002


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
    state.pending_approval.clear()
    state.reply_gen.clear()
    state.history.clear()
    yield
    state.pending_guidance.clear()
    state.awaiting_guidance_answer.clear()
    state.pending_approval.clear()
    state.reply_gen.clear()
    state.history.clear()


def _seed(in_memory_training_db, *, gen=1):
    from services import knowledge
    gid = knowledge.create_guidance_request(
        chat_id=VIP_CHAT_ID,
        username="testvip",
        topic="limites_contenido",
        gap_question="¿Puedo ofrecer videollamada fuera de tarifa?",
        draft_response="Mmm déjame pensarlo",
    )
    state.pending_guidance[gid] = {
        "chat_id": VIP_CHAT_ID,
        "bc_id": "bc_test",
        "username": "testvip",
        "gen": gen,
        "topic": "limites_contenido",
        "gap_question": "¿Puedo ofrecer videollamada fuera de tarifa?",
        "draft_response": "Mmm déjame pensarlo",
        "confidence": 65,
        "created_at": "2026-07-13T12:00:00",
    }
    state.reply_gen[VIP_CHAT_ID] = gen
    state.history[VIP_CHAT_ID] = [
        {"role": "user", "content": "me haces una videollamada privada?"},
    ]
    state.awaiting_guidance_answer[ADMIN_ID] = gid
    return gid


@pytest.mark.asyncio
async def test_answer_fresh_distill_regen_supervised(
    make_mock_update, make_context, make_user, in_memory_training_db,
):
    """Fresh gen: distill → policy → regen get_diana_response → enter_draft_pipeline."""
    from services import knowledge
    admin = make_user(user_id=ADMIN_ID, username="diana_admin")
    gid = _seed(in_memory_training_db, gen=1)
    update = make_mock_update(
        text="No ofrezcas videollamada fuera de tarifa. Redirigí a packs.",
        user=admin,
    )
    distilled = {
        "topic": "limites_contenido",
        "policy_summary": "No ofrezcas videollamada privada fuera de tarifa.",
        "example_response": "eso no lo hago fuera del pack",
        "keywords": ["videollamada", "privado"],
        "priority": 100,
        "degraded": False,
    }
    regen = ("ok con política", 90, "limites_contenido", False, "", None)

    with (
        patch(
            "handlers.callbacks.guidance.knowledge.distill_guidance",
            new_callable=AsyncMock,
            return_value=distilled,
        ) as mock_distill,
        patch(
            "handlers.callbacks.guidance.get_diana_response",
            new_callable=AsyncMock,
            return_value=regen,
        ) as mock_regen,
        patch("handlers.timer._is_supervised_for_chat", return_value=True),
        patch("handlers.timer.notify_diana_approval", new_callable=AsyncMock) as mock_notify,
        patch("handlers.timer.deliver_vip_response", new_callable=AsyncMock) as mock_deliver,
        patch("handlers.timer.save_example", return_value=701) as mock_save,
    ):
        handled = await handle_diana_guidance_answer(update, make_context())

    assert handled is True
    mock_distill.assert_awaited_once()
    mock_regen.assert_awaited_once()
    mock_save.assert_called_once()
    # saved response is regen text, not the old draft
    assert mock_save.call_args[0][3] == "ok con política"
    mock_notify.assert_awaited_once()
    mock_deliver.assert_not_awaited()
    assert gid not in state.pending_guidance
    req = knowledge.get_guidance_request(gid)
    assert req["status"] == "answered"
    assert req["policy_id"] is not None
    policy = knowledge.get_policy(req["policy_id"])
    assert policy is not None
    assert "videollamada" in policy["policy_summary"].lower()


@pytest.mark.asyncio
async def test_answer_fresh_autonomous_delivers(
    make_mock_update, make_context, make_user, in_memory_training_db,
):
    admin = make_user(user_id=ADMIN_ID, username="diana_admin")
    _seed(in_memory_training_db, gen=1)
    update = make_mock_update(text="Nunca ofrezcas X.", user=admin)
    distilled = {
        "topic": "custom",
        "policy_summary": "Nunca ofrezcas X.",
        "example_response": "no hago eso",
        "keywords": ["x"],
        "priority": 100,
        "degraded": False,
    }
    regen = ("respuesta nueva", 88, "custom", False, "", None)

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
        patch("handlers.timer._is_supervised_for_chat", return_value=False),
        patch(
            "handlers.timer.deliver_vip_response",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_deliver,
        patch("handlers.timer.notify_diana_approval", new_callable=AsyncMock) as mock_notify,
        patch("handlers.timer.save_example", return_value=702),
    ):
        await handle_diana_guidance_answer(update, make_context())

    mock_deliver.assert_awaited_once()
    mock_notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_stale_gen_no_vip_send(
    make_mock_update, make_context, make_user, in_memory_training_db,
):
    """Stale gen: store policy, close consult, no VIP send; notify Diana."""
    from services import knowledge
    admin = make_user(user_id=ADMIN_ID, username="diana_admin")
    gid = _seed(in_memory_training_db, gen=1)
    state.reply_gen[VIP_CHAT_ID] = 2  # VIP wrote again
    update = make_mock_update(text="Doctrina: no hagas Z.", user=admin)
    distilled = {
        "topic": "z",
        "policy_summary": "No hagas Z.",
        "example_response": "",
        "keywords": ["z"],
        "priority": 100,
        "degraded": False,
    }

    with (
        patch(
            "handlers.callbacks.guidance.knowledge.distill_guidance",
            new_callable=AsyncMock,
            return_value=distilled,
        ),
        patch(
            "handlers.callbacks.guidance.get_diana_response",
            new_callable=AsyncMock,
        ) as mock_regen,
        patch("handlers.timer.deliver_vip_response", new_callable=AsyncMock) as mock_deliver,
        patch("handlers.timer.save_example") as mock_save,
        patch("handlers.timer.notify_diana_approval", new_callable=AsyncMock) as mock_notify,
    ):
        handled = await handle_diana_guidance_answer(update, make_context())

    assert handled is True
    mock_regen.assert_not_awaited()
    mock_deliver.assert_not_awaited()
    mock_save.assert_not_called()
    mock_notify.assert_not_awaited()
    assert gid not in state.pending_guidance
    req = knowledge.get_guidance_request(gid)
    assert req["status"] == "answered"
    assert req["policy_id"] is not None
    # Diana notified via reply_text about stale
    update.message.reply_text.assert_awaited()
    text = update.message.reply_text.await_args[0][0].lower()
    assert "vip" in text or "escribió" in text or "obsolet" in text or "nuevo" in text


@pytest.mark.asyncio
async def test_answer_distill_fail_still_saves_policy(
    make_mock_update, make_context, make_user, in_memory_training_db,
):
    from services import knowledge
    admin = make_user(user_id=ADMIN_ID, username="diana_admin")
    gid = _seed(in_memory_training_db, gen=1)
    update = make_mock_update(text="Regla cruda degradada.", user=admin)
    distilled = {
        "topic": "limites_contenido",
        "policy_summary": "Regla cruda degradada.",
        "example_response": "",
        "keywords": [],
        "priority": 100,
        "degraded": True,
    }
    regen = ("regen ok", 80, "limites_contenido", False, "", None)

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
        patch("handlers.timer.notify_diana_approval", new_callable=AsyncMock),
        patch("handlers.timer.save_example", return_value=703),
    ):
        await handle_diana_guidance_answer(update, make_context())

    req = knowledge.get_guidance_request(gid)
    assert req["status"] == "answered"
    policy = knowledge.get_policy(req["policy_id"])
    assert policy["policy_summary"] == "Regla cruda degradada."
