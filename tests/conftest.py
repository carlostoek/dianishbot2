"""
conftest.py — Fixtures for python-telegram-bot v20+ (hardener reference).

See references/testing-strategy.md for full templates.
Use real telegram.* objects where possible + AsyncMock for Bot interactions.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from telegram import (
    Bot,
    Update,
    User,
    Chat,
    Message,
    CallbackQuery,
)
from telegram.ext import ContextTypes


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
            bot=bot,
        )
        # Attach business attrs manually (PTB objects are frozen-ish in tests)
        msg.business_connection_id = business_connection_id
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
        # The router looks at update.business_message
        update = Update(update_id=update_id, message=msg)
        # Simulate business routing attribute
        update.business_message = msg if business_connection_id else None
        update.edited_business_message = None
        return update
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
            bot=bot,
            chat_instance="test_inst",
        )
        return cq
    return _factory


@pytest.fixture
def make_callback_update(make_callback_query):
    def _factory(data: str = "a:approve:42", update_id: int = 99):
        cq = make_callback_query(data=data)
        upd = Update(update_id=update_id, callback_query=cq)
        return upd
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
