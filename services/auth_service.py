"""VIP allowlist — pure logic, no Telegram imports."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("diana.auth_users")

_cfg: dict[str, Any] = {}
_users: dict[str, dict] = {}
_admin_id: int | None = None
_file_mtime: float | None = None


def configure(**kwargs: Any) -> None:
    global _cfg
    _cfg = kwargs
    _load()


def _users_path() -> Path:
    return Path(_cfg.get("users_file", "diana_authorized_users.json"))


def _max_users() -> int:
    return int(_cfg.get("max_users", 10))


def _load(*, seed_if_missing: bool = True) -> None:
    global _users, _file_mtime
    path = _users_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _users = {str(uid): entry for uid, entry in data.get("users", {}).items()}
            _file_mtime = path.stat().st_mtime
            return
        except Exception as e:
            log.error(f"Error cargando usuarios autorizados: {e}")

    if not seed_if_missing:
        return

    seed = _cfg.get("seed_user_ids") or []
    _users = {}
    for user_id in seed:
        _users[str(user_id)] = {
            "id": user_id,
            "username": None,
            "first_name": None,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
    if _users:
        _save()
        log.info(f"Usuarios iniciales sembrados: {len(_users)}")


def _reload_if_changed() -> None:
    path = _users_path()
    if not path.exists():
        return
    mtime = path.stat().st_mtime
    if _file_mtime is None or mtime > _file_mtime:
        _load(seed_if_missing=False)


def _save() -> None:
    global _file_mtime
    path = _users_path()
    path.write_text(
        json.dumps({"users": _users}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _file_mtime = path.stat().st_mtime


def is_authorized(user_id: int | None, chat_id: int | None = None) -> bool:
    _reload_if_changed()
    if user_id is not None and str(user_id) in _users:
        return True
    if chat_id is not None and str(chat_id) in _users:
        return True
    return False


def get_authorized_ids() -> set[int]:
    return {int(uid) for uid in _users}


def get_user_count() -> int:
    _reload_if_changed()
    return len(_users)


def get_max_users() -> int:
    return _max_users()


def all_user_entries() -> list[dict]:
    _reload_if_changed()
    return list(_users.values())


def mark_history_seeded(user_id: int, *, error: str | None = None) -> None:
    """Set history_seeded_at (and optional history_seed_error) after backfill attempt."""
    key = str(user_id)
    if key not in _users:
        return
    _users[key]["history_seeded_at"] = datetime.now(timezone.utc).isoformat()
    if error:
        _users[key]["history_seed_error"] = error
    else:
        _users[key].pop("history_seed_error", None)
    _save()


def is_retriable_seed_error(error: str | None) -> bool:
    """Entity-resolution failures may succeed after cache/dialog refresh."""
    if not error:
        return False
    low = error.lower()
    return "could not find" in low or "could not resolve" in low


def get_user_entry(user_id: int) -> dict | None:
    _reload_if_changed()
    return _users.get(str(user_id))


def get_users_needing_backfill() -> list[int]:
    """Authorized VIPs without history_seeded_at (missing field = needs backfill)."""
    return [
        int(uid)
        for uid, entry in _users.items()
        if not entry.get("history_seeded_at")
        or is_retriable_seed_error(entry.get("history_seed_error"))
    ]


def is_history_seeded(user_id: int) -> bool:
    """True if user entry has history_seeded_at set."""
    entry = _users.get(str(user_id))
    if not entry or not entry.get("history_seeded_at"):
        return False
    if is_retriable_seed_error(entry.get("history_seed_error")):
        return False
    return True


def add_user(user_id: int, username: str | None, first_name: str | None) -> str:
    key = str(user_id)
    if key in _users:
        if not is_history_seeded(user_id):
            try:
                from services import history_backfill
                history_backfill.enqueue(user_id)
            except Exception as e:
                log.warning(f"No se pudo encolar backfill para {user_id}: {e}")
        return "already"

    if len(_users) >= _max_users():
        return "full"

    _users[key] = {
        "id": user_id,
        "username": username,
        "first_name": first_name,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    _save()
    log.info(f"Usuario autorizado agregado: {user_id} ({username or first_name})")
    try:
        from services import history_backfill
        history_backfill.enqueue(user_id)
    except Exception as e:
        log.warning(f"No se pudo encolar backfill para {user_id}: {e}")
    return "ok"


def remove_user(user_id: int) -> bool:
    key = str(user_id)
    if key not in _users:
        return False
    entry = _users.pop(key)
    _save()
    log.info(f"Usuario autorizado eliminado: {entry['id']} ({entry.get('username')})")
    try:
        from services import history_backfill
        history_backfill.dequeue(user_id)
    except Exception as e:
        log.warning(f"No se pudo desencolar backfill para {user_id}: {e}")
    try:
        from services import chat_history
        chat_history.clear_chat_history(user_id)
    except Exception as e:
        log.warning(f"No se pudo limpiar chat_history para {user_id}: {e}")
    return True


def set_admin_id(user_id: int) -> None:
    global _admin_id
    _admin_id = user_id


def get_admin_id() -> int | None:
    if _cfg.get("admin_id"):
        return _cfg["admin_id"]
    return _admin_id


def is_admin(user_id: int | None) -> bool:
    admin_id = get_admin_id()
    return bool(admin_id and user_id == admin_id)


def is_auto_send_enabled(user_id: int) -> bool:
    """True if this VIP bypasses supervised approval and receives auto replies."""
    _reload_if_changed()
    entry = _users.get(str(user_id))
    return bool(entry and entry.get("auto_send"))


def set_auto_send(user_id: int, enabled: bool) -> bool:
    """Enable or disable per-user auto-send. Returns False if user not found."""
    key = str(user_id)
    if key not in _users:
        return False
    if enabled:
        _users[key]["auto_send"] = True
    else:
        _users[key].pop("auto_send", None)
    _save()
    state = "activado" if enabled else "desactivado"
    log.info(f"Envío automático {state} para VIP {user_id}")
    return True


def update_user_entry(user_id: int, mutator) -> dict | None:
    """Apply mutator(entry) and persist. Returns updated entry or None if missing."""
    _reload_if_changed()
    key = str(user_id)
    entry = _users.get(key)
    if entry is None:
        return None
    mutator(entry)
    _save()
    return entry