"""Unit tests for non-VIP promo-info match, informed store, and message pair (WU1)."""

from __future__ import annotations

import pytest

from config import (
    NON_VIP_PROMO_DELAY_MAX,
    NON_VIP_PROMO_DELAY_MIN,
    NON_VIP_PROMO_MSG1_FIRST,
    NON_VIP_PROMO_MSG1_REPEAT,
    NON_VIP_PROMO_MSG2,
    NON_VIP_PROMO_TRIGGER,
)
from services import promo_info


@pytest.fixture
def promo_info_db(test_db):
    """Wire promo_info module db + schema for unit tests."""
    old = promo_info.db
    promo_info.db = test_db
    promo_info.init_schema(test_db)
    yield test_db
    promo_info.db = old


# ── is_trigger (pure, strip-only exact match) ───────────────────────


def test_is_trigger_exact_match():
    assert promo_info.is_trigger(NON_VIP_PROMO_TRIGGER) is True


def test_is_trigger_strips_surrounding_whitespace():
    assert promo_info.is_trigger(f"  {NON_VIP_PROMO_TRIGGER}  \n") is True


@pytest.mark.parametrize(
    "text",
    [
        "quiero más información 🔥",  # case fold would match — must NOT
        "Quiero más información",  # missing emoji
        "Quiero más información 🔥 extra",  # surrounding words
        "xQuiero más información 🔥",
        "Quiero mas informacion 🔥",  # accent / spelling
        "",
        "   ",
        "info",
    ],
)
def test_is_trigger_near_miss_does_not_match(text):
    assert promo_info.is_trigger(text) is False


# ── schema + informed CRUD ──────────────────────────────────────────


def test_init_schema_creates_promo_informed_table(promo_info_db):
    row = promo_info_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='promo_informed'"
    ).fetchone()
    assert row is not None


def test_is_promo_informed_false_when_missing(promo_info_db):
    assert promo_info.is_promo_informed(42) is False


def test_mark_promo_informed_then_is_true(promo_info_db):
    promo_info.mark_promo_informed(42, username="buyer")
    assert promo_info.is_promo_informed(42) is True
    assert promo_info.is_promo_informed(99) is False


def test_mark_promo_informed_persists_username_and_timestamp(promo_info_db):
    promo_info.mark_promo_informed(7, username="alice")
    row = promo_info_db.execute(
        "SELECT username, informed_at FROM promo_informed WHERE chat_id = ?",
        (7,),
    ).fetchone()
    assert row is not None
    assert row[0] == "alice"
    assert isinstance(row[1], str) and len(row[1]) > 0


def test_mark_promo_informed_idempotent(promo_info_db):
    promo_info.mark_promo_informed(7, username="first")
    promo_info.mark_promo_informed(7, username="second")
    assert promo_info.is_promo_informed(7) is True
    row = promo_info_db.execute(
        "SELECT username FROM promo_informed WHERE chat_id = ?", (7,)
    ).fetchone()
    assert row[0] == "second"


# ── message_pair (first vs repeat) ──────────────────────────────────


def test_message_pair_first_time(promo_info_db):
    msg1, msg2 = promo_info.message_pair(100)
    assert msg1 == NON_VIP_PROMO_MSG1_FIRST
    assert msg2 == NON_VIP_PROMO_MSG2
    assert msg1 != NON_VIP_PROMO_MSG1_REPEAT


def test_message_pair_repeat_after_informed(promo_info_db):
    promo_info.mark_promo_informed(100, username="buyer")
    msg1, msg2 = promo_info.message_pair(100)
    assert msg1 == NON_VIP_PROMO_MSG1_REPEAT
    assert msg2 == NON_VIP_PROMO_MSG2
    assert msg1 != NON_VIP_PROMO_MSG1_FIRST


def test_message_pair_msg2_character_for_character(promo_info_db):
    _, msg2 = promo_info.message_pair(1)
    assert msg2 == NON_VIP_PROMO_MSG2
    assert "coqu3to" in msg2
    assert "EL DIVÁN VIP" in msg2


# ── compute_promo_delay_sec ─────────────────────────────────────────


def test_compute_promo_delay_sec_within_configured_bounds():
    lo = NON_VIP_PROMO_DELAY_MIN * 60
    hi = NON_VIP_PROMO_DELAY_MAX * 60
    samples = [promo_info.compute_promo_delay_sec() for _ in range(40)]
    assert all(lo <= s <= hi for s in samples)
    # Not a single hardcoded constant (triangulate variance under uniform)
    assert min(samples) < max(samples) or lo == hi


def test_compute_promo_delay_sec_uses_uniform(monkeypatch):
    captured = {}

    def fake_uniform(a, b):
        captured["a"] = a
        captured["b"] = b
        return 200.0

    monkeypatch.setattr(promo_info.random, "uniform", fake_uniform)
    assert promo_info.compute_promo_delay_sec() == 200.0
    assert captured["a"] == NON_VIP_PROMO_DELAY_MIN * 60
    assert captured["b"] == NON_VIP_PROMO_DELAY_MAX * 60


# ── config contracts (exact product copy) ───────────────────────────


def test_config_trigger_and_msg1_variants_exact():
    assert NON_VIP_PROMO_TRIGGER == "Quiero más información 🔥"
    assert NON_VIP_PROMO_MSG1_FIRST == "Holaaa 💕\nTe mando mis promos 🔥"
    assert NON_VIP_PROMO_MSG1_REPEAT == (
        "Holis 😁 \n"
        "Claro, te mando de nuevo mis promos. Los nombres son los mismos "
        "pero es contenido nuevo y diferente."
    )


def test_config_delay_bounds_and_flag_defaults():
    from config import (
        NON_VIP_PROMO_AUTOREPLY_ENABLED,
        NON_VIP_PROMO_DELAY_MAX,
        NON_VIP_PROMO_DELAY_MIN,
        NON_VIP_PROMO_INTER_GAP_SEC,
    )

    assert NON_VIP_PROMO_AUTOREPLY_ENABLED is True
    assert NON_VIP_PROMO_DELAY_MIN == 2
    assert NON_VIP_PROMO_DELAY_MAX == 5
    assert NON_VIP_PROMO_INTER_GAP_SEC == (1.5, 3.0)


def test_training_init_db_wires_promo_schema(tmp_path, monkeypatch):
    """training.init_db must create promo_informed via promo_info.init_schema."""
    import services.training as training

    db_path = tmp_path / "wire.db"
    monkeypatch.setattr(training, "DB_FILE", str(db_path))
    # training imports DB_FILE from config at call time via from config import in module
    monkeypatch.setattr("services.training.DB_FILE", str(db_path))
    monkeypatch.setattr("config.DB_FILE", str(db_path))

    conn = training.init_db()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='promo_informed'"
        ).fetchone()
        assert row is not None
    finally:
        conn.close()
