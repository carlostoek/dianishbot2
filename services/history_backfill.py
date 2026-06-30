"""VIP chat history backfill queue and asyncio scheduler."""
import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import auth_users
from config import (
    BACKFILL_INTERVAL_SEC,
    BACKFILL_MSG_LIMIT,
    BACKFILL_QUEUE_FILE,
    DIANA_ADMIN_CHAT_ID,
)
from services.chat_history import load_chat_history, seed_chat_history

log = logging.getLogger("diana")

_session_lock = asyncio.Lock()
_queue_lock = threading.Lock()


def _queue_path() -> Path:
    return Path(BACKFILL_QUEUE_FILE)


def _default_queue() -> dict:
    return {
        "pending": [],
        "last_processed_at": None,
        "last_error": None,
    }


def _save_queue_unlocked(data: dict) -> None:
    path = _queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_queue_unlocked() -> dict:
    path = _queue_path()
    if not path.exists():
        return _default_queue()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise json.JSONDecodeError("root not object", "", 0)
        data.setdefault("pending", [])
        data.setdefault("last_processed_at", None)
        data.setdefault("last_error", None)
        return data
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        log.error("Cola de backfill corrupta o ilegible: %s", e)
        if path.exists():
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = path.with_name(f"{path.stem}.corrupt.{ts}{path.suffix}")
            try:
                os.replace(path, backup)
                log.error("Cola respaldada en %s", backup)
            except OSError as oe:
                log.error("No se pudo respaldar cola corrupta: %s", oe)
        rebuilt = _default_queue()
        rebuilt["pending"] = list(auth_users.get_users_needing_backfill())
        _save_queue_unlocked(rebuilt)
        log.info(
            "Cola reconstruida con %s VIP(s) pendientes",
            len(rebuilt["pending"]),
        )
        return rebuilt


def _load_queue() -> dict:
    with _queue_lock:
        return _load_queue_unlocked()


def _save_queue(data: dict) -> None:
    with _queue_lock:
        _save_queue_unlocked(data)


_SESSION_PERMANENT_MARKERS = (
    "AuthKey",
    "SessionRevoked",
    "SessionExpired",
    "SessionPasswordNeeded",
    "AuthKeyUnregistered",
    "AuthKeyDuplicated",
)


def is_permanent_error(exc: BaseException) -> bool:
    """True for unrecoverable entity/user/session errors (do not re-enqueue)."""
    name = type(exc).__name__
    permanent_names = {
        "ValueError",
        "PeerIdInvalidError",
        "InputUserDeactivatedError",
        "UserIdInvalidError",
        "UsernameNotOccupiedError",
        "UsernameInvalidError",
    }
    if name in permanent_names:
        return True
    if any(marker in name for marker in _SESSION_PERMANENT_MARKERS):
        return True
    msg = str(exc).lower()
    return "could not find" in msg or "no user has" in msg


def should_mark_history_seeded(
    user_id: int,
    seeded_count: int,
    telethon_message_count: int,
) -> bool:
    """Mark seeded only when backfill actually populated or confirmed empty/historic."""
    if seeded_count > 0:
        return True
    if load_chat_history(user_id):
        return True
    if telethon_message_count == 0:
        return True
    return False


def enqueue(user_id: int) -> None:
    """Dedupe; append if not in pending and not already seeded."""
    with _queue_lock:
        if auth_users.is_history_seeded(user_id):
            return
        data = _load_queue_unlocked()
        pending = data.setdefault("pending", [])
        if user_id in pending:
            return
        pending.append(user_id)
        _save_queue_unlocked(data)
    log.info("Backfill encolado: user_id=%s", user_id)


def enqueue_missing_vips() -> int:
    """Enqueue all authorized VIPs lacking history_seeded_at. Returns count enqueued."""
    count = 0
    with _queue_lock:
        data = _load_queue_unlocked()
        pending = data.setdefault("pending", [])
        pending_set = set(pending)
        for user_id in auth_users.get_users_needing_backfill():
            if auth_users.is_history_seeded(user_id) or user_id in pending_set:
                continue
            pending.append(user_id)
            pending_set.add(user_id)
            count += 1
        if count:
            _save_queue_unlocked(data)
    return count


def dequeue_next() -> int | None:
    """Pop front of pending queue; persist."""
    with _queue_lock:
        data = _load_queue_unlocked()
        pending = data.get("pending") or []
        if not pending:
            return None
        user_id = pending.pop(0)
        data["pending"] = pending
        _save_queue_unlocked(data)
        return user_id


def dequeue(user_id: int) -> bool:
    """Remove user_id from pending queue if present."""
    with _queue_lock:
        data = _load_queue_unlocked()
        pending = data.get("pending") or []
        if user_id not in pending:
            return False
        data["pending"] = [u for u in pending if u != user_id]
        _save_queue_unlocked(data)
        return True


def pending_count() -> int:
    with _queue_lock:
        data = _load_queue_unlocked()
        return len(data.get("pending") or [])


