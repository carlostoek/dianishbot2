"""Unit tests for VIP idle re-engagement state, eligibility, touch, and seed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    """WU1 module must stay isolated from LLM / approval delivery paths."""
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
