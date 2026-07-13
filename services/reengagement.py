"""VIP idle re-engagement: durable state, eligibility, direct send, scanner.

Scanner/send path is isolated from LLM, approval gate, and the VIP delivery helper.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import (
    DIANA_ADMIN_CHAT_ID,
    REENGAGE_ENABLED as _DEFAULT_ENABLED,
    REENGAGE_IDLE_DAYS as _DEFAULT_IDLE_DAYS,
    REENGAGE_SCAN_INTERVAL_SEC as _DEFAULT_SCAN_INTERVAL,
    REENGAGE_STATE_FILE as _DEFAULT_STATE_FILE,
    REENGAGE_TEMPLATES as _DEFAULT_TEMPLATES,
)

log = logging.getLogger("diana")

# Monkeypatchable (tests set path / flags).
REENGAGE_STATE_FILE = _DEFAULT_STATE_FILE
REENGAGE_ENABLED = _DEFAULT_ENABLED
REENGAGE_IDLE_DAYS = _DEFAULT_IDLE_DAYS
REENGAGE_SCAN_INTERVAL_SEC = _DEFAULT_SCAN_INTERVAL
REENGAGE_TEMPLATES = list(_DEFAULT_TEMPLATES)

_state_lock = threading.Lock()

def _reset_for_tests() -> None:
    """No in-memory cache today; hook kept for test fixture symmetry."""
    return None


def _state_path() -> Path:
    return Path(REENGAGE_STATE_FILE)


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "last_scan_at": None,
        "users": {},
    }


def _utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return _utc_now(dt).isoformat()


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc_now(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _utc_now(parsed)


def _save_state_unlocked(data: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Sibling .tmp then os.replace — same atomic pattern as history_backfill.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_state_unlocked() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return _default_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise json.JSONDecodeError("root not object", "", 0)
        data.setdefault("version", 1)
        data.setdefault("last_scan_at", None)
        users = data.get("users")
        if not isinstance(users, dict):
            data["users"] = {}
        return data
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        log.error("Re-engagement state corrupt or unreadable: %s", e)
        if path.exists():
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = path.with_name(f"{path.stem}.corrupt.{ts}{path.suffix}")
            try:
                os.replace(path, backup)
                log.error("Re-engagement state backed up to %s", backup)
            except OSError as oe:
                log.error("Could not back up corrupt re-engagement state: %s", oe)
        rebuilt = _default_state()
        _save_state_unlocked(rebuilt)
        return rebuilt


def _load_state() -> dict[str, Any]:
    with _state_lock:
        return _load_state_unlocked()


def _save_state(data: dict[str, Any]) -> None:
    with _state_lock:
        _save_state_unlocked(data)


def _user_key(chat_id: int) -> str:
    return str(int(chat_id))


def _empty_entry() -> dict[str, Any]:
    return {
        "last_vip_inbound_at": None,
        "last_reengage_at": None,
        "reengage_sent_for_inbound_at": None,
        "bc_id": "",
        "username": "",
    }


def get_entry(chat_id: int) -> dict[str, Any] | None:
    """Return a copy of the persisted entry for chat_id, or None if missing."""
    data = _load_state()
    entry = data["users"].get(_user_key(chat_id))
    if entry is None:
        return None
    return dict(entry)


def is_eligible(
    entry: dict[str, Any] | None,
    *,
    now: datetime,
    idle_days: float,
) -> bool:
    """Pure eligibility: idle long enough and no reengage for this inbound cycle.

    Cycle rule: eligible when
      now - last_vip_inbound_at >= idle_days
      AND reengage_sent_for_inbound_at != last_vip_inbound_at
    """
    if not entry:
        return False
    last_inbound = _parse_ts(entry.get("last_vip_inbound_at"))
    if last_inbound is None:
        return False
    now_utc = _utc_now(now)
    if now_utc - last_inbound < timedelta(days=float(idle_days)):
        return False
    sent_for = entry.get("reengage_sent_for_inbound_at")
    last_stamp = entry.get("last_vip_inbound_at")
    if sent_for is not None and sent_for == last_stamp:
        return False
    return True


def ensure_seeded(
    chat_id: int,
    *,
    bc_id: str = "",
    username: str = "",
    now: datetime | None = None,
) -> None:
    """Cold-start seed: set last_vip_inbound_at=now only if user has no stamp."""
    stamp = _iso(_utc_now(now))
    with _state_lock:
        data = _load_state_unlocked()
        key = _user_key(chat_id)
        users: dict[str, Any] = data.setdefault("users", {})
        entry = users.get(key)
        if entry is None:
            entry = _empty_entry()
            users[key] = entry
        if entry.get("last_vip_inbound_at"):
            # Already tracked — do not move the silence clock on re-seed.
            if bc_id and not entry.get("bc_id"):
                entry["bc_id"] = bc_id
            if username and not entry.get("username"):
                entry["username"] = username
            _save_state_unlocked(data)
            return
        entry["last_vip_inbound_at"] = stamp
        if bc_id:
            entry["bc_id"] = bc_id
        if username:
            entry["username"] = username
        entry.setdefault("last_reengage_at", None)
        entry.setdefault("reengage_sent_for_inbound_at", None)
        _save_state_unlocked(data)


def touch_inbound(
    chat_id: int,
    bc_id: str,
    username: str,
    *,
    now: datetime | None = None,
) -> None:
    """Record authorized VIP inbound: advances last_vip_inbound_at (new silence cycle)."""
    stamp = _iso(_utc_now(now))
    with _state_lock:
        data = _load_state_unlocked()
        key = _user_key(chat_id)
        users: dict[str, Any] = data.setdefault("users", {})
        entry = users.get(key)
        if entry is None:
            entry = _empty_entry()
            users[key] = entry
        entry["last_vip_inbound_at"] = stamp
        entry["bc_id"] = bc_id or entry.get("bc_id") or ""
        entry["username"] = username or entry.get("username") or ""
        entry.setdefault("last_reengage_at", None)
        entry.setdefault("reengage_sent_for_inbound_at", None)
        _save_state_unlocked(data)


def _has_active_timer(chat_id: int) -> bool:
    import state as state_mod

    task = state_mod.timers.get(chat_id)
    if task is None:
        return False
    return not task.done()


def _has_pending_approval(chat_id: int) -> bool:
    import state as state_mod

    return any(
        pending.get("chat_id") == chat_id
        for pending in state_mod.pending_approval.values()
    )


def _pick_template() -> str:
    templates = REENGAGE_TEMPLATES or _DEFAULT_TEMPLATES
    if not templates:
        return "Oye, ¿todo bien? Hace rato que no sé de ti 😊"
    return random.choice(list(templates))


async def _notify_diana(
    bot,
    *,
    chat_id: int,
    username: str,
    template: str,
    idle_days: float,
) -> None:
    """Info-only DM to Diana after a successful re-engagement send."""
    if not DIANA_ADMIN_CHAT_ID:
        return
    who = f"@{username}" if username else str(chat_id)
    excerpt = template if len(template) <= 80 else template[:77] + "…"
    text = (
        f"🔁 Re-engagement enviado a {who} (chat {chat_id})\n"
        f"Idle ≥ {idle_days:g} día(s)\n"
        f"Msg: {excerpt}"
    )
    try:
        await bot.send_message(chat_id=DIANA_ADMIN_CHAT_ID, text=text)
    except Exception as e:
        log.error("Re-engagement notify Diana failed for %s: %s", chat_id, e)


async def maybe_reengage(
    bot,
    chat_id: int,
    *,
    now: datetime | None = None,
) -> bool:
    """Send one fixed-template re-engagement if eligible. Returns True on success.

    Direct VIP send (bare bot.send_message + business_connection_id). Bypasses
    approval and the human-like VIP delivery path. Marks cycle only when inbound
    stamp is unchanged after send.
    """
    if not REENGAGE_ENABLED:
        return False

    from services.auth_service import is_authorized
    from services import sandbox
    from services.chat_history import append_message
    import state as state_mod

    if not is_authorized(chat_id, chat_id=chat_id):
        return False
    from services import data_pause

    if sandbox.is_active(chat_id) or data_pause.is_paused(chat_id):
        return False
    if _has_active_timer(chat_id):
        return False
    if _has_pending_approval(chat_id):
        return False

    now_utc = _utc_now(now)
    idle_days = float(REENGAGE_IDLE_DAYS)

    # Snapshot eligibility + stamp under lock.
    with _state_lock:
        data = _load_state_unlocked()
        entry = data["users"].get(_user_key(chat_id))
        if not entry:
            return False
        bc_id = (entry.get("bc_id") or state_mod.chat_bc.get(chat_id) or "").strip()
        if not bc_id:
            log.debug("Re-engagement skip chat %s: missing bc_id", chat_id)
            return False
        if not is_eligible(entry, now=now_utc, idle_days=idle_days):
            return False
        stamp = entry.get("last_vip_inbound_at")
        username = entry.get("username") or ""
        template = _pick_template()

    # Pre-send stamp recheck (abort send if VIP inbound advanced the cycle).
    with _state_lock:
        data = _load_state_unlocked()
        entry = data["users"].get(_user_key(chat_id))
        if not entry or entry.get("last_vip_inbound_at") != stamp:
            log.info(
                "Re-engagement aborted pre-send (stamp mismatch) chat %s", chat_id
            )
            return False

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=template,
            business_connection_id=bc_id,
        )
    except Exception as e:
        log.error("Re-engagement send failed chat %s: %s", chat_id, e)
        return False

    # Mark cycle only if stamp still equals the snapshot.
    marked = False
    with _state_lock:
        data = _load_state_unlocked()
        entry = data["users"].get(_user_key(chat_id))
        if entry and entry.get("last_vip_inbound_at") == stamp:
            entry["reengage_sent_for_inbound_at"] = stamp
            entry["last_reengage_at"] = _iso(now_utc)
            if bc_id and not entry.get("bc_id"):
                entry["bc_id"] = bc_id
            _save_state_unlocked(data)
            marked = True
        else:
            log.info(
                "Re-engagement post-send mark aborted (stamp mismatch) chat %s",
                chat_id,
            )

    if not marked:
        return False

    from state import chat_write_lock

    async with chat_write_lock(chat_id):
        append_message(chat_id, "assistant", template)

    await _notify_diana(
        bot,
        chat_id=chat_id,
        username=username,
        template=template,
        idle_days=idle_days,
    )
    log.info("Re-engagement sent to chat %s (%s)", chat_id, username or "—")
    return True


async def _scan_once(app) -> None:
    """Seed authorized VIPs and attempt re-engagement once per chat."""
    if not REENGAGE_ENABLED:
        return

    from services.auth_service import get_authorized_ids
    import state as state_mod

    bot = app.bot
    now = _utc_now()
    for chat_id in sorted(get_authorized_ids()):
        bc_id = state_mod.chat_bc.get(chat_id, "") or ""
        try:
            ensure_seeded(chat_id, bc_id=bc_id, now=now)
            await maybe_reengage(bot, chat_id, now=now)
        except Exception as e:
            log.error("Re-engagement scan error chat %s: %s", chat_id, e)

    with _state_lock:
        data = _load_state_unlocked()
        data["last_scan_at"] = _iso(now)
        _save_state_unlocked(data)


async def _scheduler_loop(app) -> None:
    """Periodic scanner; interval from REENGAGE_SCAN_INTERVAL_SEC."""
    while True:
        try:
            if REENGAGE_ENABLED:
                await _scan_once(app)
        except Exception as e:
            log.error("Unexpected re-engagement scheduler error: %s", e)
        await asyncio.sleep(float(REENGAGE_SCAN_INTERVAL_SEC))


def start_scheduler(app) -> None:
    """Start background re-engagement scanner (no-op when disabled)."""
    if not REENGAGE_ENABLED:
        log.info("Re-engagement scheduler disabled (REENGAGE_ENABLED=False)")
        return
    asyncio.create_task(_scheduler_loop(app))
    log.info(
        "Re-engagement scheduler started (interval=%ss, idle_days=%s, state=%s)",
        REENGAGE_SCAN_INTERVAL_SEC,
        REENGAGE_IDLE_DAYS,
        REENGAGE_STATE_FILE,
    )