async def _notify_backfill_result(
    bot,
    user_id: int,
    name: str,
    success: bool,
    detail: str,
) -> None:
    if not DIANA_ADMIN_CHAT_ID:
        return
    if success:
        text = (
            f"✅ Backfill historial OK\n"
            f"• VIP: {name} ({user_id})\n"
            f"• {detail}"
        )
    else:
        text = (
            f"⚠️ Backfill historial falló\n"
            f"• VIP: {name} ({user_id})\n"
            f"• {detail}"
        )
    try:
        await bot.send_message(chat_id=DIANA_ADMIN_CHAT_ID, text=text)
    except Exception as e:
        log.error("Error notificando backfill a Diana: %s", e)


async def _process_one(app) -> None:
    user_id = dequeue_next()
    if user_id is None:
        return

    display_name = f"ID {user_id}"

    if not auth_users.is_authorized(user_id):
        log.warning("Backfill omitido: VIP %s ya no autorizado", user_id)
        auth_users.mark_history_seeded(user_id, error="usuario no autorizado")
        with _queue_lock:
            data = _load_queue_unlocked()
            data["last_processed_at"] = datetime.now(timezone.utc).isoformat()
            _save_queue_unlocked(data)
        return

    success = False
    err_text: str | None = None
    permanent = False
    reenqueue = False

    async with _session_lock:
        try:
            from services.telethon_import import fetch_vip_history

            messages, entity_name = await fetch_vip_history(user_id, BACKFILL_MSG_LIMIT)
            display_name = entity_name or display_name
            seeded_count = seed_chat_history(user_id, messages)
            telethon_count = len(messages)

            if should_mark_history_seeded(user_id, seeded_count, telethon_count):
                auth_users.mark_history_seeded(user_id)
                if seeded_count:
                    detail = f"{seeded_count} mensaje(s) sembrados"
                elif telethon_count:
                    detail = "historial ya existía (skip-if-nonempty)"
                else:
                    detail = "chat vacío (0 mensajes)"
                try:
                    await _notify_backfill_result(
                        app.bot, user_id, display_name, True, detail,
                    )
                except Exception as notify_err:
                    log.error(
                        "Error notificando backfill exitoso para %s: %s",
                        user_id,
                        notify_err,
                    )
                success = True
                log.info(
                    "Backfill OK user_id=%s seeded=%s pending_remaining=%s",
                    user_id,
                    seeded_count,
                    pending_count(),
                )
            else:
                err_text = "seed omitido: historial en RAM o sandbox activo"
                reenqueue = True
                log.warning(
                    "Backfill seed omitido para %s (RAM/sandbox) — reencolar",
                    user_id,
                )
                try:
                    await _notify_backfill_result(
                        app.bot,
                        user_id,
                        display_name,
                        False,
                        "Seed omitido (RAM/sandbox) — reencolado al final",
                    )
                except Exception as notify_err:
                    log.error(
                        "Error notificando backfill omitido para %s: %s",
                        user_id,
                        notify_err,
                    )
        except Exception as e:
            err_text = f"{type(e).__name__}: {e}"
            log.warning("Backfill falló para %s: %s", user_id, err_text)
            permanent = is_permanent_error(e)

            if permanent:
                auth_users.mark_history_seeded(user_id, error=err_text)
                try:
                    await _notify_backfill_result(
                        app.bot,
                        user_id,
                        display_name,
                        False,
                        f"Error permanente — no se reintentará: {err_text}",
                    )
                except Exception as notify_err:
                    log.error(
                        "Error notificando backfill permanente para %s: %s",
                        user_id,
                        notify_err,
                    )
            elif not auth_users.is_history_seeded(user_id):
                reenqueue = True
                try:
                    await _notify_backfill_result(
                        app.bot,
                        user_id,
                        display_name,
                        False,
                        f"Error transitorio — reencolado al final: {err_text}",
                    )
                except Exception as notify_err:
                    log.error(
                        "Error notificando backfill transitorio para %s: %s",
                        user_id,
                        notify_err,
                    )
            else:
                log.warning(
                    "Backfill notify falló tras seed exitoso para %s — no reencolar",
                    user_id,
                )

    with _queue_lock:
        data = _load_queue_unlocked()
        data["last_processed_at"] = datetime.now(timezone.utc).isoformat()
        if success:
            data["last_error"] = None
        else:
            data["last_error"] = err_text
            if (
                reenqueue
                and not permanent
                and not auth_users.is_history_seeded(user_id)
            ):
                pending = data.setdefault("pending", [])
                if user_id not in pending:
                    pending.append(user_id)
        _save_queue_unlocked(data)


async def _scheduler_loop(app) -> None:
    while True:
        queue = _load_queue()
        if queue.get("pending"):
            try:
                await _process_one(app)
            except Exception as e:
                log.error("Error inesperado en worker de backfill: %s", e)
        await asyncio.sleep(BACKFILL_INTERVAL_SEC)


def start_scheduler(app) -> None:
    """Start hourly backfill worker; disabled gracefully if Telethon unavailable."""
    try:
        from services import telethon_import
        telethon_import.get_api_credentials()
    except (ImportError, RuntimeError, ValueError) as e:
        log.warning("Backfill scheduler deshabilitado: %s", e)
        return
    asyncio.create_task(_scheduler_loop(app))
    log.info(
        "Backfill scheduler iniciado (intervalo=%ss, cola=%s)",
        BACKFILL_INTERVAL_SEC,
        BACKFILL_QUEUE_FILE,
    )


# Backward compat for tests importing private name
_is_permanent_error = is_permanent_error