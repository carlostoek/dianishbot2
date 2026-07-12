"""Unit tests for deliver_sequential_messages (multi-message human-like delivery)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import state
from services import chat_history
from services.delivery import deliver_sequential_messages

CHAT_ID = 888001
BC_ID = "bc_multi"
USERNAME = "promo_user"
MSG_A = "Holaaa 💕\nTe mando mis promos 🔥"
MSG_B = "*Precios en pesos mexicanos\n♥ Encanto Inicial"


@pytest.fixture(autouse=True)
def _reset_state():
    state.history.clear()
    state.pending_msg.clear()
    state.reply_gen.clear()
    yield
    state.history.clear()
    state.pending_msg.clear()
    state.reply_gen.clear()


@pytest.mark.asyncio
async def test_two_messages_single_read_receipt_and_order(bot):
    """One mark_as_read, then send Message A before B (no second read cycle)."""
    state.pending_msg[CHAT_ID] = 42
    mock_mark = AsyncMock()
    mock_typing = AsyncMock()
    mock_sleep = AsyncMock()

    with (
        patch("services.delivery.asyncio.sleep", mock_sleep),
        patch("services.delivery.mark_as_read", mock_mark),
        patch("services.delivery.simulate_typing", mock_typing),
    ):
        ok = await deliver_sequential_messages(
            bot,
            chat_id=CHAT_ID,
            bc_id=BC_ID,
            username=USERNAME,
            texts=[MSG_A, MSG_B],
            persist=False,
        )

    assert ok is True
    mock_mark.assert_awaited_once_with(bot, BC_ID, CHAT_ID, 42)
    assert bot.send_message.await_count == 2
    first = bot.send_message.await_args_list[0].kwargs
    second = bot.send_message.await_args_list[1].kwargs
    assert first["text"] == MSG_A
    assert first["chat_id"] == CHAT_ID
    assert first["business_connection_id"] == BC_ID
    assert second["text"] == MSG_B
    assert second["business_connection_id"] == BC_ID
    assert mock_typing.await_count == 2


@pytest.mark.asyncio
async def test_inter_message_gap_between_sends(bot, monkeypatch):
    """Short random gap between msg1 and msg2; not after last."""
    # No pending_msg → skip pre-read sleep so only inter-gap uses random.uniform
    sleep_calls: list[float] = []
    uniform_calls: list[tuple[float, float]] = []

    async def capture_sleep(sec):
        sleep_calls.append(sec)

    def capture_uniform(a, b):
        uniform_calls.append((a, b))
        return 2.0

    monkeypatch.setattr("services.delivery.random.uniform", capture_uniform)

    with (
        patch("services.delivery.asyncio.sleep", side_effect=capture_sleep),
        patch("services.delivery.mark_as_read", new_callable=AsyncMock) as mock_mark,
        patch("services.delivery.simulate_typing", new_callable=AsyncMock),
    ):
        ok = await deliver_sequential_messages(
            bot,
            chat_id=CHAT_ID,
            bc_id=BC_ID,
            username=USERNAME,
            texts=[MSG_A, MSG_B],
            persist=False,
            inter_gap_sec=(1.5, 3.0),
        )

    assert ok is True
    mock_mark.assert_not_awaited()
    assert uniform_calls == [(1.5, 3.0)]
    assert sleep_calls == [2.0]



@pytest.mark.asyncio
async def test_should_abort_before_second_message_returns_false(bot):
    """If should_abort becomes true before msg2, stop and return False."""
    state.pending_msg[CHAT_ID] = 11
    calls = {"n": 0}

    def abort_after_first():
        # First check (msg1): False; second check (msg2): True
        calls["n"] += 1
        return calls["n"] >= 2

    with (
        patch("services.delivery.asyncio.sleep", new_callable=AsyncMock),
        patch("services.delivery.mark_as_read", new_callable=AsyncMock),
        patch("services.delivery.simulate_typing", new_callable=AsyncMock),
    ):
        ok = await deliver_sequential_messages(
            bot,
            chat_id=CHAT_ID,
            bc_id=BC_ID,
            username=USERNAME,
            texts=[MSG_A, MSG_B],
            should_abort=abort_after_first,
            persist=False,
        )

    assert ok is False
    assert bot.send_message.await_count == 1
    assert bot.send_message.await_args.kwargs["text"] == MSG_A


@pytest.mark.asyncio
async def test_send_failure_returns_false(bot):
    """If any send fails, overall result is False (incomplete sequence)."""
    state.pending_msg[CHAT_ID] = 3
    bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))

    with (
        patch("services.delivery.asyncio.sleep", new_callable=AsyncMock),
        patch("services.delivery.mark_as_read", new_callable=AsyncMock),
        patch("services.delivery.simulate_typing", new_callable=AsyncMock),
    ):
        ok = await deliver_sequential_messages(
            bot,
            chat_id=CHAT_ID,
            bc_id=BC_ID,
            username=USERNAME,
            texts=[MSG_A, MSG_B],
            persist=False,
        )

    assert ok is False
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_second_send_failure_returns_false_after_first(bot):
    """Msg1 ok + msg2 fail → False (partial success is not full success)."""
    state.pending_msg[CHAT_ID] = 5
    bot.send_message = AsyncMock(
        side_effect=[MagicMock(), RuntimeError("msg2 failed")]
    )

    with (
        patch("services.delivery.asyncio.sleep", new_callable=AsyncMock),
        patch("services.delivery.mark_as_read", new_callable=AsyncMock),
        patch("services.delivery.simulate_typing", new_callable=AsyncMock),
    ):
        ok = await deliver_sequential_messages(
            bot,
            chat_id=CHAT_ID,
            bc_id=BC_ID,
            username=USERNAME,
            texts=[MSG_A, MSG_B],
            persist=False,
        )

    assert ok is False
    assert bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_persist_true_appends_each_assistant_message(chat_history_db, bot):
    """When persist=True, each successful send appends assistant history."""
    state.pending_msg[CHAT_ID] = 9

    with (
        patch("services.delivery.asyncio.sleep", new_callable=AsyncMock),
        patch("services.delivery.mark_as_read", new_callable=AsyncMock),
        patch("services.delivery.simulate_typing", new_callable=AsyncMock),
    ):
        ok = await deliver_sequential_messages(
            bot,
            chat_id=CHAT_ID,
            bc_id=BC_ID,
            username=USERNAME,
            texts=[MSG_A, MSG_B],
            persist=True,
        )

    assert ok is True
    stored = chat_history.load_chat_history(CHAT_ID)
    assert len(stored) == 2
    assert stored[0] == {"role": "assistant", "content": MSG_A}
    assert stored[1] == {"role": "assistant", "content": MSG_B}


@pytest.mark.asyncio
async def test_persist_false_does_not_append(chat_history_db, bot):
    """persist=False (promo path) must not write chat history."""
    state.pending_msg[CHAT_ID] = 9

    with (
        patch("services.delivery.asyncio.sleep", new_callable=AsyncMock),
        patch("services.delivery.mark_as_read", new_callable=AsyncMock),
        patch("services.delivery.simulate_typing", new_callable=AsyncMock),
    ):
        ok = await deliver_sequential_messages(
            bot,
            chat_id=CHAT_ID,
            bc_id=BC_ID,
            username=USERNAME,
            texts=[MSG_A, MSG_B],
            persist=False,
        )

    assert ok is True
    assert chat_history.load_chat_history(CHAT_ID) == []


@pytest.mark.asyncio
async def test_single_message_still_one_read_and_one_send(bot):
    """N=1: one read, one typing, one send; no inter-gap uniform call."""
    state.pending_msg[CHAT_ID] = 1
    with (
        patch("services.delivery.asyncio.sleep", new_callable=AsyncMock),
        patch("services.delivery.mark_as_read", new_callable=AsyncMock) as mock_mark,
        patch("services.delivery.simulate_typing", new_callable=AsyncMock) as mock_typing,
        patch("services.delivery.random.uniform", return_value=2.5) as mock_uniform,
    ):
        ok = await deliver_sequential_messages(
            bot,
            chat_id=CHAT_ID,
            bc_id=BC_ID,
            username=USERNAME,
            texts=[MSG_A],
            persist=False,
            inter_gap_sec=(1.5, 3.0),
        )

    assert ok is True
    mock_mark.assert_awaited_once()
    bot.send_message.assert_awaited_once()
    mock_typing.assert_awaited_once()
    for c in mock_uniform.call_args_list:
        args = c.args if c.args else ()
        if len(args) >= 2 and args == (1.5, 3.0):
            pytest.fail("inter-gap random.uniform must not run for single message")
