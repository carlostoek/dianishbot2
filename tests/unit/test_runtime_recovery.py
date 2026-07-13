"""Tests for runtime state persistence and startup recovery."""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import state
from handlers.recovery import recover_runtime_on_startup
from handlers.timer import auto_reply, compute_reply_delay
from state import (
    history,
    reply_gen,
    timers,
    timer_schedule,
    pending_approval,
    chat_bc,
    pending_msg,
    _save_runtime_state,
    _load_runtime_state,
    _build_runtime_snapshot,
)


@pytest.fixture
def runtime_file(tmp_path, monkeypatch):
    path = tmp_path / "diana_runtime.json"
    monkeypatch.setattr(state, "RUNTIME_STATE_FILE", str(path))
    monkeypatch.setattr("config.RUNTIME_STATE_FILE", str(path))
    yield path
    if path.exists():
        path.unlink()


@pytest.fixture(autouse=True)
def _reset_state():
    history.clear()
    reply_gen.clear()
    timers.clear()
    timer_schedule.clear()
    pending_approval.clear()
    chat_bc.clear()
    pending_msg.clear()
    yield
    history.clear()
    reply_gen.clear()
    timers.clear()
    timer_schedule.clear()
    pending_approval.clear()
    chat_bc.clear()
    pending_msg.clear()


def test_save_snapshot_on_timer_schedule(runtime_file):
    chat_id = 100
    history[chat_id] = [{"role": "user", "content": "hola"}]
    reply_gen[chat_id] = 1
    chat_bc[chat_id] = "bc_test"
    pending_msg[chat_id] = 42
    fire_at = (datetime.now() + timedelta(minutes=2)).isoformat(timespec="seconds")
    timer_schedule[chat_id] = {
        "username": "vip",
        "bc_id": "bc_test",
        "gen": 1,
        "fire_at": fire_at,
    }
    _save_runtime_state()

    data = json.loads(runtime_file.read_text(encoding="utf-8"))
    assert len(data["timers"]) == 1
    assert data["timers"][0]["chat_id"] == 100
    assert data["history"]["100"][0]["content"] == "hola"
    assert data["chat_bc"]["100"] == "bc_test"


@pytest.mark.asyncio
async def test_restore_overdue_timer_fires_immediately(runtime_file):
    chat_id = 200
    history[chat_id] = [{"role": "user", "content": "hey"}]
    fire_at = (datetime.now() - timedelta(seconds=30)).isoformat(timespec="seconds")
    runtime_file.write_text(
        json.dumps({
            "version": 1,
            "reply_gen": {"200": 1},
            "chat_bc": {"200": "bc_x"},
            "chat_meta": {},
            "pending_msg": {},
            "history": {"200": [{"role": "user", "content": "hey"}]},
            "timers": [{
                "chat_id": 200,
                "username": "vip",
                "bc_id": "bc_x",
                "gen": 1,
                "fire_at": fire_at,
            }],
            "pending_approval": {},
        }),
        encoding="utf-8",
    )

    delays = []

    async def capture_auto_reply(bot, chat_id, username, bc_id, gen, *, delay_sec=None):
        delays.append(delay_sec)
        timers.pop(chat_id, None)

    bot = AsyncMock()
    with (
        patch("handlers.recovery.auto_reply", side_effect=capture_auto_reply),
        patch("handlers.recovery.DIANA_ADMIN_CHAT_ID", None),
    ):
        n_timers, n_drafts = await recover_runtime_on_startup(bot)
    if timers:
        await asyncio.gather(*timers.values())

    assert n_timers == 1
    assert n_drafts == 0
    assert delays[0] == 0.0


@pytest.mark.asyncio
async def test_restore_future_timer_uses_remaining(runtime_file):
    chat_id = 300
    history[chat_id] = [{"role": "user", "content": "hola"}]
    fire_at = (datetime.now() + timedelta(seconds=90)).isoformat(timespec="seconds")
    runtime_file.write_text(
        json.dumps({
            "version": 1,
            "reply_gen": {"300": 1},
            "chat_bc": {"300": "bc_y"},
            "chat_meta": {},
            "pending_msg": {},
            "history": {"300": [{"role": "user", "content": "hola"}]},
            "timers": [{
                "chat_id": 300,
                "username": "vip",
                "bc_id": "bc_y",
                "gen": 1,
                "fire_at": fire_at,
            }],
            "pending_approval": {},
        }),
        encoding="utf-8",
    )

    delays = []

    async def capture_auto_reply(bot, chat_id, username, bc_id, gen, *, delay_sec=None):
        delays.append(delay_sec)
        timers.pop(chat_id, None)

    bot = AsyncMock()
    with (
        patch("handlers.recovery.auto_reply", side_effect=capture_auto_reply),
        patch("handlers.recovery.DIANA_ADMIN_CHAT_ID", None),
    ):
        await recover_runtime_on_startup(bot)
    if timers:
        await asyncio.gather(*timers.values())

    assert 85 <= delays[0] <= 95


