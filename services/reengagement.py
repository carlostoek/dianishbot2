"""VIP idle re-engagement: durable state, eligibility, and inbound touch.

WU1: state + pure eligibility + touch/seed only. Scanner/send land in a later slice.
No LLM, approval, or delivery coupling.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import REENGAGE_STATE_FILE as _DEFAULT_STATE_FILE

log = logging.getLogger("diana")

# Monkeypatchable path (tests set this to a tmp file).
REENGAGE_STATE_FILE = _DEFAULT_STATE_FILE

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
