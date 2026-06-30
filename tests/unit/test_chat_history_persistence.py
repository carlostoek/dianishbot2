"""Tests for durable chat history persistence (services/chat_history.py)."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import auth_users
import state
from config import MAX_STORED_HISTORY
from handlers import business
from services import chat_history, sandbox
from services.delivery import deliver_vip_response
from services.llm import get_diana_response
from state import _should_skip_timer_recovery

VIP_ID = 777001
VIP_CHAT_ID = 777001
ADMIN_ID = 555001
BC_ID = "bc_test"
_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def profiles_file(tmp_path):
    path = tmp_path / "sandbox_profiles.json"
    src = _REPO_ROOT / "diana_sandbox_profiles.json"
    path.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _reset_state(tmp_path, profiles_file):
    users_file = tmp_path / "authorized.json"
    auth_users.configure(
        users_file=str(users_file),
        max_users=5,
        seed_user_ids=[VIP_ID],
        admin_id=ADMIN_ID,
    )
    auth_users.set_admin_id(ADMIN_ID)
    sandbox.configure(profiles_file=str(profiles_file))
    sandbox._active.clear()
    sandbox._focus_chat_id = None
    state.history.clear()
    state.reply_gen.clear()
    state.timers.clear()
    state.timer_schedule.clear()
    state.pending_approval.clear()
    state.chat_bc.clear()
    state.pending_msg.clear()
    state.chat_meta.clear()
    state.connections.clear()
    yield
    sandbox._active.clear()
    sandbox._focus_chat_id = None
    state.history.clear()
    state.reply_gen.clear()
    state.timers.clear()
    state.timer_schedule.clear()
    state.pending_approval.clear()
    state.chat_bc.clear()
    state.pending_msg.clear()
    state.chat_meta.clear()
    state.connections.clear()


def test_init_db_creates_chat_history_table(chat_history_db):
    row = chat_history_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='chat_history'"
    ).fetchone()
    assert row is not None


def test_append_user_message_persists_to_db(chat_history_db):
    chat_history.append_message(VIP_CHAT_ID, "user", "hola")
    assert state.history[VIP_CHAT_ID][0]["content"] == "hola"
    stored = chat_history.load_chat_history(VIP_CHAT_ID)
    assert len(stored) == 1
    assert stored[0]["role"] == "user"


@pytest.mark.asyncio
async def test_append_assistant_on_delivery_persists(chat_history_db, bot):
    state.reply_gen[VIP_CHAT_ID] = 1
    with (
        patch("services.delivery.asyncio.sleep", new_callable=AsyncMock),
        patch("services.delivery.mark_as_read", new_callable=AsyncMock),
        patch("services.delivery.simulate_typing", new_callable=AsyncMock),
    ):
        ok = await deliver_vip_response(
            bot,
            chat_id=VIP_CHAT_ID,
            bc_id=BC_ID,
            username="vip",
            gen=1,
            text="respuesta bot",
        )
    assert ok is True
    stored = chat_history.load_chat_history(VIP_CHAT_ID)
    assert len(stored) == 1
    assert stored[0]["role"] == "assistant"
    assert stored[0]["content"] == "respuesta bot"


@pytest.mark.asyncio
async def test_diana_manual_reply_persists_authorized(chat_history_db):
    state.connections[BC_ID] = ADMIN_ID
    state.chat_meta[VIP_CHAT_ID] = {"vip_id": VIP_ID, "username": "vip"}

    msg = AsyncMock()
    msg.business_connection_id = BC_ID
    msg.chat.id = VIP_CHAT_ID
    msg.text = "respuesta manual Diana"
    msg.caption = None
    msg.from_user.id = ADMIN_ID
    msg.from_user.username = "diana"
    msg.from_user.first_name = "Diana"
    msg.message_id = 1

    await business._handle_business_message(msg, AsyncMock(), edited=False)

    stored = chat_history.load_chat_history(VIP_CHAT_ID)
    assert len(stored) == 1
    assert stored[0]["role"] == "assistant"
    assert stored[0]["content"] == "respuesta manual Diana"


@pytest.mark.asyncio
async def test_diana_manual_skips_unauthorized_observe(
    chat_history_db, in_memory_training_db, monkeypatch,
):
    monkeypatch.setattr(business, "OBSERVE_UNAUTHORIZED", True)
    state.connections[BC_ID] = ADMIN_ID
    state.chat_meta[500] = {"vip_id": 999, "username": "observed"}
    state.history[500] = [{"role": "user", "content": "pregunta"}]

    msg = AsyncMock()
    msg.business_connection_id = BC_ID
    msg.chat.id = 500
    msg.text = "respuesta manual"
    msg.caption = None
    msg.from_user.id = ADMIN_ID
    msg.from_user.username = "diana"
    msg.from_user.first_name = "Diana"
    msg.message_id = 1

    await business._handle_business_message(msg, AsyncMock(), edited=False)

    assert chat_history.load_chat_history(500) == []


def test_ensure_loaded_restores_empty_ram(chat_history_db):
    chat_history.append_message(VIP_CHAT_ID, "user", "persistido")
    state.history.clear()
    msgs = chat_history.ensure_loaded(VIP_CHAT_ID)
    assert len(msgs) == 1
    assert msgs[0]["content"] == "persistido"


def test_vip_message_after_restart_preserves_prior_context(chat_history_db):
    for i in range(3):
        chat_history.append_message(VIP_CHAT_ID, "user" if i % 2 == 0 else "assistant", f"msg{i}")
    state.history.clear()
    chat_history.ensure_loaded(VIP_CHAT_ID)
    chat_history.append_message(VIP_CHAT_ID, "user", "nuevo tras restart")
    assert len(state.history[VIP_CHAT_ID]) == 4
    assert state.history[VIP_CHAT_ID][0]["content"] == "msg0"
    assert state.history[VIP_CHAT_ID][-1]["content"] == "nuevo tras restart"


def test_sandbox_active_does_not_persist(chat_history_db):
    sandbox.activate(VIP_CHAT_ID)
    chat_history.append_message(VIP_CHAT_ID, "user", "sandbox msg")
    assert VIP_CHAT_ID in state.history
    assert chat_history.load_chat_history(VIP_CHAT_ID) == []


def test_sandbox_reset_preserves_db_history(chat_history_db):
    chat_history.append_message(VIP_CHAT_ID, "user", "persistido")
    sandbox.activate(VIP_CHAT_ID)
    chat_history.append_message(VIP_CHAT_ID, "user", "solo ram sandbox")
    assert sandbox.reset_chat_state(VIP_CHAT_ID) is True
    assert VIP_CHAT_ID not in state.history
    stored = chat_history.load_chat_history(VIP_CHAT_ID)
    assert len(stored) == 1
    assert stored[0]["content"] == "persistido"


def test_ensure_loaded_prefers_longer_db_when_ram_shorter(chat_history_db):
    runtime_msgs = [{"role": "user", "content": f"r{i}"} for i in range(2)]
    db_msgs = [{"role": "user", "content": f"d{i}"} for i in range(5)]
    state.history[VIP_CHAT_ID] = list(runtime_msgs)
    chat_history_db.execute(
        "INSERT INTO chat_history (chat_id, messages, updated_at) VALUES (?, ?, ?)",
        (VIP_CHAT_ID, json.dumps(db_msgs), "2026-01-01"),
    )
    chat_history_db.commit()
    msgs = chat_history.ensure_loaded(VIP_CHAT_ID)
    assert len(msgs) == 5
    assert msgs[0]["content"] == "d0"


def test_append_message_calls_merge_when_ram_shorter(chat_history_db):
    state.history[VIP_CHAT_ID] = [{"role": "user", "content": "r0"}]
    db_msgs = [{"role": "user", "content": f"d{i}"} for i in range(4)]
    chat_history_db.execute(
        "INSERT INTO chat_history (chat_id, messages, updated_at) VALUES (?, ?, ?)",
        (VIP_CHAT_ID, json.dumps(db_msgs), "2026-01-01"),
    )
    chat_history_db.commit()
    chat_history.append_message(VIP_CHAT_ID, "user", "nuevo")
    assert len(state.history[VIP_CHAT_ID]) == 5
    assert state.history[VIP_CHAT_ID][0]["content"] == "d0"
    assert state.history[VIP_CHAT_ID][-1]["content"] == "nuevo"
    stored = chat_history.load_chat_history(VIP_CHAT_ID)
    assert len(stored) == 5
    assert stored[-1]["content"] == "nuevo"


@pytest.mark.asyncio
async def test_delivery_failure_does_not_persist_assistant(chat_history_db, bot):
    state.reply_gen[VIP_CHAT_ID] = 1
    bot.send_message = AsyncMock(side_effect=RuntimeError("send failed"))
    with (
        patch("services.delivery.asyncio.sleep", new_callable=AsyncMock),
        patch("services.delivery.mark_as_read", new_callable=AsyncMock),
        patch("services.delivery.simulate_typing", new_callable=AsyncMock),
    ):
        ok = await deliver_vip_response(
            bot,
            chat_id=VIP_CHAT_ID,
            bc_id=BC_ID,
            username="vip",
            gen=1,
            text="no debe guardarse",
        )
    assert ok is False
    assert chat_history.load_chat_history(VIP_CHAT_ID) == []


@pytest.mark.asyncio
async def test_vip_ingest_merges_seeded_db(chat_history_db):
    db_msgs = [{"role": "user", "content": f"prev{i}"} for i in range(3)]
    chat_history_db.execute(
        "INSERT INTO chat_history (chat_id, messages, updated_at) VALUES (?, ?, ?)",
        (VIP_CHAT_ID, json.dumps(db_msgs), "2026-01-01"),
    )
    chat_history_db.commit()

    msg = AsyncMock()
    msg.business_connection_id = BC_ID
    msg.chat.id = VIP_CHAT_ID
    msg.text = "mensaje nuevo"
    msg.caption = None
    msg.from_user.id = VIP_ID
    msg.from_user.username = "vip"
    msg.from_user.first_name = "VIP"
    msg.message_id = 42

    with (
        patch("handlers.business.compute_reply_delay", return_value=60.0),
        patch("handlers.business.auto_reply", new_callable=AsyncMock),
        patch("handlers.business._save_runtime_state"),
    ):
        await business._handle_business_message(msg, AsyncMock(), edited=False)

    assert len(state.history[VIP_CHAT_ID]) == 4
    assert state.history[VIP_CHAT_ID][0]["content"] == "prev0"
    assert state.history[VIP_CHAT_ID][-1]["content"] == "mensaje nuevo"
    stored = chat_history.load_chat_history(VIP_CHAT_ID)
    assert len(stored) == 4


@pytest.mark.asyncio
async def test_get_diana_response_lazy_loads_when_ram_empty(chat_history_db, in_memory_training_db):
    chat_history.append_message(VIP_CHAT_ID, "user", "hola desde db")
    state.history.clear()
    llm_json = '{"response": "hey", "confidence": 85, "topic": "saludo"}'

    with (
        patch("services.llm.raw_call", new_callable=AsyncMock, return_value=(llm_json, None)),
        patch("services.llm.asyncio.sleep", new_callable=AsyncMock),
    ):
        response, confidence, topic, failure = await get_diana_response(VIP_CHAT_ID)

    assert response == "hey"
    assert failure is None
    assert len(state.history[VIP_CHAT_ID]) == 1


def test_runtime_recovery_merge_prefers_longer_history(chat_history_db):
    runtime_msgs = [{"role": "user", "content": f"r{i}"} for i in range(3)]
    db_msgs = [{"role": "user", "content": f"d{i}"} for i in range(5)]
    state.history[VIP_CHAT_ID] = list(runtime_msgs)
    chat_history_db.execute(
        "INSERT INTO chat_history (chat_id, messages, updated_at) VALUES (?, ?, ?)",
        (VIP_CHAT_ID, json.dumps(db_msgs), "2026-01-01"),
    )
    chat_history_db.commit()
    chat_history.merge_runtime_with_db(VIP_CHAT_ID)
    assert len(state.history[VIP_CHAT_ID]) == 5
    assert state.history[VIP_CHAT_ID][0]["content"] == "d0"


def test_runtime_recovery_keeps_longer_runtime(chat_history_db):
    runtime_msgs = [{"role": "user", "content": f"r{i}"} for i in range(5)]
    db_msgs = [{"role": "user", "content": f"d{i}"} for i in range(3)]
    state.history[VIP_CHAT_ID] = list(runtime_msgs)
    chat_history_db.execute(
        "INSERT INTO chat_history (chat_id, messages, updated_at) VALUES (?, ?, ?)",
        (VIP_CHAT_ID, json.dumps(db_msgs), "2026-01-01"),
    )
    chat_history_db.commit()
    chat_history.merge_runtime_with_db(VIP_CHAT_ID)
    assert len(state.history[VIP_CHAT_ID]) == 5
    assert state.history[VIP_CHAT_ID][0]["content"] == "r0"


def test_should_skip_timer_recovery_with_loaded_history(chat_history_db):
    msgs = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "yo contesté"},
    ]
    chat_history_db.execute(
        "INSERT INTO chat_history (chat_id, messages, updated_at) VALUES (?, ?, ?)",
        (VIP_CHAT_ID, json.dumps(msgs), "2026-01-01"),
    )
    chat_history_db.commit()
    state.history.clear()
    chat_history.ensure_loaded(VIP_CHAT_ID)
    assert _should_skip_timer_recovery(VIP_CHAT_ID) is True


def test_trim_respects_max_stored_history(chat_history_db):
    for i in range(51):
        chat_history.append_message(VIP_CHAT_ID, "user", f"msg{i}")
    stored = chat_history.load_chat_history(VIP_CHAT_ID)
    assert len(stored) == MAX_STORED_HISTORY
    assert stored[0]["content"] == "msg1"
    assert stored[-1]["content"] == "msg50"


def test_load_chat_history_trims_legacy_oversized_rows(chat_history_db):
    legacy = [{"role": "user", "content": f"legacy{i}"} for i in range(60)]
    chat_history_db.execute(
        "INSERT INTO chat_history (chat_id, messages, updated_at) VALUES (?, ?, ?)",
        (VIP_CHAT_ID, json.dumps(legacy), "2026-01-01"),
    )
    chat_history_db.commit()
    stored = chat_history.load_chat_history(VIP_CHAT_ID)
    assert len(stored) == MAX_STORED_HISTORY
    assert stored[0]["content"] == "legacy10"
    assert stored[-1]["content"] == "legacy59"