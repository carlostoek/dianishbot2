"""Non-VIP promo-info autoreply: match, informed store, schedule/run orchestration.

Fixed Spanish templates, no LLM/approval/training/memory/reengagement.
"""
from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
from datetime import datetime

import auth_users
from config import (
    NON_VIP_PROMO_DELAY_MAX,
    NON_VIP_PROMO_DELAY_MIN,
    NON_VIP_PROMO_INTER_GAP_SEC,
    NON_VIP_PROMO_MSG1_FIRST,
    NON_VIP_PROMO_MSG1_REPEAT,
    NON_VIP_PROMO_MSG2,
    NON_VIP_PROMO_TRIGGER,
)
from services.delivery import deliver_sequential_messages
from state import timers

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


def _clear_promo_timer(chat_id: int) -> None:
    """Drop in-memory promo wait entry only — never touches timer_schedule."""
    timers.pop(chat_id, None)


async def schedule_promo_reply(
    bot,
    *,
    chat_id: int,
    username: str,
    bc_id: str,
    vip_id: int | None,
) -> bool:
    """Create promo wait task if no active timers[chat_id]. Never writes timer_schedule."""
    if chat_id in timers:
        log.info(
            "Promo wait ignored for chat %s — timer already active (no reschedule)",
            chat_id,
        )
        return False

    delay_sec = compute_promo_delay_sec()
    task = asyncio.create_task(
        run_promo_reply(
            bot,
            chat_id=chat_id,
            username=username,
            bc_id=bc_id,
            vip_id=vip_id,
            delay_sec=delay_sec,
        )
    )
    timers[chat_id] = task
    log.info(
        "Promo wait scheduled for %s (chat %s) in %.0fs — no timer_schedule",
        username,
        chat_id,
        delay_sec,
    )
    return True


async def run_promo_reply(
    bot,
    *,
    chat_id: int,
    username: str,
    bc_id: str,
    vip_id: int | None,
    delay_sec: float,
) -> None:
    """Sleep → auth re-check → sequential deliver → mark informed on full success.

    Clears timers[chat_id] on every exit path. Does not use LLM, approval,
    notify, training, memory, or reengagement. Does not write timer_schedule.
    """
    try:
        await asyncio.sleep(delay_sec)

        if auth_users.is_authorized(vip_id, chat_id):
            log.info(
                "Promo abort for chat %s — authorized as VIP at fire time",
                chat_id,
            )
            return

        msg1, msg2 = message_pair(chat_id)
        ok = await deliver_sequential_messages(
            bot,
            chat_id=chat_id,
            bc_id=bc_id,
            username=username,
            texts=[msg1, msg2],
            should_abort=lambda: auth_users.is_authorized(vip_id, chat_id),
            persist=False,
            inter_gap_sec=NON_VIP_PROMO_INTER_GAP_SEC,
        )
        if ok:
            mark_promo_informed(chat_id, username=username)
            log.info("Promo delivered and marked informed for chat %s", chat_id)
        else:
            log.info(
                "Promo delivery incomplete for chat %s — not marked informed",
                chat_id,
            )
    except asyncio.CancelledError:
        log.info("Promo wait cancelled for chat %s", chat_id)
        raise
    finally:
        _clear_promo_timer(chat_id)
