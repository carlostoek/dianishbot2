"""
conftest.py — Fixtures for python-telegram-bot v20+ (hardener reference).

See references/testing-strategy.md for full templates.
Use real telegram.* objects where possible + AsyncMock for Bot interactions.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import config

from telegram import (
    Bot,
    Update,
    User,
    Chat,
    Message,
    CallbackQuery,
)
from telegram.ext import ContextTypes


@pytest.fixture(scope="session", autouse=True)
def _ensure_system_prompt_file():
    """Crea un prompt mínimo si no existe (p. ej. en CI sin el archivo local)."""
    path = Path(config.SYSTEM_PROMPT_FILE)
    created = False
    if not path.is_file():
        path.write_text("Eres Diana. Responde en español.", encoding="utf-8")
        created = True
    config.load_system_prompt(force=True)
    yield
    config.reset_system_prompt_cache()
    if created:
        path.unlink(missing_ok=True)


@pytest.fixture
def bot():
    """AsyncMock Bot that records calls without hitting real Telegram."""
    bot = AsyncMock(spec=Bot)
    bot.send_message = AsyncMock()
    bot.send_chat_action = AsyncMock()
    bot.get_me = AsyncMock(return_value=User(id=123, first_name="Diana", is_bot=True))
    # business APIs are called via raw HTTP in delivery, not bot methods directly
    return bot


@pytest.fixture
def make_user():
    def _factory(
        user_id: int = 999999,
        username: str = "testvip",
        first_name: str = "Test",
        is_bot: bool = False,
    ):
        return User(
            id=user_id,
            username=username,
            first_name=first_name,
            is_bot=is_bot,
        )
    return _factory


@pytest.fixture
def make_chat():
    def _factory(chat_id: int = -100123, chat_type: str = "private"):
        return Chat(id=chat_id, type=chat_type)
    return _factory


@pytest.fixture
def make_message(bot, make_user, make_chat):
    def _factory(
        text: str = "hola diana",
        user=None,
        chat=None,
        message_id: int = 42,
        business_connection_id: str | None = "bc_test_123",
    ):
        user = user or make_user()
        chat = chat or make_chat()
        msg = Message(
            message_id=message_id,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            text=text,
            business_connection_id=business_connection_id,
        )
        return msg
    return _factory


@pytest.fixture
def make_business_message(make_message):
    """Convenience for business messages (the core of this bot)."""
    def _factory(**kwargs):
        return make_message(**kwargs)
    return _factory


@pytest.fixture
def make_update(make_message):
    def _factory(
        text: str = "hola diana",
        user=None,
        chat=None,
        update_id: int = 1,
        business_connection_id: str | None = "bc_test_123",
    ):
        msg = make_message(
            text=text, user=user, chat=chat, business_connection_id=business_connection_id
        )
        if business_connection_id:
            return Update(update_id=update_id, business_message=msg)
        return Update(update_id=update_id, message=msg)
    return _factory


@pytest.fixture
def make_callback_query(bot, make_user, make_message):
    def _factory(
        data: str = "a:approve:42",
        user=None,
        message=None,
    ):
        user = user or make_user()
        msg = message or make_message()
        cq = CallbackQuery(
            id="cb_123456",
            from_user=user,
            data=data,
            message=msg,
            chat_instance="test_inst",
        )
        return cq
    return _factory


@pytest.fixture
def make_callback_update(make_callback_query):
    def _factory(
        data: str = "a:approve:42",
        update_id: int = 99,
        user=None,
        message=None,
    ):
        cq = make_callback_query(data=data, user=user, message=message)
        upd = Update(update_id=update_id, callback_query=cq)
        return upd
    return _factory


@pytest.fixture
def make_mock_message(make_user, make_chat):
    """MagicMock message with async reply_text (PTB 22+ objects are frozen)."""
    def _factory(
        text: str = "hola",
        user=None,
        chat=None,
    ):
        user = user or make_user()
        chat = chat or make_chat()
        msg = MagicMock()
        msg.text = text
        msg.from_user = user
        msg.chat = chat
        msg.reply_text = AsyncMock()
        return msg
    return _factory


@pytest.fixture
def make_mock_update(make_mock_message):
    def _factory(text: str = "hola", user=None, chat=None):
        msg = make_mock_message(text=text, user=user, chat=chat)
        update = MagicMock()
        update.message = msg
        update.business_message = None
        update.callback_query = None
        return update
    return _factory


@pytest.fixture
def make_mock_callback_update(make_user):
    def _factory(data: str = "a:approve:42", user=None):
        user = user or make_user()
        cq = MagicMock()
        cq.data = data
        cq.from_user = user
        cq.answer = AsyncMock()
        cq.edit_message_text = AsyncMock()
        update = MagicMock()
        update.callback_query = cq
        return update
    return _factory


@pytest.fixture
def make_context(bot):
    """Context with bot + empty data bags. Extend as needed for timers/job_queue."""
    def _factory(user_data=None, chat_data=None, bot_data=None):
        ctx = AsyncMock(spec=ContextTypes.DEFAULT_TYPE)
        ctx.bot = bot
        ctx.user_data = user_data or {}
        ctx.chat_data = chat_data or {}
        ctx.bot_data = bot_data or {}
        ctx.job_queue = AsyncMock()
        ctx.job_queue.run_once = AsyncMock(return_value=MagicMock())
        ctx.job_queue.get_jobs_by_name = MagicMock(return_value=[])
        return ctx
    return _factory


# ──────────────────────────────────────────────────────────────────────────────
# DB fixtures (shared sqlite pattern used by training + memory)
# ──────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_db(tmp_path):
    """Fresh sqlite file-backed DB matching the schema used by the bot (sync conn)."""
    import sqlite3
    db_path = tmp_path / "test_diana.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            username TEXT,
            ts TEXT,
            context TEXT,
            bot_response TEXT,
            confidence INTEGER,
            topic TEXT,
            rating TEXT,
            correction TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_memory (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            key      TEXT NOT NULL,
            value    TEXT NOT NULL,
            source   TEXT DEFAULT 'auto',
            confidence INTEGER DEFAULT 80,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, key) ON CONFLICT REPLACE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS escalation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            username TEXT,
            ts TEXT NOT NULL,
            source TEXT NOT NULL,
            reason TEXT NOT NULL,
            matched TEXT,
            trigger_text TEXT NOT NULL,
            context TEXT,
            verdict TEXT,
            reviewed_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            username TEXT,
            ts TEXT NOT NULL,
            reason TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            detail TEXT,
            topic_guess TEXT,
            context TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            chat_id    INTEGER PRIMARY KEY,
            messages   TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS promo_informed (
            chat_id     INTEGER PRIMARY KEY,
            username    TEXT,
            informed_at TEXT NOT NULL
        )
    """)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def in_memory_training_db(test_db):
    """Provide the module-level db monkey-patch pattern used by services."""
    import services.training as training_mod
    old_db = training_mod.db
    training_mod.db = test_db
    yield test_db
    training_mod.db = old_db


@pytest.fixture
def chat_history_db(test_db):
    """Wire chat_history module db for unit tests."""
    import services.chat_history as ch_mod
    old = ch_mod.db
    ch_mod.db = test_db
    ch_mod.init_schema(test_db)
    yield test_db
    ch_mod.db = old


@pytest.fixture
def promo_info_db(test_db):
    """Wire promo_info module db for unit tests."""
    import services.promo_info as promo_mod
    old = promo_mod.db
    promo_mod.db = test_db
    promo_mod.init_schema(test_db)
    yield test_db
    promo_mod.db = old
