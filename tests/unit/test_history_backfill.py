"""Tests for services/history_backfill.py queue + worker hooks."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import auth_users
import state
from services import chat_history, history_backfill


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    queue_file = tmp_path / "backfill_queue.json"
    users_file = tmp_path / "authorized.json"
    monkeypatch.setattr(history_backfill, "BACKFILL_QUEUE_FILE", str(queue_file))
    auth_users.configure(users_file=str(users_file), max_users=10, seed_user_ids=[])
    state.history.clear()
    if queue_file.exists():
        queue_file.unlink()
    yield
    state.history.clear()


def test_backfill_queue_enqueue_dedupes():
    history_backfill.enqueue(111)
    history_backfill.enqueue(111)
    q = history_backfill._load_queue()
    assert q["pending"].count(111) == 1


def test_enqueue_missing_vips_only_unseeded(tmp_path):
    users_file = tmp_path / "authorized.json"
    users_file.write_text(
        json.dumps({
            "users": {
                "1": {"id": 1, "history_seeded_at": "2026-01-01T00:00:00+00:00"},
                "2": {"id": 2},
            }
        }),
        encoding="utf-8",
    )
    auth_users.configure(users_file=str(users_file), max_users=10, seed_user_ids=[])
    n = history_backfill.enqueue_missing_vips()
    assert n == 1
    q = history_backfill._load_queue()
    assert q["pending"] == [2]


def test_add_user_enqueues_backfill():
    auth_users.add_user(999, "vip", "V")
    q = history_backfill._load_queue()
    assert 999 in q["pending"]


def test_enqueue_skips_already_seeded(tmp_path):
    users_file = tmp_path / "authorized.json"
    users_file.write_text(
        json.dumps({
            "users": {
                "42": {
                    "id": 42,
                    "history_seeded_at": "2026-01-01T00:00:00+00:00",
                }
            }
        }),
        encoding="utf-8",
    )
    auth_users.configure(users_file=str(users_file), max_users=10, seed_user_ids=[])
    history_backfill.enqueue(42)
    q = history_backfill._load_queue()
    assert 42 not in q["pending"]


@pytest.mark.asyncio
async def test_worker_skips_without_api_credentials(monkeypatch):
    auth_users.add_user(42, "vip", "V")
    history_backfill.enqueue(42)
    app = MagicMock()
    app.bot = AsyncMock()

    with patch(
        "services.telethon_import.get_api_credentials",
        side_effect=RuntimeError("no creds"),
    ):
        await history_backfill._process_one(app)

    assert not auth_users.is_history_seeded(42)
    q = history_backfill._load_queue()
    assert q["pending"] == [42]


@pytest.mark.asyncio
async def test_worker_notifies_admin_on_failure(monkeypatch, chat_history_db, tmp_path):
    auth_users.add_user(42, "vip", "V")
    history_backfill.enqueue(42)
    app = MagicMock()
    app.bot = AsyncMock()

    with patch(
        "services.telethon_import.fetch_vip_history",
        side_effect=ValueError("could not find entity"),
    ):
        await history_backfill._process_one(app)

    app.bot.send_message.assert_awaited_once()
    call_kw = app.bot.send_message.await_args.kwargs
    assert "falló" in call_kw["text"].lower() or "permanente" in call_kw["text"].lower()
    assert auth_users.is_history_seeded(42)
    users = json.loads((tmp_path / "authorized.json").read_text(encoding="utf-8"))
    assert "history_seed_error" in users["users"]["42"]
    q = history_backfill._load_queue()
    assert 42 not in q["pending"]


@pytest.mark.asyncio
async def test_worker_marks_seeded_when_db_already_populated(monkeypatch, chat_history_db):
    """Skip-if-nonempty: live history preserved; worker still marks seeded."""
    auth_users.add_user(77, "vip", "V")
    chat_history.append_message(77, "user", "live antes del backfill")
    history_backfill.enqueue(77)
    app = MagicMock()
    app.bot = AsyncMock()

    fetched = [
        {"role": "user", "content": "viejo telethon"},
        {"role": "assistant", "content": "respuesta vieja"},
    ]
    with patch(
        "services.telethon_import.fetch_vip_history",
        return_value=(fetched, "VIP Live"),
    ):
        await history_backfill._process_one(app)

    assert auth_users.is_history_seeded(77)
    stored = chat_history.load_chat_history(77)
    assert len(stored) == 1
    assert stored[0]["content"] == "live antes del backfill"
    call_kw = app.bot.send_message.await_args.kwargs
    assert "skip" in call_kw["text"].lower() or "existía" in call_kw["text"].lower()


@pytest.mark.asyncio
async def test_worker_sets_history_seeded_at_on_success(monkeypatch, chat_history_db):
    auth_users.add_user(55, None, "Test")
    history_backfill.enqueue(55)
    app = MagicMock()
    app.bot = AsyncMock()

    msgs = [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "qué tal"}]
    with patch(
        "services.telethon_import.fetch_vip_history",
        return_value=(msgs, "Test VIP"),
    ):
        await history_backfill._process_one(app)

    assert auth_users.is_history_seeded(55)
    assert len(chat_history.load_chat_history(55)) == 2
    app.bot.send_message.assert_awaited_once()


def test_start_scheduler_disabled_without_telethon(monkeypatch, caplog):
    app = MagicMock()

    with patch(
        "services.telethon_import.get_api_credentials",
        side_effect=ImportError("no telethon"),
    ):
        with caplog.at_level("WARNING"):
            history_backfill.start_scheduler(app)
    assert any("deshabilitado" in r.message for r in caplog.records)


def test_dequeue_removes_user_from_pending():
    history_backfill.enqueue(77)
    history_backfill.enqueue(88)
    assert history_backfill.dequeue(77) is True
    q = history_backfill._load_queue()
    assert 77 not in q["pending"]
    assert 88 in q["pending"]
    assert history_backfill.dequeue(77) is False


def test_corrupt_queue_rebuilds_pending_from_unseeded_vips(tmp_path, monkeypatch):
    queue_file = tmp_path / "backfill_queue.json"
    users_file = tmp_path / "authorized.json"
    monkeypatch.setattr(history_backfill, "BACKFILL_QUEUE_FILE", str(queue_file))
    auth_users.configure(users_file=str(users_file), max_users=10, seed_user_ids=[])
    auth_users.add_user(88, None, "Pending")
    queue_file.write_text("{ corrupt", encoding="utf-8")

    q = history_backfill._load_queue()
    assert 88 in q["pending"]
    backups = list(tmp_path.glob("backfill_queue.corrupt.*.json"))
    assert len(backups) == 1


@pytest.mark.asyncio
async def test_worker_skips_unauthorized_user():
    history_backfill.enqueue(404)
    app = MagicMock()
    app.bot = AsyncMock()

    with patch("services.telethon_import.fetch_vip_history") as mock_fetch:
        await history_backfill._process_one(app)
        mock_fetch.assert_not_called()

    q = history_backfill._load_queue()
    assert 404 not in q["pending"]


@pytest.mark.asyncio
async def test_worker_no_reenqueue_when_already_seeded(chat_history_db):
    """Notify failure after successful seed must not re-enqueue."""
    auth_users.add_user(66, None, "V")
    history_backfill.enqueue(66)
    app = MagicMock()
    app.bot = AsyncMock()
    app.bot.send_message = AsyncMock(side_effect=RuntimeError("notify failed"))

    msgs = [{"role": "user", "content": "hola"}]
    with patch(
        "services.telethon_import.fetch_vip_history",
        return_value=(msgs, "VIP"),
    ):
        await history_backfill._process_one(app)

    assert auth_users.is_history_seeded(66)
    q = history_backfill._load_queue()
    assert 66 not in q["pending"]
    assert len(chat_history.load_chat_history(66)) == 1


def test_remove_user_dequeues_and_clears_history(tmp_path, chat_history_db, monkeypatch):
    queue_file = tmp_path / "backfill_queue.json"
    monkeypatch.setattr(history_backfill, "BACKFILL_QUEUE_FILE", str(queue_file))
    auth_users.add_user(42, "u", "U")
    history_backfill.enqueue(42)
    chat_history.append_message(42, "user", "hi")

    auth_users.remove_user(42)

    assert 42 not in history_backfill._load_queue()["pending"]
    assert chat_history.load_chat_history(42) == []
    assert 42 not in state.history


@pytest.mark.asyncio
async def test_worker_does_not_mark_seeded_when_ram_skip_blocks_db(chat_history_db):
    auth_users.add_user(91, "vip", "V")
    state.history[91] = [{"role": "user", "content": "sandbox-live"}]
    history_backfill.enqueue(91)
    app = MagicMock()
    app.bot = AsyncMock()

    fetched = [
        {"role": "user", "content": "telethon msg"},
        {"role": "assistant", "content": "respuesta"},
    ]
    with patch(
        "services.telethon_import.fetch_vip_history",
        return_value=(fetched, "VIP"),
    ):
        await history_backfill._process_one(app)

    assert not auth_users.is_history_seeded(91)
    assert chat_history.load_chat_history(91) == []
    assert state.history[91][0]["content"] == "sandbox-live"
    q = history_backfill._load_queue()
    assert 91 in q["pending"]


def test_add_user_already_reenqueues_if_unseeded(tmp_path, monkeypatch):
    queue_file = tmp_path / "backfill_queue.json"
    users_file = tmp_path / "authorized.json"
    monkeypatch.setattr(history_backfill, "BACKFILL_QUEUE_FILE", str(queue_file))
    auth_users.configure(users_file=str(users_file), max_users=10, seed_user_ids=[])
    auth_users.add_user(123, "vip", "V")
    history_backfill.dequeue(123)

    assert auth_users.add_user(123, "vip", "V") == "already"
    q = history_backfill._load_queue()
    assert 123 in q["pending"]


@pytest.mark.parametrize(
    "exc_name",
    [
        "AuthKeyUnregisteredError",
        "SessionRevokedError",
        "SessionExpiredError",
        "AuthKeyDuplicatedError",
    ],
)
def test_is_permanent_error_session_errors(exc_name):
    exc = type(exc_name, (Exception,), {})()
    assert history_backfill.is_permanent_error(exc) is True


def test_get_few_shots_unaffected_by_seed(chat_history_db, in_memory_training_db):
    import services.training as training_mod

    training_mod.db.execute(
        """
        INSERT INTO examples
        (chat_id, username, ts, context, bot_response, confidence, topic, rating, status)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            1,
            "vip",
            "2026-01-01T00:00:00",
            json.dumps([{"role": "user", "content": "hola"}]),
            "respuesta",
            90,
            "general",
            "good",
            "reviewed",
        ),
    )
    training_mod.db.commit()

    before = training_mod.get_few_shots("general")
    chat_history.seed_chat_history(
        99,
        [{"role": "user", "content": "seeded"}],
    )
    after = training_mod.get_few_shots("general")
    assert before == after