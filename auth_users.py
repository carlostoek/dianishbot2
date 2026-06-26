"""Administración de usuarios autorizados para recibir respuestas del bot."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageOrigin, Update
from telegram.ext import ContextTypes

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


def _display_name(entry: dict) -> str:
    if entry.get("username"):
        return f"@{entry['username']}"
    if entry.get("first_name"):
        return entry["first_name"]
    return f"ID {entry['id']}"


def _format_user_line(index: int, entry: dict) -> str:
    name = _display_name(entry)
    extra = ""
    if entry.get("username") and entry.get("first_name"):
        extra = f" ({entry['first_name']})"
    return f"{index}. {name}{extra} — ID: {entry['id']}"


def _list_keyboard() -> InlineKeyboardMarkup | None:
    if not _users:
        return None
    rows = []
    for entry in _users.values():
        label = f"Eliminar {_display_name(entry)}"
        rows.append([
            InlineKeyboardButton(
                label[:60],
                callback_data=f"au:del:{entry['id']}",
            )
        ])
    return InlineKeyboardMarkup(rows)


async def send_user_list(bot, chat_id: int) -> None:
    max_n = _max_users()
    count = len(_users)
    lines = [f"Usuarios autorizados ({count}/{max_n})", "─" * 22]

    if not _users:
        lines.append("No hay usuarios autorizados.")
        lines.append("")
        lines.append("Para agregar uno, reenvía un mensaje suyo al bot.")
    else:
        for i, entry in enumerate(_users.values(), 1):
            lines.append(_format_user_line(i, entry))
        lines.append("")
        lines.append("Para agregar: reenvía un mensaje del usuario.")
        lines.append("Para eliminar: usa los botones de abajo.")

    await bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        reply_markup=_list_keyboard(),
    )


def _extract_forwarded_user(msg) -> tuple[int, str | None, str | None] | None:
    origin = msg.forward_origin
    if not origin:
        return None

    if origin.type == MessageOrigin.USER:
        user = origin.sender_user
        return user.id, user.username, user.first_name

    return None


def add_user(user_id: int, username: str | None, first_name: str | None) -> str:
    key = str(user_id)
    if key in _users:
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
    return "ok"


def remove_user(user_id: int) -> bool:
    key = str(user_id)
    if key not in _users:
        return False
    entry = _users.pop(key)
    _save()
    log.info(f"Usuario autorizado eliminado: {entry['id']} ({entry.get('username')})")
    return True


def set_admin_id(user_id: int) -> None:
    global _admin_id
    _admin_id = user_id


def get_admin_id() -> int | None:
    if _cfg.get("admin_id"):
        return _cfg["admin_id"]
    return _admin_id


def _is_admin(user_id: int | None) -> bool:
    admin_id = get_admin_id()
    return bool(admin_id and user_id == admin_id)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("au:"):
        return False

    if not _is_admin(query.from_user.id):
        await query.answer("No autorizado", show_alert=True)
        return True

    parts = query.data.split(":")
    if len(parts) != 3 or parts[1] != "del":
        await query.answer()
        return True

    try:
        user_id = int(parts[2])
    except ValueError:
        await query.answer("ID inválido", show_alert=True)
        return True

    if not remove_user(user_id):
        await query.answer("Usuario no encontrado", show_alert=True)
        return True

    await query.answer("Usuario eliminado")
    await send_user_list(context.bot, query.message.chat_id)
    try:
        await query.message.delete()
    except Exception:
        log.debug("No se pudo borrar el mensaje de lista de usuarios (puede ser ya eliminado)", exc_info=True)
    return True


async def handle_admin_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    msg = update.message
    if not msg or not _is_admin(msg.from_user.id):
        return False

    if msg.text == "/usuarios":
        await send_user_list(context.bot, msg.chat_id)
        return True

    if msg.text and msg.text.startswith("/fallos"):
        from services.training import format_llm_failure_report
        parts = msg.text.split()
        days = 7
        if len(parts) > 1:
            try:
                days = max(1, min(int(parts[1]), 90))
            except ValueError:
                await msg.reply_text("Uso: /fallos [días]  (ej: /fallos 7)")
                return True
        await msg.reply_text(format_llm_failure_report(days))
        return True

    if msg.forward_origin:
        origin = msg.forward_origin
        if origin.type == MessageOrigin.HIDDEN_USER:
            await msg.reply_text(
                "No puedo obtener el ID de ese usuario — tiene la privacidad "
                "de reenvío activada. Pídele que la desactive o que te escriba "
                "directamente al bot primero."
            )
            return True

        extracted = _extract_forwarded_user(msg)
        if not extracted:
            await msg.reply_text(
                "No pude identificar al usuario de ese reenvío. "
                "Reenvía un mensaje directo del chat privado con él."
            )
            return True

        user_id, username, first_name = extracted
        result = add_user(user_id, username, first_name)
        if result == "already":
            await msg.reply_text(
                f"{_display_name(_users[str(user_id)])} ya está en la lista."
            )
        elif result == "full":
            await msg.reply_text(
                f"Lista llena ({_max_users()} usuarios). Elimina uno antes de agregar."
            )
        else:
            name = _display_name(_users[str(user_id)])
            await msg.reply_text(
                f"Agregado: {name} (ID: {user_id})\n\n"
                "Pídele que te escriba un mensaje nuevo a tu cuenta de Diana "
                "(no al bot) para activar la cobertura."
            )
        return True

    return False