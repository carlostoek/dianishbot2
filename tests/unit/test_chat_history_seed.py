"""Tests for seed_chat_history() skip-if-nonempty policy."""

from pathlib import Path

import state
from config import MAX_STORED_HISTORY
from services import chat_history, sandbox

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _clear_ram():
    state.history.clear()


def test_seed_chat_history_writes_db_and_ram(chat_history_db):
    _clear_ram()
    msgs = [{"role": "user", "content": "hola"}]
    n = chat_history.seed_chat_history(42, msgs)
    assert n == 1
    assert state.history[42][0]["content"] == "hola"
    stored = chat_history.load_chat_history(42)
    assert stored[0]["content"] == "hola"


def test_seed_chat_history_trims_to_max_stored(chat_history_db):
    _clear_ram()
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(60)]
    n = chat_history.seed_chat_history(1, msgs)
    assert n == MAX_STORED_HISTORY
    assert len(chat_history.load_chat_history(1)) == MAX_STORED_HISTORY
    assert state.history[1][0]["content"] == "m10"


def test_seed_chat_history_skips_when_db_nonempty(chat_history_db):
    _clear_ram()
    chat_history.append_message(1, "user", "live")
    n = chat_history.seed_chat_history(1, [{"role": "user", "content": "seed"}])
    assert n == 0
    assert chat_history.load_chat_history(1)[-1]["content"] == "live"


def test_seed_chat_history_skips_when_ram_nonempty(chat_history_db):
    _clear_ram()
    state.history[5] = [{"role": "user", "content": "sandbox-live"}]
    n = chat_history.seed_chat_history(5, [{"role": "user", "content": "seed"}])
    assert n == 0
    assert state.history[5][0]["content"] == "sandbox-live"
    assert chat_history.load_chat_history(5) == []


def test_seed_chat_history_skips_db_in_sandbox(chat_history_db, tmp_path):
    _clear_ram()
    profiles_file = tmp_path / "sandbox_profiles.json"
    src = _REPO_ROOT / "diana_sandbox_profiles.json"
    profiles_file.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    sandbox.configure(profiles_file=str(profiles_file))
    sandbox._active[7] = "nuevo"

    n = chat_history.seed_chat_history(
        7,
        [{"role": "user", "content": "seeded"}],
    )
    assert n == 0
    assert chat_history.load_chat_history(7) == []
    assert 7 not in state.history
    sandbox._active.clear()


def test_seed_chat_history_overwrite_mode(chat_history_db):
    _clear_ram()
    chat_history.append_message(1, "user", "live")
    n = chat_history.seed_chat_history(
        1,
        [{"role": "assistant", "content": "replaced"}],
        overwrite=True,
    )
    assert n == 1
    assert chat_history.load_chat_history(1)[0]["content"] == "replaced"
    assert state.history[1][0]["content"] == "replaced"