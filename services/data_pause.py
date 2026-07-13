"""Pausa total por VIP — el bot no registra ni interactúa hasta reactivar."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from services import auth_service

log = logging.getLogger("diana.data_pause")

_INDEFINITE = "indefinite"
_DURATION_OPTIONS: tuple[tuple[str, int | None], ...] = (
    ("1 día", 1),
    ("3 días", 3),
    ("1 semana", 7),
    ("1 mes", 30),
    ("Indefinido", None),
)


def _parse_until(raw: str) -> datetime | None:
    if raw == _INDEFINITE:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _clear_pause_fields(entry: dict) -> None:
    entry.pop("data_paused_at", None)
    entry.pop("data_paused_until", None)


def _expire_if_needed(user_id: int, entry: dict) -> bool:
    """Auto-resume if pause expired. Returns True if still paused."""
    until_raw = entry.get("data_paused_until")
    if not until_raw:
        return False
    until_dt = _parse_until(until_raw)
    if until_dt is None:
        return True
    now = datetime.now(timezone.utc)
    if until_dt.tzinfo is None:
        until_dt = until_dt.replace(tzinfo=timezone.utc)
    if until_dt > now:
        return True
    auth_service.update_user_entry(user_id, _clear_pause_fields)
    log.info(f"VIP reactivado automáticamente | {user_id} (pausa expirada)")
    return False


def is_paused(chat_id: int) -> bool:
    """True if data collection is paused for this VIP (auto-expires when due)."""
    entry = auth_service.get_user_entry(chat_id)
    if not entry or not entry.get("data_paused_until"):
        return False
    return _expire_if_needed(chat_id, entry)


def pause(chat_id: int, *, days: int | None) -> tuple[bool, str | None]:
    """Pause data collection. days=None means indefinite until manual resume."""
    if auth_service.get_user_entry(chat_id) is None:
        return False, "Usuario no encontrado"
    if days is not None and days < 1:
        return False, "Duración inválida"

    now = datetime.now(timezone.utc)
    if days is None:
        until_raw = _INDEFINITE
        label = "indefinida"
    else:
        until_raw = (now + timedelta(days=days)).isoformat()
        label = f"{days} día(s)"

    def _apply(entry: dict) -> None:
        entry["data_paused_at"] = now.isoformat()
        entry["data_paused_until"] = until_raw

    auth_service.update_user_entry(chat_id, _apply)
    clear_chat_state(chat_id)
    log.info(f"VIP pausado | chat {chat_id} | duración {label}")
    return True, None


def resume(chat_id: int) -> bool:
    """Resume data collection. Returns False if user not found or not paused."""
    entry = auth_service.get_user_entry(chat_id)
    if not entry or not entry.get("data_paused_until"):
        return False
    auth_service.update_user_entry(chat_id, _clear_pause_fields)
    log.info(f"VIP reactivado | chat {chat_id}")
    return True


def clear_chat_state(chat_id: int) -> None:
    """Drop all in-memory session state for a paused VIP."""
    import state

    state.history.pop(chat_id, None)
    state.reply_gen.pop(chat_id, None)
    state.chat_bc.pop(chat_id, None)
    state.pending_msg.pop(chat_id, None)
    state.timer_schedule.pop(chat_id, None)
    state.chat_meta.pop(chat_id, None)

    task = state.timers.pop(chat_id, None)
    if task and not task.done():
        task.cancel()

    for ex_id, pending in list(state.pending_approval.items()):
        if pending.get("chat_id") == chat_id:
            state.pending_approval.pop(ex_id, None)

    for esc_id, pending in list(state.pending_escalations.items()):
        if pending.get("chat_id") == chat_id:
            state.pending_escalations.pop(esc_id, None)

    state._clear_timer_schedule(chat_id)
    state._save_runtime_state()


def get_status(chat_id: int) -> dict | None:
    """Status for admin UI. None if user missing."""
    entry = auth_service.get_user_entry(chat_id)
    if not entry:
        return None
    if not entry.get("data_paused_until"):
        return {"paused": False, "until": None, "label": ""}
    if not _expire_if_needed(chat_id, entry):
        return {"paused": False, "until": None, "label": ""}

    until_raw = entry["data_paused_until"]
    if until_raw == _INDEFINITE:
        return {"paused": True, "until": None, "label": "indefinida"}

    until_dt = _parse_until(until_raw)
    if until_dt and until_dt.tzinfo is None:
        until_dt = until_dt.replace(tzinfo=timezone.utc)
    label = until_dt.strftime("%d/%m/%Y %H:%M UTC") if until_dt else "?"
    return {"paused": True, "until": until_dt, "label": label}


def format_profile_line(chat_id: int) -> str | None:
    """One-line status for VIP profile, or None if not paused."""
    status = get_status(chat_id)
    if not status or not status["paused"]:
        return None
    if status["until"] is None:
        return "🔇 VIP pausado — bot inactivo (indefinido)"
    return f"🔇 VIP pausado — bot inactivo hasta {status['label']}"


def duration_options() -> tuple[tuple[str, int | None], ...]:
    return _DURATION_OPTIONS


def uses_synthetic_examples(chat_id: int) -> bool:
    """True when training examples must not be persisted (sandbox only)."""
    from services import sandbox

    return sandbox.is_active(chat_id)