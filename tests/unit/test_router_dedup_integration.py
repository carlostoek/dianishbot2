"""Router dedup integration: duplicate updates must not double-act."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import auth_users
import state
from handlers.router import _dedup_cache, process_update


VIP_ID = 777001
VIP_CHAT_ID = 777001
ADMIN_ID = 555001
BC_ID = "bc_test"


@pytest.fixture(autouse=True)
def _reset(tmp_path):
    users_file = tmp_path / "authorized.json"
    auth_users.configure(
        users_file=str(users_file),
        max_users=5,
        seed_user_ids=[VIP_ID],
        admin_id=ADMIN_ID,
    )
    auth_users.set_admin_id(ADMIN_ID)
    _dedup_cache.clear()
    state.history.clear()
    state.reply_gen.clear()
    state.timers.clear()
    state.timer_schedule.clear()
    state.pending_approval.clear()
    state.chat_bc.clear()
    state.pending_msg.clear()
    state.connections.clear()
    yield
    _dedup_cache.clear()
    state.history.clear()
    state.reply_gen.clear()
    state.timers.clear()
    state.timer_schedule.clear()
    state.pending_approval.clear()
    state.chat_bc.clear()
    state.pending_msg.clear()
    state.connections.clear()


@pytest.fixture
def admin_user(make_user):
    return make_user(user_id=ADMIN_ID, username="diana_admin", first_name="Diana")


@pytest.fixture
def pending_entry():
    return {
        "chat_id": VIP_CHAT_ID,
        "bc_id": BC_ID,
        "username": "testvip",
        "gen": 1,
        "variants": [{"response": "hola", "confidence": 90, "topic": "general"}],
        "selected": 0,
        "regenerating": False,
    }


@pytest.mark.asyncio
async def test_duplicate_approve_callback_single_delivery(
    make_mock_callback_update, make_context, pending_entry, admin_user,
):
    """Same callback_query.id twice → deliver_vip_response at most once."""
    ex_id = 42
    state.pending_approval[ex_id] = pending_entry
    state.reply_gen[VIP_CHAT_ID] = 1

    upd1 = make_mock_callback_update(data=f"a:approve:{ex_id}", user=admin_user)
    upd1.update_id = 300
    upd1.callback_query.id = "cb_dup_approve"
    upd2 = make_mock_callback_update(data=f"a:approve:{ex_id}", user=admin_user)
    upd2.update_id = 301
    upd2.callback_query.id = "cb_dup_approve"
    ctx = make_context()

    with (
        patch("handlers.callbacks.DIANA_ADMIN_CHAT_ID", ADMIN_ID),
        patch(
            "handlers.callbacks.deliver_vip_response",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_deliver,
        patch("handlers.callbacks.update_rating"),
        patch("handlers.callbacks.schedule_memory_extract"),
    ):
        await process_update(upd1, ctx)
        await process_update(upd2, ctx)

    assert mock_deliver.await_count == 1
    assert ex_id not in state.pending_approval


@pytest.mark.asyncio
async def test_duplicate_business_message_single_timer(
    make_update, make_context, make_user, make_chat, chat_history_db,
):
    """Same update_id twice → only one auto_reply timer scheduled."""
    vip = make_user(user_id=VIP_ID, username="testvip")
    chat = make_chat(chat_id=VIP_CHAT_ID)
    upd = make_update(
        text="hola",
        user=vip,
        chat=chat,
        update_id=500,
        business_connection_id=BC_ID,
    )
    ctx = make_context()
    create_calls: list[object] = []

    def capture_task(coro):
        create_calls.append(coro)
        task = MagicMock()
        task.cancel = MagicMock()
        return task

    with (
        patch("handlers.business.compute_reply_delay", return_value=30.0),
        patch("handlers.business.asyncio.create_task", side_effect=capture_task),
    ):
        await process_update(upd, ctx)
        await process_update(upd, ctx)

    assert len(create_calls) == 1
    assert state.reply_gen[VIP_CHAT_ID] == 1