@pytest.mark.asyncio
async def test_skip_timer_when_last_message_is_assistant(runtime_file):
    chat_id = 400
    fire_at = (datetime.now() + timedelta(minutes=1)).isoformat(timespec="seconds")
    runtime_file.write_text(
        json.dumps({
            "version": 1,
            "reply_gen": {"400": 1},
            "chat_bc": {"400": "bc_z"},
            "chat_meta": {},
            "pending_msg": {},
            "history": {
                "400": [
                    {"role": "user", "content": "hola"},
                    {"role": "assistant", "content": "yo contesté"},
                ],
            },
            "timers": [{
                "chat_id": 400,
                "username": "vip",
                "bc_id": "bc_z",
                "gen": 1,
                "fire_at": fire_at,
            }],
            "pending_approval": {},
        }),
        encoding="utf-8",
    )

    bot = AsyncMock()
    with (
        patch("handlers.recovery.auto_reply", new_callable=AsyncMock) as mock_ar,
        patch("handlers.recovery.DIANA_ADMIN_CHAT_ID", None),
    ):
        n_timers, _ = await recover_runtime_on_startup(bot)

    assert n_timers == 0
    mock_ar.assert_not_called()
    assert chat_id not in timer_schedule


def test_restore_pending_approval_enables_callbacks(runtime_file):
    runtime_file.write_text(
        json.dumps({
            "version": 1,
            "reply_gen": {"500": 2},
            "chat_bc": {"500": "bc_d"},
            "chat_meta": {},
            "pending_msg": {},
            "history": {"500": [{"role": "user", "content": "test"}]},
            "timers": [],
            "pending_approval": {
                "99": {
                    "chat_id": 500,
                    "bc_id": "bc_d",
                    "username": "vip",
                    "gen": 2,
                    "variants": [
                        {"response": "hola", "confidence": 80, "topic": "general"},
                    ],
                    "selected": 0,
                    "regenerating": True,
                },
            },
        }),
        encoding="utf-8",
    )
    _load_runtime_state()

    assert 99 in pending_approval
    assert pending_approval[99]["regenerating"] is False
    assert pending_approval[99]["variants"][0]["response"] == "hola"


def test_cancel_timer_clears_snapshot(runtime_file):
    chat_id = 600
    history[chat_id] = [{"role": "user", "content": "x"}]
    timer_schedule[chat_id] = {
        "username": "vip",
        "bc_id": "bc",
        "gen": 1,
        "fire_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_runtime_state()
    assert runtime_file.exists()

    timer_schedule.pop(chat_id)
    _save_runtime_state()
    assert not runtime_file.exists()


@pytest.mark.asyncio
async def test_startup_notify_admin_when_recovered(runtime_file):
    chat_id = 700
    history[chat_id] = [{"role": "user", "content": "hola"}]
    fire_at = (datetime.now() - timedelta(seconds=5)).isoformat(timespec="seconds")
    runtime_file.write_text(
        json.dumps({
            "version": 1,
            "reply_gen": {"700": 1},
            "chat_bc": {"700": "bc_n"},
            "chat_meta": {},
            "pending_msg": {},
            "history": {"700": [{"role": "user", "content": "hola"}]},
            "timers": [{
                "chat_id": 700,
                "username": "vip",
                "bc_id": "bc_n",
                "gen": 1,
                "fire_at": fire_at,
            }],
            "pending_approval": {},
        }),
        encoding="utf-8",
    )

    bot = AsyncMock()

    async def noop_ar(*args, **kwargs):
        timers.pop(chat_id, None)

    with (
        patch("handlers.recovery.auto_reply", side_effect=noop_ar),
        patch("handlers.recovery.DIANA_ADMIN_CHAT_ID", 12345),
    ):
        await recover_runtime_on_startup(bot)

    bot.send_message.assert_awaited_once()
    text = bot.send_message.await_args.kwargs.get("text") or bot.send_message.await_args[1].get("text", "")
    assert "Recuperación tras reinicio" in text
    assert "1 timer" in text


@pytest.mark.asyncio
async def test_auto_reply_accepts_explicit_delay():
    chat_id = 800
    gen = 1
    reply_gen[chat_id] = gen
    history[chat_id] = [{"role": "user", "content": "hola"}]

    slept = []

    async def track_sleep(delay):
        slept.append(delay)

    with (
        patch("asyncio.sleep", side_effect=track_sleep),
        patch("handlers.timer.get_diana_response", new_callable=AsyncMock, return_value=(None, 0, "general", False, "", None)),
        patch("handlers.timer._finish_timer"),
    ):
        await auto_reply(AsyncMock(), chat_id, "vip", "bc", gen, delay_sec=42.5)

    assert slept == [42.5]


def test_compute_reply_delay_supervised(monkeypatch):
    monkeypatch.setattr("handlers.timer.APPROVAL_MODE", True)
    monkeypatch.setattr("handlers.timer.SILENCE_MINUTES", 2)
    assert compute_reply_delay() == 120.0


def test_empty_snapshot_deletes_file(runtime_file):
    _save_runtime_state()
    assert not runtime_file.exists()