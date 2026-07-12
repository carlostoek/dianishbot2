"""Non-VIP promo-info autoreply: match, informed store, message pair, delay.

WU1 foundation only — schedule/run orchestration lands in WU3.
"""
from __future__ import annotations

import logging
import random
import sqlite3
from datetime import datetime

from config import (
    NON_VIP_PROMO_DELAY_MAX,
    NON_VIP_PROMO_DELAY_MIN,
    NON_VIP_PROMO_MSG1_FIRST,
    NON_VIP_PROMO_MSG1_REPEAT,
    NON_VIP_PROMO_MSG2,
    NON_VIP_PROMO_TRIGGER,
)

log = logging.getLogger("diana")

db: sqlite3.Connection | None = None


def _require_db() -> sqlite3.Connection:
    if db is None:
        raise RuntimeError("promo_info DB not initialized; wire in diana.main()")
    return db


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS promo_informed (
            chat_id     INTEGER PRIMARY KEY,
            username    TEXT,
            informed_at TEXT NOT NULL
        )
    """)


def is_trigger(text: str) -> bool:
    """True iff text.strip() == NON_VIP_PROMO_TRIGGER (no case fold)."""
    if text is None:
        return False
    return text.strip() == NON_VIP_PROMO_TRIGGER


def is_promo_informed(chat_id: int) -> bool:
    conn = _require_db()
    row = conn.execute(
        "SELECT 1 FROM promo_informed WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()
    return row is not None


def mark_promo_informed(chat_id: int, *, username: str = "") -> None:
    conn = _require_db()
    conn.execute(
        "INSERT OR REPLACE INTO promo_informed (chat_id, username, informed_at) "
        "VALUES (?, ?, ?)",
        (chat_id, username or "", datetime.now().isoformat()),
    )
    conn.commit()


def compute_promo_delay_sec() -> float:
    """Uniform random delay in seconds between configured min/max minutes."""
    return random.uniform(NON_VIP_PROMO_DELAY_MIN * 60, NON_VIP_PROMO_DELAY_MAX * 60)


def message_pair(chat_id: int) -> tuple[str, str]:
    """Return (msg1 first|repeat, msg2) based on durable informed flag."""
    msg1 = (
        NON_VIP_PROMO_MSG1_REPEAT
        if is_promo_informed(chat_id)
        else NON_VIP_PROMO_MSG1_FIRST
    )
    return msg1, NON_VIP_PROMO_MSG2
