"""Durable per-chat message history (RAM cache: state.history)."""
import json
import logging
import sqlite3
from datetime import datetime

from config import MAX_STORED_HISTORY
import state

log = logging.getLogger("diana")

db: sqlite3.Connection | None = None


def _require_db() -> sqlite3.Connection:
    if db is None:
        raise RuntimeError("chat_history DB not initialized; wire in diana.main()")
    return db


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            chat_id    INTEGER PRIMARY KEY,
            messages   TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)


def _trim(messages: list[dict]) -> list[dict]:
    if len(messages) <= MAX_STORED_HISTORY:
        return messages
    return messages[-MAX_STORED_HISTORY:]


def _serialize(messages: list[dict]) -> str:
    return json.dumps(messages, ensure_ascii=False)


def _deserialize(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        return [
            {"role": m["role"], "content": str(m["content"])}
            for m in data
            if isinstance(m, dict) and m.get("role") in ("user", "assistant") and "content" in m
        ]
    except (json.JSONDecodeError, TypeError, KeyError):
        log.warning("chat_history corrupt JSON — treating as empty")
        return []


def load_chat_history(chat_id: int) -> list[dict]:
    if db is None:
        return []
    row = _require_db().execute(
        "SELECT messages FROM chat_history WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    if not row:
        return []
    return _trim(_deserialize(row[0]))


def ensure_loaded(chat_id: int) -> list[dict]:
    """Load DB → RAM, preferring longer history. Returns current RAM list."""
    if db is None:
        return state.history.get(chat_id, [])
    if chat_id not in state.history or not state.history[chat_id]:
        stored = load_chat_history(chat_id)
        if stored:
            state.history[chat_id] = list(stored)
    merge_runtime_with_db(chat_id)
    return state.history.get(chat_id, [])


def append_message(
    chat_id: int,
    role: str,
    content: str,
    *,
    persist: bool = True,
) -> None:
    """Append to RAM; optionally persist to SQLite (with sandbox gate)."""
    from services import sandbox

    if role not in ("user", "assistant"):
        raise ValueError(f"invalid role: {role}")
    ensure_loaded(chat_id)
    msgs = state.history.setdefault(chat_id, [])
    msgs.append({"role": role, "content": content})
    msgs[:] = _trim(msgs)

    if not persist or not sandbox.should_persist(chat_id):
        return

    conn = _require_db()
    conn.execute(
        "INSERT OR REPLACE INTO chat_history (chat_id, messages, updated_at) "
        "VALUES (?, ?, ?)",
        (chat_id, _serialize(msgs), datetime.now().isoformat()),
    )
    conn.commit()


def clear_chat_history(chat_id: int) -> None:
    state.history.pop(chat_id, None)
    if db is None:
        return
    conn = _require_db()
    conn.execute("DELETE FROM chat_history WHERE chat_id = ?", (chat_id,))
    conn.commit()


def merge_runtime_with_db(chat_id: int) -> None:
    """After runtime load: prefer longer history (runtime mid-flight vs DB)."""
    if db is None:
        return
    runtime_msgs = state.history.get(chat_id, [])
    db_msgs = load_chat_history(chat_id)
    if not db_msgs:
        return
    if len(runtime_msgs) >= len(db_msgs):
        return
    state.history[chat_id] = list(db_msgs)


def merge_all_loaded_chats() -> int:
    """Merge DB into RAM for every chat_id present in state.history after runtime load."""
    merged = 0
    for chat_id in list(state.history.keys()):
        before = len(state.history.get(chat_id, []))
        merge_runtime_with_db(chat_id)
        after = len(state.history.get(chat_id, []))
        if after > before:
            merged += 1
    return merged