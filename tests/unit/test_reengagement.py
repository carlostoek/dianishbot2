"""Unit tests for VIP idle re-engagement state, eligibility, touch, send, and scanner."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import reengagement


@pytest.fixture(autouse=True)
def _reengage_state(tmp_path, monkeypatch):
    state_file = tmp_path / "diana_reengage_state.json"
    monkeypatch.setattr(reengagement, "REENGAGE_STATE_FILE", str(state_file))
    # Reset any module-level cache if present
    if hasattr(reengagement, "_reset_for_tests"):
        reengagement._reset_for_tests()
    yield state_file
    if hasattr(reengagement, "_reset_for_tests"):
        reengagement._reset_for_tests()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _seed_eligible(
    chat_id: int = 1001,
    *,
    bc_id: str = "bc-eligible",
    username: str = "vip_user",
    idle_days: float = 2,
    now: datetime | None = None,
) -> datetime:
    """Seed state so chat is eligible at `now` (default: 2026-07-10 12:00 UTC)."""
    now = now or datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    inbound = now - timedelta(days=idle_days)
    reengagement.touch_inbound(chat_id, bc_id, username, now=inbound)
    return now

# ── is_eligible (pure) ──────────────────────────────────────────────


def test_is_eligible_when_idle_enough_and_no_reengage_this_cycle():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    inbound = now - timedelta(days=2)
    entry = {
        "last_vip_inbound_at": _iso(inbound),
        "reengage_sent_for_inbound_at": None,
        "last_reengage_at": None,
    }
    assert reengagement.is_eligible(entry, now=now, idle_days=2) is True


def test_is_eligible_false_when_still_within_idle_window():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    inbound = now - timedelta(days=1, hours=23)
    entry = {
        "last_vip_inbound_at": _iso(inbound),
        "reengage_sent_for_inbound_at": None,
    }
    assert reengagement.is_eligible(entry, now=now, idle_days=2) is False


def test_is_eligible_false_when_already_reengaged_this_cycle():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    inbound = now - timedelta(days=3)
    stamp = _iso(inbound)
    entry = {
        "last_vip_inbound_at": stamp,
        "reengage_sent_for_inbound_at": stamp,
        "last_reengage_at": _iso(now - timedelta(days=1)),
    }
    assert reengagement.is_eligible(entry, now=now, idle_days=2) is False


def test_is_eligible_true_again_after_new_inbound_cycle():
    """Prior reengage for old stamp; new inbound stamp + idle → eligible again."""
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    old_inbound = now - timedelta(days=10)
    new_inbound = now - timedelta(days=2)
    entry = {
        "last_vip_inbound_at": _iso(new_inbound),
        "reengage_sent_for_inbound_at": _iso(old_inbound),
        "last_reengage_at": _iso(now - timedelta(days=5)),
    }
    assert reengagement.is_eligible(entry, now=now, idle_days=2) is True


def test_is_eligible_false_without_last_vip_inbound_at():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    assert reengagement.is_eligible({}, now=now, idle_days=2) is False
    assert (
        reengagement.is_eligible(
            {"last_vip_inbound_at": None, "reengage_sent_for_inbound_at": None},
            now=now,
            idle_days=2,
        )
        is False
    )


def test_is_eligible_at_exact_idle_threshold():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    inbound = now - timedelta(days=2)
    entry = {
        "last_vip_inbound_at": _iso(inbound),
        "reengage_sent_for_inbound_at": None,
    }
    assert reengagement.is_eligible(entry, now=now, idle_days=2) is True


# ── ensure_seeded (cold start = now) ─────────────────────────────────


def test_ensure_seeded_sets_last_inbound_to_now(tmp_path):
    now = datetime(2026, 7, 10, 15, 30, tzinfo=timezone.utc)
    reengagement.ensure_seeded(42, bc_id="bc-1", username="vip_user", now=now)

    entry = reengagement.get_entry(42)
    assert entry is not None
    assert entry["last_vip_inbound_at"] == _iso(now)
    assert entry["reengage_sent_for_inbound_at"] is None
    assert entry["bc_id"] == "bc-1"
    assert entry["username"] == "vip_user"
    # Cold start: not immediately eligible
    assert reengagement.is_eligible(entry, now=now, idle_days=2) is False


def test_ensure_seeded_is_idempotent_does_not_overwrite_existing():
    t0 = datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    reengagement.ensure_seeded(7, bc_id="bc-a", username="a", now=t0)
    reengagement.ensure_seeded(7, bc_id="bc-b", username="b", now=t1)

    entry = reengagement.get_entry(7)
    assert entry["last_vip_inbound_at"] == _iso(t0)
    # bc_id/username may refresh, but stamp must not move on re-seed
    assert entry["last_vip_inbound_at"] != _iso(t1)


def test_ensure_seeded_not_eligible_until_idle_days_pass():
    seed_at = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    reengagement.ensure_seeded(99, now=seed_at)
    entry = reengagement.get_entry(99)
    almost = seed_at + timedelta(days=1, hours=23)
    assert reengagement.is_eligible(entry, now=almost, idle_days=2) is False
    ready = seed_at + timedelta(days=2)
    assert reengagement.is_eligible(entry, now=ready, idle_days=2) is True


# ── touch_inbound ────────────────────────────────────────────────────


def test_touch_inbound_sets_stamp_and_metadata():
    now = datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc)
    reengagement.touch_inbound(100, "bc-x", "alice", now=now)
    entry = reengagement.get_entry(100)
    assert entry["last_vip_inbound_at"] == _iso(now)
    assert entry["bc_id"] == "bc-x"
    assert entry["username"] == "alice"


def test_touch_inbound_resets_cycle_after_prior_reengage():
    """After reengage for stamp A, new VIP inbound (stamp B) makes entry eligible again once idle."""
    t_old = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    reengagement.touch_inbound(55, "bc-1", "bob", now=t_old)

    # Simulate successful reengage for that cycle (state write like WU2 mark)
    state = reengagement._load_state()
    key = "55"
    stamp = state["users"][key]["last_vip_inbound_at"]
    state["users"][key]["reengage_sent_for_inbound_at"] = stamp
    state["users"][key]["last_reengage_at"] = _iso(t_old + timedelta(days=2))
    reengagement._save_state(state)

    entry = reengagement.get_entry(55)
    assert reengagement.is_eligible(
        entry, now=t_old + timedelta(days=5), idle_days=2
    ) is False

    t_new = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    reengagement.touch_inbound(55, "bc-1", "bob", now=t_new)
    entry = reengagement.get_entry(55)
    assert entry["last_vip_inbound_at"] == _iso(t_new)
    # Still holding prior reengage marker for old stamp → new cycle
    assert entry["reengage_sent_for_inbound_at"] == stamp
    assert entry["reengage_sent_for_inbound_at"] != entry["last_vip_inbound_at"]
    assert reengagement.is_eligible(
        entry, now=t_new + timedelta(days=2), idle_days=2
    ) is True


def test_touch_inbound_overwrites_seeded_stamp():
    seed = datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc)
    reengagement.ensure_seeded(11, bc_id="", username="", now=seed)
    touch = datetime(2026, 7, 9, 18, 0, tzinfo=timezone.utc)
    reengagement.touch_inbound(11, "bc-11", "carol", now=touch)
    entry = reengagement.get_entry(11)
    assert entry["last_vip_inbound_at"] == _iso(touch)
    assert entry["bc_id"] == "bc-11"


# ── persist / load (atomic JSON state) ───────────────────────────────


def test_persist_and_load_survives_reload(_reengage_state):
    state_file: Path = _reengage_state
    now = datetime(2026, 7, 7, 8, 0, tzinfo=timezone.utc)
    reengagement.touch_inbound(200, "bc-200", "dave", now=now)

    assert state_file.exists()
    raw = json.loads(state_file.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert "200" in raw["users"]
    assert raw["users"]["200"]["last_vip_inbound_at"] == _iso(now)

    # Simulate process restart: clear any in-memory cache and re-read
    if hasattr(reengagement, "_reset_for_tests"):
        reengagement._reset_for_tests()

    entry = reengagement.get_entry(200)
    assert entry["last_vip_inbound_at"] == _iso(now)
    assert entry["bc_id"] == "bc-200"
    assert entry["username"] == "dave"
    assert reengagement.is_eligible(
        entry, now=now + timedelta(days=2), idle_days=2
    ) is True


def test_persist_uses_atomic_write_pattern(_reengage_state, monkeypatch):
    """State save must use .tmp + os.replace (no half-written final file on crash)."""
    calls: list[tuple] = []
    real_replace = reengagement.os.replace

    def tracking_replace(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(reengagement.os, "replace", tracking_replace)
    reengagement.touch_inbound(
        1, "bc", "u", now=datetime(2026, 7, 10, tzinfo=timezone.utc)
    )
    assert calls, "expected os.replace for atomic persist"
    src, dst = calls[-1]
    assert src.endswith(".tmp") or Path(src).suffix == ".tmp"
    assert dst == str(_reengage_state)


def test_load_missing_state_returns_empty_users(_reengage_state):
    state = reengagement._load_state()
    assert state["version"] == 1
    assert state["users"] == {}
    assert state.get("last_scan_at") is None


def test_no_llm_or_approval_imports():
    """Module must stay isolated from LLM / approval / deliver_vip_response paths."""
    import ast
    from pathlib import Path

    src = Path(reengagement.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {
        "services.llm",
        "services.delivery",
        "services.training",
        "handlers.callbacks",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in banned, f"banned import: {node.module}"
            assert not node.module.startswith("services.llm"), node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in banned
    assert "deliver_vip_response" not in src
    assert "reply_gen" not in src


# ── maybe_reengage (send path) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_maybe_reengage_success_marks_cycle_and_notifies(monkeypatch):
    now = _seed_eligible(1001)
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock())
    append_calls: list[tuple] = []
    notify_calls: list[dict] = []

    monkeypatch.setattr(
        "services.auth_service.is_authorized", lambda *a, **k: True
    )
    monkeypatch.setattr("services.sandbox.is_active", lambda cid: False)
    monkeypatch.setattr(
        "services.chat_history.append_message",
        lambda chat_id, role, content, **kw: append_calls.append(
            (chat_id, role, content)
        ),
    )

    async def capture_notify(*args, **kwargs):
        notify_calls.append({"args": args, "kwargs": kwargs})

    # Patch notify helper if present; also allow inline DM via send_message
    if hasattr(reengagement, "_notify_diana"):
        monkeypatch.setattr(reengagement, "_notify_diana", capture_notify)

    import state as state_mod

    state_mod.timers.pop(1001, None)
    state_mod.pending_approval.clear()

    ok = await reengagement.maybe_reengage(bot, 1001, now=now)
    assert ok is True

    entry = reengagement.get_entry(1001)
    assert entry["reengage_sent_for_inbound_at"] == entry["last_vip_inbound_at"]
    assert entry["last_reengage_at"] is not None

    bot.send_message.assert_awaited()
    send_kwargs = bot.send_message.await_args.kwargs
    assert send_kwargs.get("business_connection_id") == "bc-eligible"
    assert send_kwargs.get("chat_id") == 1001
    text = send_kwargs.get("text") or (
        bot.send_message.await_args.args[1]
        if len(bot.send_message.await_args.args) > 1
        else None
    )
    from config import REENGAGE_TEMPLATES

    assert text in REENGAGE_TEMPLATES

    assert append_calls, "expected append_message for assistant template"
    assert append_calls[0][0] == 1001
    assert append_calls[0][1] == "assistant"
    assert append_calls[0][2] in REENGAGE_TEMPLATES

    # Diana notified: either via _notify_diana or a second send_message to admin
    from config import DIANA_ADMIN_CHAT_ID

    if notify_calls:
        assert len(notify_calls) >= 1
        n = notify_calls[0]
        assert n["kwargs"].get("chat_id") == 1001
        assert n["kwargs"].get("username") == "vip_user"
        assert n["kwargs"].get("template") in REENGAGE_TEMPLATES
        assert n["kwargs"].get("idle_days") is not None
        # bot is first positional when helper is patched
        assert n["args"], "expected bot as first positional arg to _notify_diana"
    else:
        admin_sends = [
            c
            for c in bot.send_message.await_args_list
            if (c.kwargs.get("chat_id") == DIANA_ADMIN_CHAT_ID)
            or (c.args and c.args[0] == DIANA_ADMIN_CHAT_ID)
        ]
        assert admin_sends, "expected DM to DIANA_ADMIN_CHAT_ID after success"


@pytest.mark.asyncio
async def test_maybe_reengage_send_failure_does_not_mark_cycle(monkeypatch):
    now = _seed_eligible(1002)
    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))

    monkeypatch.setattr(
        "services.auth_service.is_authorized", lambda *a, **k: True
    )
    monkeypatch.setattr("services.sandbox.is_active", lambda cid: False)
    monkeypatch.setattr(
        "services.chat_history.append_message",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no append on fail")),
    )

    import state as state_mod

    state_mod.timers.pop(1002, None)
    state_mod.pending_approval.clear()

    ok = await reengagement.maybe_reengage(bot, 1002, now=now)
    assert ok is False

    entry = reengagement.get_entry(1002)
    assert entry["reengage_sent_for_inbound_at"] is None
    assert entry["last_reengage_at"] is None


@pytest.mark.asyncio
async def test_maybe_reengage_pre_send_stamp_mismatch_aborts(monkeypatch):
    """If VIP inbound advances stamp after eligibility snapshot, do not send."""
    now = _seed_eligible(1003)
    bot = AsyncMock()
    bot.send_message = AsyncMock()

    monkeypatch.setattr(
        "services.auth_service.is_authorized", lambda *a, **k: True
    )
    monkeypatch.setattr("services.sandbox.is_active", lambda cid: False)

    import state as state_mod

    state_mod.timers.pop(1003, None)
    state_mod.pending_approval.clear()

    original_load = reengagement._load_state_unlocked
    call_count = {"n": 0}

    def load_with_race():
        data = original_load()
        call_count["n"] += 1
        # After first eligibility snapshot reads, inject a newer inbound stamp
        # so pre-send recheck sees a mismatch.
        if call_count["n"] >= 2:
            key = "1003"
            if key in data.get("users", {}):
                data["users"][key]["last_vip_inbound_at"] = _iso(now)
        return data

    monkeypatch.setattr(reengagement, "_load_state_unlocked", load_with_race)

    ok = await reengagement.maybe_reengage(bot, 1003, now=now)
    assert ok is False
    bot.send_message.assert_not_awaited()
    # Cycle not marked for the obsolete stamp
    # (entry may have been mutated by race fixture; reengage marker must stay unset)
    state = reengagement._load_state()
    # Prefer get_entry if load_unlocked was patched oddly
    users = state.get("users", {})
    entry = users.get("1003") or reengagement.get_entry(1003)
    if entry:
        assert entry.get("reengage_sent_for_inbound_at") in (None, entry.get("last_vip_inbound_at"))
        # Must not have marked the *old* cycle; if marker set it must equal current stamp only after real success
        if entry.get("reengage_sent_for_inbound_at") is not None:
            # abort path should never mark
            pytest.fail("cycle should not be marked when pre-send stamp mismatches")


@pytest.mark.asyncio
async def test_maybe_reengage_post_send_stamp_mismatch_does_not_mark(monkeypatch):
    """Send may complete, but mark is aborted if stamp changed mid-flight."""
    now = _seed_eligible(1004)
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock())
    append_calls: list = []

    monkeypatch.setattr(
        "services.auth_service.is_authorized", lambda *a, **k: True
    )
    monkeypatch.setattr("services.sandbox.is_active", lambda cid: False)
    monkeypatch.setattr(
        "services.chat_history.append_message",
        lambda *a, **k: append_calls.append(a),
    )

    import state as state_mod

    state_mod.timers.pop(1004, None)
    state_mod.pending_approval.clear()

    original_stamp = reengagement.get_entry(1004)["last_vip_inbound_at"]

    async def send_then_touch(*args, **kwargs):
        # Concurrent VIP inbound during send
        reengagement.touch_inbound(1004, "bc-eligible", "vip_user", now=now)
        return MagicMock()

    bot.send_message = AsyncMock(side_effect=send_then_touch)

    ok = await reengagement.maybe_reengage(bot, 1004, now=now)
    assert ok is False

    entry = reengagement.get_entry(1004)
    # New stamp from touch; must not mark reengage for either cycle incorrectly
    assert entry["last_vip_inbound_at"] != original_stamp
    assert entry["reengage_sent_for_inbound_at"] is None
    assert not append_calls


@pytest.mark.asyncio
async def test_maybe_reengage_skips_unauthorized(monkeypatch):
    now = _seed_eligible(1005)
    bot = AsyncMock()
    monkeypatch.setattr(
        "services.auth_service.is_authorized", lambda *a, **k: False
    )
    monkeypatch.setattr("services.sandbox.is_active", lambda cid: False)

    ok = await reengagement.maybe_reengage(bot, 1005, now=now)
    assert ok is False
    bot.send_message.assert_not_awaited()
    assert reengagement.get_entry(1005)["reengage_sent_for_inbound_at"] is None


@pytest.mark.asyncio
async def test_maybe_reengage_skips_sandbox(monkeypatch):
    now = _seed_eligible(1006)
    bot = AsyncMock()
    monkeypatch.setattr(
        "services.auth_service.is_authorized", lambda *a, **k: True
    )
    monkeypatch.setattr("services.sandbox.is_active", lambda cid: True)

    ok = await reengagement.maybe_reengage(bot, 1006, now=now)
    assert ok is False
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_reengage_skips_missing_bc_id(monkeypatch):
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    reengagement.touch_inbound(
        1007, "", "no_bc", now=now - timedelta(days=2)
    )
    bot = AsyncMock()
    monkeypatch.setattr(
        "services.auth_service.is_authorized", lambda *a, **k: True
    )
    monkeypatch.setattr("services.sandbox.is_active", lambda cid: False)

    import state as state_mod

    state_mod.chat_bc.pop(1007, None)
    state_mod.timers.pop(1007, None)
    state_mod.pending_approval.clear()

    ok = await reengagement.maybe_reengage(bot, 1007, now=now)
    assert ok is False
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_reengage_skips_active_timer(monkeypatch):
    now = _seed_eligible(1008)
    bot = AsyncMock()
    monkeypatch.setattr(
        "services.auth_service.is_authorized", lambda *a, **k: True
    )
    monkeypatch.setattr("services.sandbox.is_active", lambda cid: False)

    import state as state_mod

    async def _never():
        await asyncio.sleep(3600)

    task = asyncio.create_task(_never())
    state_mod.timers[1008] = task
    state_mod.pending_approval.clear()
    try:
        ok = await reengagement.maybe_reengage(bot, 1008, now=now)
        assert ok is False
        bot.send_message.assert_not_awaited()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        state_mod.timers.pop(1008, None)


@pytest.mark.asyncio
async def test_maybe_reengage_skips_pending_approval(monkeypatch):
    now = _seed_eligible(1009)
    bot = AsyncMock()
    monkeypatch.setattr(
        "services.auth_service.is_authorized", lambda *a, **k: True
    )
    monkeypatch.setattr("services.sandbox.is_active", lambda cid: False)

    import state as state_mod

    state_mod.timers.pop(1009, None)
    state_mod.pending_approval[999001] = {
        "chat_id": 1009,
        "bc_id": "bc-eligible",
        "username": "vip_user",
        "gen": 1,
        "variants": [],
        "selected": 0,
    }
    try:
        ok = await reengagement.maybe_reengage(bot, 1009, now=now)
        assert ok is False
        bot.send_message.assert_not_awaited()
    finally:
        state_mod.pending_approval.pop(999001, None)


@pytest.mark.asyncio
async def test_maybe_reengage_uses_fixed_template_not_llm(monkeypatch):
    now = _seed_eligible(1010)
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock())

    monkeypatch.setattr(
        "services.auth_service.is_authorized", lambda *a, **k: True
    )
    monkeypatch.setattr("services.sandbox.is_active", lambda cid: False)
    monkeypatch.setattr(
        "services.chat_history.append_message", lambda *a, **k: None
    )

    import state as state_mod

    state_mod.timers.pop(1010, None)
    state_mod.pending_approval.clear()

    with patch("services.llm.raw_call", new_callable=AsyncMock) as llm:
        ok = await reengagement.maybe_reengage(bot, 1010, now=now)
        assert ok is True
        llm.assert_not_called()

    from config import REENGAGE_TEMPLATES

    text = bot.send_message.await_args_list[0].kwargs.get("text")
    if text is None and bot.send_message.await_args_list[0].args:
        # positional chat_id, text
        args = bot.send_message.await_args_list[0].args
        text = args[1] if len(args) > 1 else None
    assert text in REENGAGE_TEMPLATES


@pytest.mark.asyncio
async def test_maybe_reengage_independent_of_approval_mode(monkeypatch):
    """Direct send even when APPROVAL_MODE is True — no pending_approval created."""
    now = _seed_eligible(1011)
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock())

    monkeypatch.setattr(
        "services.auth_service.is_authorized", lambda *a, **k: True
    )
    monkeypatch.setattr("services.sandbox.is_active", lambda cid: False)
    monkeypatch.setattr(
        "services.chat_history.append_message", lambda *a, **k: None
    )
    monkeypatch.setattr("config.APPROVAL_MODE", True)

    import state as state_mod

    state_mod.timers.pop(1011, None)
    before = set(state_mod.pending_approval.keys())
    state_mod.pending_approval.clear()

    ok = await reengagement.maybe_reengage(bot, 1011, now=now)
    assert ok is True
    assert state_mod.pending_approval == {}
    bot.send_message.assert_awaited()
    # restore nothing critical; clear leftover
    for k in list(state_mod.pending_approval.keys()):
        if k not in before:
            state_mod.pending_approval.pop(k, None)


# ── scheduler ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_loop_honors_disabled_flag(monkeypatch):
    monkeypatch.setattr("config.REENGAGE_ENABLED", False)
    monkeypatch.setattr(reengagement, "REENGAGE_ENABLED", False, raising=False)

    calls: list = []

    async def fake_maybe(bot, chat_id, **kw):
        calls.append(chat_id)
        return False

    monkeypatch.setattr(reengagement, "maybe_reengage", fake_maybe)
    monkeypatch.setattr(
        "services.auth_service.get_authorized_ids", lambda: {1, 2, 3}
    )

    app = MagicMock()
    app.bot = AsyncMock()

    # Disabled: _scan_once must early-return without attempting re-engagement
    await reengagement._scan_once(app)
    assert calls == [], "disabled scanner must not call maybe_reengage"

    # Disabled: start_scheduler must not spawn a background task
    created: list = []

    def track_create(coro, *a, **k):
        created.append(coro)
        coro.close()  # don't actually run
        return MagicMock()

    monkeypatch.setattr(asyncio, "create_task", track_create)
    reengagement.start_scheduler(app)
    assert created == [], "disabled start_scheduler must not create_task"


@pytest.mark.asyncio
async def test_scan_once_seeds_and_attempts_eligible(monkeypatch):
    """Scanner seeds authorized ids via chat_bc and calls maybe_reengage."""
    monkeypatch.setattr("config.REENGAGE_ENABLED", True)
    if hasattr(reengagement, "REENGAGE_ENABLED"):
        monkeypatch.setattr(reengagement, "REENGAGE_ENABLED", True)

    monkeypatch.setattr(
        "services.auth_service.get_authorized_ids", lambda: {2001, 2002}
    )

    import state as state_mod

    state_mod.chat_bc[2001] = "bc-2001"
    # 2002 missing from chat_bc — still seeded

    seed_calls: list[tuple] = []
    reengage_calls: list[int] = []

    def track_seed(chat_id, **kw):
        seed_calls.append((chat_id, kw.get("bc_id", "")))

    async def track_reengage(bot, chat_id, **kw):
        reengage_calls.append(chat_id)
        return False

    monkeypatch.setattr(reengagement, "ensure_seeded", track_seed)
    monkeypatch.setattr(reengagement, "maybe_reengage", track_reengage)

    app = MagicMock()
    app.bot = AsyncMock()

    assert hasattr(reengagement, "_scan_once") or hasattr(
        reengagement, "_scheduler_loop"
    )

    if hasattr(reengagement, "_scan_once"):
        await reengagement._scan_once(app)
    else:
        # Drive one loop iteration by patching sleep to stop after first cycle
        async def stop_sleep(_):
            raise asyncio.CancelledError()

        monkeypatch.setattr(asyncio, "sleep", stop_sleep)
        with pytest.raises(asyncio.CancelledError):
            await reengagement._scheduler_loop(app)

    assert {c[0] for c in seed_calls} == {2001, 2002}
    bc_for_2001 = dict(seed_calls)[2001]
    assert bc_for_2001 == "bc-2001"
    assert set(reengage_calls) == {2001, 2002}

    state_mod.chat_bc.pop(2001, None)


def test_start_scheduler_creates_task_when_enabled(monkeypatch):
    monkeypatch.setattr("config.REENGAGE_ENABLED", True)
    if hasattr(reengagement, "REENGAGE_ENABLED"):
        monkeypatch.setattr(reengagement, "REENGAGE_ENABLED", True)

    created = []

    def fake_create_task(coro, *a, **k):
        created.append(coro)
        # Prevent "coroutine was never awaited"
        if hasattr(coro, "close"):
            coro.close()
        return MagicMock(name="reengage-task")

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)
    app = MagicMock()
    reengagement.start_scheduler(app)
    assert len(created) == 1


def test_start_scheduler_skips_when_disabled(monkeypatch):
    monkeypatch.setattr("config.REENGAGE_ENABLED", False)
    if hasattr(reengagement, "REENGAGE_ENABLED"):
        monkeypatch.setattr(reengagement, "REENGAGE_ENABLED", False)

    created = []

    def fake_create_task(coro, *a, **k):
        created.append(coro)
        if hasattr(coro, "close"):
            coro.close()
        return MagicMock()

    monkeypatch.setattr(asyncio, "create_task", fake_create_task)
    app = MagicMock()
    reengagement.start_scheduler(app)
    assert created == []


# ── WU3 wiring hooks (business touch + router scheduler) ─────────────


def _make_business_msg(
    *,
    chat_id: int = 9001,
    sender_id: int = 9001,
    bc_id: str = "bc-wire",
    text: str = "hola diana",
    username: str = "vip_wire",
    message_id: int = 42,
):
    msg = MagicMock()
    msg.business_connection_id = bc_id
    msg.chat.id = chat_id
    msg.chat.type = "private"
    msg.text = text
    msg.caption = None
    msg.photo = None
    msg.video = None
    msg.voice = None
    msg.audio = None
    msg.document = None
    msg.sticker = None
    msg.animation = None
    msg.video_note = None
    msg.from_user = MagicMock()
    msg.from_user.id = sender_id
    msg.from_user.username = username
    msg.from_user.first_name = username
    msg.message_id = message_id
    return msg


@pytest.mark.asyncio
async def test_business_authorized_vip_inbound_calls_touch_inbound(monkeypatch):
    """Authorized VIP inbound (not edit/owner/observe-only) must touch reengagement clock."""
    import auth_users
    import state as state_mod
    from handlers import business

    users_file = Path(reengagement.REENGAGE_STATE_FILE).parent / "auth_wire.json"
    auth_users.configure(
        users_file=str(users_file), max_users=10, seed_user_ids=[9001], admin_id=1,
    )
    state_mod.connections["bc-wire"] = 1  # owner is Diana admin, not VIP
    state_mod.history.clear()
    state_mod.timers.clear()
    state_mod.timer_schedule.clear()
    state_mod.reply_gen.clear()
    state_mod.chat_bc.clear()
    state_mod.pending_msg.clear()

    msg = _make_business_msg()
    context = MagicMock()
    context.bot = AsyncMock()

    with (
        patch("services.reengagement.touch_inbound") as mock_touch,
        patch("handlers.business.auto_reply", new_callable=AsyncMock),
        patch("handlers.business.needs_escalation", return_value=None),
        patch("handlers.business.append_message"),
        patch("handlers.business.ensure_loaded"),
        patch("handlers.business._save_runtime_state"),
    ):
        await business._handle_business_message(msg, context, edited=False)

    mock_touch.assert_called_once_with(9001, "bc-wire", "vip_wire")


@pytest.mark.asyncio
async def test_business_unauthorized_does_not_touch_inbound(monkeypatch):
    import auth_users
    import state as state_mod
    from handlers import business

    users_file = Path(reengagement.REENGAGE_STATE_FILE).parent / "auth_unauth.json"
    auth_users.configure(
        users_file=str(users_file), max_users=10, seed_user_ids=[], admin_id=1,
    )
    state_mod.connections["bc-wire"] = 1

    msg = _make_business_msg(chat_id=9002, sender_id=9002, username="stranger")
    context = MagicMock()
    context.bot = AsyncMock()

    with (
        patch("services.reengagement.touch_inbound") as mock_touch,
        patch("handlers.business.OBSERVE_UNAUTHORIZED", False),
    ):
        await business._handle_business_message(msg, context, edited=False)

    mock_touch.assert_not_called()


@pytest.mark.asyncio
async def test_business_edit_does_not_touch_inbound(monkeypatch):
    import auth_users
    import state as state_mod
    from handlers import business

    users_file = Path(reengagement.REENGAGE_STATE_FILE).parent / "auth_edit.json"
    auth_users.configure(
        users_file=str(users_file), max_users=10, seed_user_ids=[9003], admin_id=1,
    )
    state_mod.connections["bc-wire"] = 1

    msg = _make_business_msg(chat_id=9003, sender_id=9003, username="vip_edit")
    context = MagicMock()
    context.bot = AsyncMock()

    with patch("services.reengagement.touch_inbound") as mock_touch:
        await business._handle_business_message(msg, context, edited=True)

    mock_touch.assert_not_called()


@pytest.mark.asyncio
async def test_business_owner_inbound_does_not_touch_inbound(monkeypatch):
    """Diana (business owner) messages must not advance VIP silence clock."""
    import auth_users
    import state as state_mod
    from handlers import business

    owner_id = 555
    users_file = Path(reengagement.REENGAGE_STATE_FILE).parent / "auth_owner.json"
    auth_users.configure(
        users_file=str(users_file), max_users=10, seed_user_ids=[9004], admin_id=owner_id,
    )
    state_mod.connections["bc-wire"] = owner_id
    state_mod.history.clear()

    msg = _make_business_msg(
        chat_id=9004, sender_id=owner_id, username="diana_owner",
    )
    context = MagicMock()
    context.bot = AsyncMock()

    with (
        patch("services.reengagement.touch_inbound") as mock_touch,
        patch("handlers.business.append_message"),
        patch("handlers.business.ensure_loaded"),
    ):
        await business._handle_business_message(msg, context, edited=False)

    mock_touch.assert_not_called()


@pytest.mark.asyncio
async def test_business_observe_only_does_not_touch_inbound(monkeypatch):
    """Unauthorized observe-only path must not call touch_inbound."""
    import auth_users
    import state as state_mod
    from handlers import business

    users_file = Path(reengagement.REENGAGE_STATE_FILE).parent / "auth_obs.json"
    auth_users.configure(
        users_file=str(users_file), max_users=10, seed_user_ids=[], admin_id=1,
    )
    state_mod.connections["bc-wire"] = 1
    state_mod.history.clear()

    msg = _make_business_msg(chat_id=9005, sender_id=9005, username="observed")
    context = MagicMock()
    context.bot = AsyncMock()

    with (
        patch("services.reengagement.touch_inbound") as mock_touch,
        patch("handlers.business.OBSERVE_UNAUTHORIZED", True),
        patch("handlers.business.append_message"),
        patch("handlers.business.ensure_loaded"),
    ):
        await business._handle_business_message(msg, context, edited=False)

    mock_touch.assert_not_called()


@pytest.mark.asyncio
async def test_business_sandbox_active_does_not_touch_inbound(monkeypatch):
    """Sandbox chats must not pollute durable reengagement state via touch."""
    import auth_users
    import state as state_mod
    from handlers import business
    from services import sandbox

    users_file = Path(reengagement.REENGAGE_STATE_FILE).parent / "auth_sbx.json"
    auth_users.configure(
        users_file=str(users_file), max_users=10, seed_user_ids=[9006], admin_id=1,
    )
    state_mod.connections["bc-wire"] = 1
    state_mod.history.clear()
    state_mod.timers.clear()
    state_mod.timer_schedule.clear()
    state_mod.reply_gen.clear()

    # Force sandbox active without needing profiles file
    sandbox._active[9006] = "nuevo"

    msg = _make_business_msg(chat_id=9006, sender_id=9006, username="sbx_vip")
    context = MagicMock()
    context.bot = AsyncMock()

    try:
        with (
            patch("services.reengagement.touch_inbound") as mock_touch,
            patch("handlers.business.auto_reply", new_callable=AsyncMock),
            patch("handlers.business.needs_escalation", return_value=None),
            patch("handlers.business.append_message"),
            patch("handlers.business.ensure_loaded"),
            patch("handlers.business._save_runtime_state"),
        ):
            await business._handle_business_message(msg, context, edited=False)

        mock_touch.assert_not_called()
    finally:
        sandbox._active.pop(9006, None)


@pytest.mark.asyncio
async def test_post_init_starts_reengagement_scheduler(monkeypatch):
    """_post_init must start reengagement scheduler alongside backfill."""
    from handlers import router

    app = MagicMock()
    backfill_calls = []
    reengage_calls = []

    async def fake_recover(bot):
        return None

    monkeypatch.setattr(router, "recover_runtime_on_startup", fake_recover)
    monkeypatch.setattr(router, "_load_connections_state", lambda: None)

    import services.history_backfill as hb
    import services.reengagement as re

    monkeypatch.setattr(hb, "start_scheduler", lambda a: backfill_calls.append(a))
    monkeypatch.setattr(re, "start_scheduler", lambda a: reengage_calls.append(a))

    await router._post_init(app)

    assert backfill_calls == [app]
    assert reengage_calls == [app]

