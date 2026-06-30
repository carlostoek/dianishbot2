"""Administración de usuarios autorizados para recibir respuestas del bot."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MessageOrigin,
    Update,
)
from telegram.ext import ContextTypes

import state

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


def get_users_needing_backfill() -> list[int]:
    """Authorized VIPs without history_seeded_at (missing field = needs backfill)."""
    return [
        int(uid)
        for uid, entry in _users.items()
        if not entry.get("history_seeded_at")
    ]


def is_history_seeded(user_id: int) -> bool:
    """True if user entry has history_seeded_at set."""
    entry = _users.get(str(user_id))
    return bool(entry and entry.get("history_seeded_at"))


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


# ═══════════════════════════════════════════════════════
#  INLINE MENU BUILDERS
# ═══════════════════════════════════════════════════════

_MAX_NAME_LEN = 18
ESTADO_TITLE = "📊 *Estado del Bot*"


def _build_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Menu inline principal del administrador."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Usuarios VIP", callback_data="au:list")],
        [InlineKeyboardButton("📊 Estado del Bot", callback_data="au:estado")],
        [InlineKeyboardButton("📈 Fallos del LLM", callback_data="au:fallos")],
        [InlineKeyboardButton("⚠️ Escalaciones", callback_data="au:escalaciones")],
        [InlineKeyboardButton("🤖 Config LLM", callback_data="au:llm")],
        [InlineKeyboardButton("🔍 Trace LLM", callback_data="au:trace")],
        [InlineKeyboardButton("❓ Ayuda", callback_data="au:ayuda")],
        [InlineKeyboardButton("🙈 Ocultar Menu", callback_data="au:ocultar")],
    ])


def _build_llm_menu_keyboard() -> InlineKeyboardMarkup:
    """Submenu: proveedores, modelos del proveedor activo y volver."""
    from services import llm_settings

    active_provider = llm_settings.get_provider()
    active_model = llm_settings.get_model()

    rows: list[list[InlineKeyboardButton]] = []
    provider_buttons = []
    for provider, label in (("deepseek", "DeepSeek"), ("anthropic", "Anthropic")):
        marker = " ✅" if provider == active_provider else ""
        if llm_settings.has_api_key(provider):
            provider_buttons.append(
                InlineKeyboardButton(
                    f"{label}{marker}",
                    callback_data=f"au:llm_set:provider:{provider}",
                ),
            )
        else:
            provider_buttons.append(
                InlineKeyboardButton(
                    f"{label} (sin key)",
                    callback_data="au:llm:nokey",
                ),
            )
    rows.append(provider_buttons)

    for model in llm_settings.MODEL_CATALOG[active_provider]:
        marker = " ✅" if model == active_model else ""
        rows.append([
            InlineKeyboardButton(
                f"{model}{marker}",
                callback_data=f"au:llm_set:model:{model}",
            ),
        ])

    rows.append([
        InlineKeyboardButton("⬅️ Volver al menu", callback_data="au:menu"),
    ])
    return InlineKeyboardMarkup(rows)


def _build_trace_menu_keyboard() -> InlineKeyboardMarkup:
    from services import trace

    estado = "ON" if trace.is_enabled() else "OFF"
    toggle_label = "Desactivar" if trace.is_enabled() else "Activar"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"🔍 Trace: {estado}",
            callback_data="au:trace:noop",
        )],
        [InlineKeyboardButton(
            f"⏻ {toggle_label}",
            callback_data="au:trace:toggle",
        )],
        [InlineKeyboardButton("⬅️ Volver al menu", callback_data="au:menu")],
    ])


def _build_user_list_keyboard() -> InlineKeyboardMarkup:
    """Lista de usuarios VIP con acciones por usuario."""
    rows = []
    for entry in _users.values():
        name = _display_name(entry)
        uid = entry["id"]
        rows.append([
            InlineKeyboardButton(
                f"👤 {name[:_MAX_NAME_LEN]}",
                callback_data=f"au:view:{uid}",
            ),
            InlineKeyboardButton("📝", callback_data=f"au:notes:{uid}"),
            InlineKeyboardButton("🗑", callback_data=f"au:del_confirm:{uid}"),
        ])
    rows.append([
        InlineKeyboardButton("⬅️ Volver al menu", callback_data="au:menu"),
    ])
    return InlineKeyboardMarkup(rows)


def _notes_keyboard_rows(user_id: int) -> list[list[InlineKeyboardButton]]:
    """Fila de botones de notas — vacía si el chat está en sandbox."""
    from services import sandbox
    if sandbox.is_active(user_id):
        return []
    return [[
        InlineKeyboardButton(
            "📝 Agregar Nota", callback_data=f"au:note_add:{user_id}",
        ),
        InlineKeyboardButton(
            "🗑 Borrar Notas", callback_data=f"au:notes_clear:{user_id}",
        ),
    ]]


def _build_user_detail_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Keyboard para la vista detalle de un usuario."""
    rows = list(_notes_keyboard_rows(user_id))
    rows.extend([
        [
            InlineKeyboardButton(
                "🗑 Eliminar Usuario", callback_data=f"au:del_confirm:{user_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "⬅️ Volver a la lista", callback_data="au:list",
            ),
        ],
    ])
    return InlineKeyboardMarkup(rows)


def _build_confirm_delete_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Si, eliminar", callback_data=f"au:del:{user_id}",
            ),
            InlineKeyboardButton(
                "❌ Cancelar", callback_data=f"au:view:{user_id}",
            ),
        ],
    ])


def _build_confirm_clear_notes_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Si, borrar todas", callback_data=f"au:notes_clear_ok:{user_id}",
            ),
            InlineKeyboardButton(
                "❌ Cancelar", callback_data=f"au:view:{user_id}",
            ),
        ],
    ])


def _build_back_to_list_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Volver a la lista", callback_data="au:list")],
    ])


def _build_back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Volver al menu", callback_data="au:menu")],
    ])


async def send_user_list(bot, chat_id: int) -> None:
    """Envia la lista de usuarios VIP con teclado inline de acciones."""
    max_n = _max_users()
    count = len(_users)

    if not _users:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"👥 Usuarios VIP ({count}/{max_n})\n\n"
                "No hay usuarios autorizados.\n\n"
                "Para agregar uno, reenvia un mensaje suyo al bot."
            ),
            reply_markup=_build_back_to_menu_keyboard(),
        )
        return

    lines = [
        f"👥 Usuarios VIP ({count}/{max_n})",
        "",
        "Selecciona un usuario para ver su perfil, o usa los botones:",
        "  👤 = Ver perfil completo",
        "  📝 = Ver y agregar notas",
        "  🗑 = Eliminar usuario",
    ]
    if count < max_n:
        lines.append("")
        lines.append("Para agregar: reenvia un mensaje del usuario al bot.")

    await bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        reply_markup=_build_user_list_keyboard(),
    )


async def send_main_menu(bot, chat_id: int) -> None:
    """Envia el menu inline principal del administrador."""
    await bot.send_message(
        chat_id=chat_id,
        text="📋 Menu Principal — Diana Bot Admin",
        reply_markup=_build_main_menu_keyboard(),
    )


async def send_llm_menu(bot, chat_id: int) -> None:
    """Envia el submenu de configuracion LLM."""
    from services import llm_settings

    text = (
        f"🤖 *Configuración LLM*\n\n"
        f"Activo: {llm_settings.get_display_label()}"
    )
    await bot.send_message(
        chat_id,
        text,
        parse_mode="Markdown",
        reply_markup=_build_llm_menu_keyboard(),
    )


def build_estado_text(*, title: str = ESTADO_TITLE) -> str:
    """Cuerpo compartido de estado para inline y slash."""
    from config import (
        APPROVAL_MODE,
        CONFIDENCE_THRESHOLD,
        OBSERVE_UNAUTHORIZED,
        RESPONSE_DELAY_MAX,
        RESPONSE_DELAY_MIN,
        SILENCE_MINUTES,
    )
    from services.llm_settings import format_estado_llm_line
    from services import trace
    from state import pending_approval

    mode = "Supervisado" if APPROVAL_MODE else "Autonomo"
    delay = (
        f"{SILENCE_MINUTES} min (supervisado)"
        if APPROVAL_MODE
        else f"{RESPONSE_DELAY_MIN}-{RESPONSE_DELAY_MAX} min"
    )
    vip_count = len(_users)
    pending = len(pending_approval)

    return (
        f"{title}\n\n"
        f"*Modo:* {mode}\n"
        f"*Delay:* {delay}\n"
        f"*Umbral confianza:* {CONFIDENCE_THRESHOLD}%\n"
        f"*VIPs autorizados:* {vip_count}\n"
        f"*Borradores pendientes:* {pending}\n"
        f"*Observar no auth:* {'Si' if OBSERVE_UNAUTHORIZED else 'No'}\n"
        f"{format_estado_llm_line()}\n"
        f"{trace.format_estado_line()}"
    )


async def send_estado(bot, chat_id: int) -> None:
    """Envia estado del bot (slash /menu inline)."""
    await bot.send_message(
        chat_id=chat_id,
        text=build_estado_text(),
        parse_mode="Markdown",
        reply_markup=_build_back_to_menu_keyboard(),
    )


async def send_user_detail(bot, chat_id: int, user_id: int) -> None:
    """Envia la vista detalle de un usuario VIP."""
    entry = _users.get(str(user_id))
    if not entry:
        await bot.send_message(chat_id=chat_id, text="Usuario no encontrado.")
        return

    from services import llm as llm_mod

    svc = llm_mod.memory_service
    facts: dict[str, str] = {}
    notes: list[dict] = []
    if svc:
        raw_facts = svc.get_facts(user_id)
        facts = {k: v for k, v in raw_facts.items() if k != "notes"}
        notes = svc.get_notes(user_id)

    username = entry.get("username")
    first_name = entry.get("first_name")
    added = entry.get("added_at", "?")[:10]

    lines = [f"👤 Perfil de {_display_name(entry)}", ""]
    lines.append(f"ID: {user_id}")
    if first_name and username:
        lines.append(f"Nombre: {first_name}")
    lines.append(f"Agregado: {added}")

    if facts:
        lines.append("")
        lines.append("📊 Datos extraidos:")
        labels = {
            "name": "Se llama",
            "occupation": "Trabaja/estudia en",
            "location": "Es de",
            "interests": "Le interesa",
            "relationship": "Estado sentimental",
            "personality": "Su estilo",
            "last_topic": "Ultimo tema",
            "notable": "Dato importante",
        }
        for key, value in facts.items():
            label = labels.get(key, key)
            lines.append(f"  • {label}: {value}")

    if notes:
        from services.memory import extract_note_display_date, extract_note_display_text

        display_notes = []
        for n in notes:
            text = extract_note_display_text(n, user_id)
            if text:
                date = extract_note_display_date(n, user_id)
                display_notes.append((date, text))

        if display_notes:
            lines.append("")
            lines.append(f"📝 Notas de Diana ({len(display_notes)}):")
            for date_str, text in display_notes[-5:]:
                preview = text[:100] + ("..." if len(text) > 100 else "")
                lines.append(f"  [{date_str}] {preview}")
            if len(display_notes) > 5:
                lines.append(f"  ... y {len(display_notes) - 5} mas")

    if not facts and not notes:
        lines.append("")
        lines.append("Sin datos extraidos ni notas todavia.")

    from services import sandbox
    if sandbox.is_active(user_id):
        lines.append("")
        lines.append("🧪 Sandbox activo — notas deshabilitadas.")

    await bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        reply_markup=_build_user_detail_keyboard(user_id),
    )


async def send_user_notes_view(bot, chat_id: int, user_id: int) -> None:
    """Envia la vista de notas de un usuario."""
    entry = _users.get(str(user_id))
    if not entry:
        await bot.send_message(chat_id=chat_id, text="Usuario no encontrado.")
        return

    from services import llm as llm_mod
    from services.memory import extract_note_display_date, extract_note_display_text

    svc = llm_mod.memory_service
    notes: list[dict] = []
    if svc:
        notes = svc.get_notes(user_id)

    display_notes = []
    for n in notes:
        text = extract_note_display_text(n, user_id)
        if text:
            date = extract_note_display_date(n, user_id)
            display_notes.append((date, text))

    lines = [
        f"📝 Notas de {_display_name(entry)}",
        f"ID: {user_id}",
        "",
    ]

    if display_notes:
        lines.append(f"{len(display_notes)} nota(s):")
        for date_str, text in display_notes:
            lines.append(f"  [{date_str}] {text}")
    else:
        lines.append("No hay notas para este usuario.")
        lines.append("Usa el boton 📝 Agregar Nota para crear una.")

    note_rows = _notes_keyboard_rows(user_id)
    note_rows.append(
        [InlineKeyboardButton("⬅️ Volver a la lista", callback_data="au:list")],
    )
    await bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(note_rows),
    )


async def send_confirm_delete(bot, chat_id: int, user_id: int) -> None:
    """Envia pantalla de confirmacion para eliminar un usuario."""
    entry = _users.get(str(user_id))
    if not entry:
        await bot.send_message(chat_id=chat_id, text="Usuario no encontrado.")
        return

    name = _display_name(entry)
    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"⚠️ Eliminar a {name}?\n\n"
            f"ID: {user_id}\n\n"
            "Esta accion no se puede deshacer. "
            "El usuario dejara de recibir respuestas automaticas."
        ),
        reply_markup=_build_confirm_delete_keyboard(user_id),
    )


async def send_confirm_clear_notes(bot, chat_id: int, user_id: int) -> None:
    """Envia pantalla de confirmacion para borrar todas las notas."""
    entry = _users.get(str(user_id))
    if not entry:
        await bot.send_message(chat_id=chat_id, text="Usuario no encontrado.")
        return

    from services import llm as llm_mod

    svc = llm_mod.memory_service
    note_count = len(svc.get_notes(user_id)) if svc else 0

    if note_count == 0:
        await bot.send_message(
            chat_id=chat_id,
            text=f"No hay notas para {_display_name(entry)}.",
            reply_markup=_build_back_to_list_keyboard(),
        )
        return

    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"⚠️ Borrar todas las notas de {_display_name(entry)}?\n\n"
            f"Se eliminaran {note_count} nota(s). "
            "Esta accion no se puede deshacer."
        ),
        reply_markup=_build_confirm_clear_notes_keyboard(user_id),
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


def _is_admin(user_id: int | None) -> bool:
    admin_id = get_admin_id()
    return bool(admin_id and user_id == admin_id)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Maneja todos los callbacks del menu admin (au:)."""
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("au:"):
        return False

    if not _is_admin(query.from_user.id):
        await query.answer("No autorizado", show_alert=True)
        return True

    data = query.data[3:]  # strip "au:"
    parts = data.split(":")
    action = parts[0]

    # ── Acciones sin ID ─────────────────────────────────
    if action == "menu":
        await query.answer()
        await _edit_or_send(query, "📋 Menu Principal — Diana Bot Admin",
                            _build_main_menu_keyboard())
        return True

    if action == "list":
        await query.answer()
        await _replace_with_user_list(query)
        return True

    if action == "estado":
        await query.answer()
        await _replace_with_estado(query)
        return True

    if action == "fallos":
        await query.answer()
        await _replace_with_fallos(query)
        return True

    if action == "escalaciones":
        await query.answer()
        await _replace_with_escalaciones(query)
        return True

    if action == "ayuda":
        await query.answer()
        await _replace_with_ayuda(query)
        return True

    if action == "ocultar":
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass
        return True

    if action == "llm":
        if len(parts) >= 2 and parts[1] == "nokey":
            await query.answer("Falta API key en .env", show_alert=True)
            return True
        await query.answer()
        await _replace_with_llm_menu(query)
        return True

    if action == "llm_set":
        from services import llm_settings

        if len(parts) < 3:
            await query.answer("Callback LLM inválido", show_alert=True)
            return True

        sub = parts[1]
        value = ":".join(parts[2:])
        if sub == "provider":
            ok, err = llm_settings.set_provider(value)
            if not ok:
                await query.answer(err or "Error", show_alert=True)
            else:
                await query.answer("Proveedor actualizado")
            await _replace_with_llm_menu(query)
            return True
        if sub == "model":
            ok, err = llm_settings.set_model(value)
            if not ok:
                await query.answer(err or "Error", show_alert=True)
            else:
                await query.answer("Modelo actualizado")
            await _replace_with_llm_menu(query)
            return True

        await query.answer("Acción LLM desconocida", show_alert=True)
        return True

    if action == "trace":
        from services import trace

        if len(parts) >= 2 and parts[1] == "toggle":
            trace.toggle()
            await query.answer(f"Trace {'ON' if trace.is_enabled() else 'OFF'}")
        elif len(parts) >= 2 and parts[1] == "noop":
            await query.answer()
        else:
            await query.answer()
        await _replace_with_trace_menu(query)
        return True

    # ── Acciones con user_id ────────────────────────────
    if len(parts) < 2:
        await query.answer()
        return True

    try:
        user_id = int(parts[1])
    except ValueError:
        await query.answer("ID invalido", show_alert=True)
        return True

    if action == "view":
        await query.answer()
        await _replace_with_detail(query, user_id)
        return True

    if action == "del":
        await query.answer()
        if not remove_user(user_id):
            await query.answer("Usuario no encontrado", show_alert=True)
        else:
            await query.answer("Usuario eliminado")
        await _replace_with_user_list(query)
        return True

    if action == "del_confirm":
        await query.answer()
        await _replace_with_delete_confirm(query, user_id)
        return True

    if action == "notes":
        await query.answer()
        await _replace_with_notes(query, user_id)
        return True

    if action == "note_add":
        await _start_admin_note_capture(query, user_id)
        return True

    if action == "notes_clear":
        from services import sandbox
        if sandbox.is_active(user_id):
            await query.answer(
                "Notas deshabilitadas — chat en sandbox", show_alert=True,
            )
            return True
        await query.answer()
        await _replace_with_clear_notes_confirm(query, user_id)
        return True

    if action == "notes_clear_ok":
        await _execute_clear_notes(query, user_id)
        return True

    await query.answer()
    return True


# ═══════════════════════════════════════════════════════
#  CALLBACK ACTION HELPERS
# ═══════════════════════════════════════════════════════


async def _edit_or_send(query, text: str, markup: InlineKeyboardMarkup) -> None:
    """Intenta editar el mensaje actual; si falla, envia uno nuevo."""
    try:
        await query.edit_message_text(text, reply_markup=markup)
    except Exception:
        await query.message.reply_text(text, reply_markup=markup)


async def _replace_with_user_list(query) -> None:
    """Reemplaza el mensaje actual con la lista de usuarios."""
    max_n = _max_users()
    count = len(_users)

    if not _users:
        await _edit_or_send(
            query,
            (f"👥 Usuarios VIP ({count}/{max_n})\n\n"
             "No hay usuarios autorizados.\n\n"
             "Para agregar uno, reenvia un mensaje suyo al bot."),
            _build_back_to_menu_keyboard(),
        )
        return

    lines = [
        f"👥 Usuarios VIP ({count}/{max_n})",
        "",
        "👤 = Ver perfil  |  📝 = Notas  |  🗑 = Eliminar",
    ]
    if count < max_n:
        lines.append("")
        lines.append("Reenvia un mensaje para agregar un usuario.")

    try:
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=_build_user_list_keyboard(),
        )
    except Exception:
        await query.message.reply_text(
            "\n".join(lines),
            reply_markup=_build_user_list_keyboard(),
        )


async def _replace_with_detail(query, user_id: int) -> None:
    entry = _users.get(str(user_id))
    if not entry:
        await query.answer("Usuario no encontrado", show_alert=True)
        return

    from services import llm as llm_mod
    from services.memory import extract_note_display_date, extract_note_display_text

    svc = llm_mod.memory_service
    facts: dict[str, str] = {}
    notes: list[dict] = []
    if svc:
        raw = svc.get_facts(user_id)
        facts = {k: v for k, v in raw.items() if k != "notes"}
        notes = svc.get_notes(user_id)

    added = entry.get("added_at", "?")[:10]
    lines = [f"👤 Perfil de {_display_name(entry)}", ""]
    lines.append(f"ID: {user_id}")
    if entry.get("first_name") and entry.get("username"):
        lines.append(f"Nombre: {entry['first_name']}")
    lines.append(f"Agregado: {added}")

    if facts:
        lines.append("")
        lines.append("📊 Datos extraidos:")
        labels = {
            "name": "Se llama", "occupation": "Trabaja/estudia en",
            "location": "Es de", "interests": "Le interesa",
            "relationship": "Estado sentimental", "personality": "Su estilo",
            "last_topic": "Ultimo tema", "notable": "Dato importante",
        }
        for key, value in facts.items():
            label = labels.get(key, key)
            lines.append(f"  • {label}: {value}")

    if notes:
        display_notes = []
        for n in notes:
            text = extract_note_display_text(n, user_id)
            if text:
                date = extract_note_display_date(n, user_id)
                display_notes.append((date, text))
        if display_notes:
            lines.append("")
            lines.append(f"📝 Notas ({len(display_notes)}):")
            for date_str, text in display_notes[-5:]:
                preview = text[:100] + ("..." if len(text) > 100 else "")
                lines.append(f"  [{date_str}] {preview}")
            if len(display_notes) > 5:
                lines.append(f"  ... y {len(display_notes) - 5} mas")

    if not facts and not notes:
        lines.append("")
        lines.append("Sin datos extraidos ni notas todavia.")

    from services import sandbox
    if sandbox.is_active(user_id):
        lines.append("")
        lines.append("🧪 Sandbox activo — notas deshabilitadas.")

    lines.append("")
    lines.append("Usa los botones para gestionar este perfil.")

    await _edit_or_send(query, "\n".join(lines),
                        _build_user_detail_keyboard(user_id))


async def _replace_with_delete_confirm(query, user_id: int) -> None:
    entry = _users.get(str(user_id))
    if not entry:
        await query.answer("Usuario no encontrado", show_alert=True)
        return
    name = _display_name(entry)
    await _edit_or_send(
        query,
        (f"⚠️ Eliminar a {name}?\n\n"
         f"ID: {user_id}\n\n"
         "Esta accion no se puede deshacer. "
         "El usuario dejara de recibir respuestas automaticas."),
        _build_confirm_delete_keyboard(user_id),
    )


async def _replace_with_notes(query, user_id: int) -> None:
    entry = _users.get(str(user_id))
    if not entry:
        await query.answer("Usuario no encontrado", show_alert=True)
        return

    from services import llm as llm_mod
    from services import sandbox
    from services.memory import extract_note_display_date, extract_note_display_text

    svc = llm_mod.memory_service
    notes: list[dict] = []
    if svc:
        notes = svc.get_notes(user_id)

    display_notes = []
    for n in notes:
        text = extract_note_display_text(n, user_id)
        if text:
            date = extract_note_display_date(n, user_id)
            display_notes.append((date, text))

    lines = [f"📝 Notas de {_display_name(entry)}", f"ID: {user_id}", ""]
    if display_notes:
        lines.append(f"{len(display_notes)} nota(s):")
        for date_str, text in display_notes:
            lines.append(f"  [{date_str}] {text}")
    else:
        lines.append("No hay notas para este usuario.")
        if not sandbox.is_active(user_id):
            lines.append("Usa 📝 Agregar Nota para crear una.")

    if sandbox.is_active(user_id):
        lines.append("")
        lines.append("🧪 Sandbox activo — notas deshabilitadas.")

    note_rows = _notes_keyboard_rows(user_id)
    note_rows.append(
        [InlineKeyboardButton("⬅️ Volver a la lista", callback_data="au:list")],
    )
    await _edit_or_send(query, "\n".join(lines), InlineKeyboardMarkup(note_rows))


async def _replace_with_clear_notes_confirm(query, user_id: int) -> None:
    entry = _users.get(str(user_id))
    if not entry:
        await query.answer("Usuario no encontrado", show_alert=True)
        return

    from services import llm as llm_mod

    svc = llm_mod.memory_service
    note_count = len(svc.get_notes(user_id)) if svc else 0

    if note_count == 0:
        await query.answer("No hay notas para borrar")
        return

    await _edit_or_send(
        query,
        (f"⚠️ Borrar todas las notas de {_display_name(entry)}?\n\n"
         f"Se eliminaran {note_count} nota(s). "
         "Esta accion no se puede deshacer."),
        _build_confirm_clear_notes_keyboard(user_id),
    )


async def _execute_clear_notes(query, user_id: int) -> None:
    from services import sandbox
    if sandbox.is_active(user_id):
        await query.answer("Notas deshabilitadas — chat en sandbox", show_alert=True)
        return

    from services import llm as llm_mod

    svc = llm_mod.memory_service
    if not svc:
        await query.answer("Memoria no disponible", show_alert=True)
        return

    ok = svc.clear_notes(user_id)
    await query.answer(
        f"Notas borradas ({user_id})" if ok
        else f"No habia notas ({user_id})"
    )
    # Refresh the detail view
    await _replace_with_detail(query, user_id)


async def _start_admin_note_capture(query, user_id: int) -> None:
    """Activa la captura de nota desde el menu admin inline."""
    entry = _users.get(str(user_id))
    if not entry:
        await query.answer("Usuario no encontrado", show_alert=True)
        return

    admin_id = query.from_user.id

    # Check for existing note capture (approval flow)
    if admin_id in state.awaiting_note:
        await query.answer(
            "Ya estas escribiendo una nota para un borrador. "
            "Termina o usa /cancelar_nota.",
            show_alert=True,
        )
        return

    if admin_id in state.awaiting_admin_note:
        await query.answer(
            "Ya estas escribiendo una nota. Termina o usa /cancelar_nota.",
            show_alert=True,
        )
        return

    from services import sandbox
    if sandbox.is_active(user_id):
        await query.answer("Nota deshabilitada — chat en sandbox", show_alert=True)
        return

    state.awaiting_admin_note[admin_id] = {
        "user_id": user_id,
        "username": _display_name(entry),
    }

    await query.answer()
    await _edit_or_send(
        query,
        (f"✏️ Escribe tu nota para {_display_name(entry)}:\n\n"
         "Se guardara en su perfil y se usara en respuestas futuras.\n"
         "Escribe /cancelar_nota para cancelar."),
        _build_back_to_list_keyboard(),
    )


async def _replace_with_llm_menu(query) -> None:
    """Muestra submenu de configuracion LLM."""
    from services import llm_settings

    text = (
        f"🤖 *Configuración LLM*\n\n"
        f"Activo: {llm_settings.get_display_label()}"
    )
    await _edit_or_send(query, text, _build_llm_menu_keyboard())


async def _replace_with_trace_menu(query) -> None:
    from services import trace

    estado = "ON" if trace.is_enabled() else "OFF"
    text = (
        f"🔍 *Trace LLM*\n\n"
        f"Estado: {estado}\n\n"
        "Cuando esta activo, cada llamada al LLM se registra en "
        "`diana_traces.jsonl` con el prompt inyectado, perfil, "
        "modelo y output completo."
    )
    await _edit_or_send(query, text, _build_trace_menu_keyboard())


async def _replace_with_estado(query) -> None:
    """Muestra estado del bot inline."""
    await _edit_or_send(query, build_estado_text(), _build_back_to_menu_keyboard())


async def _replace_with_fallos(query) -> None:
    """Muestra reporte de fallos del LLM inline."""
    from services.training import format_llm_failure_report
    report = format_llm_failure_report(days=7)
    await _edit_or_send(query, report, _build_back_to_menu_keyboard())


async def _replace_with_escalaciones(query) -> None:
    """Muestra reporte de escalaciones inline."""
    import state
    from services.training import format_escalation_report

    live_pending = sum(
        1 for p in state.pending_escalations.values() if not p.get("verdict")
    )
    report = format_escalation_report(days=7, live_pending=live_pending)
    await _edit_or_send(query, report, _build_back_to_menu_keyboard())


async def send_escalaciones(bot, chat_id: int, *, days: int = 7) -> None:
    """Envía reporte de escalaciones (inline / slash)."""
    import state
    from services.training import format_escalation_report

    live_pending = sum(
        1 for p in state.pending_escalations.values() if not p.get("verdict")
    )
    report = format_escalation_report(days=days, live_pending=live_pending)
    await bot.send_message(
        chat_id=chat_id,
        text=report,
        reply_markup=_build_back_to_menu_keyboard(),
    )


async def _replace_with_ayuda(query) -> None:
    """Muestra referencia de comandos inline."""
    text = (
        "❓ *Comandos Disponibles*\n\n"
        "*Gestion de VIPs*\n"
        "`/usuarios` — Listar, agregar \\(reenviando mensaje\\), eliminar VIPs\n"
        "`/notas <id>` — Ver notas y datos extraidos de un VIP\n"
        "`/nota <id> <texto>` — Agregar nota manual para un VIP\n"
        "`/borrar_notas <id>` — Limpiar todas las notas de un VIP\n"
        "`/sandbox on|off <chat_id>` — Modo prueba sin persistencia\n"
        "`/sandbox perfil <name>` — Cambiar perfil \\(ultimo on\\)\n"
        "`/sandbox perfiles | estado | reset`\n\n"
        "*Estado y Monitoreo*\n"
        "`/estado` — Estado actual del bot\n"
        "`/fallos [dias]` — Reporte de fallos del LLM \\(7 dias por defecto\\)\n"
        "`/escalaciones [dias]` — Historial de escalaciones \\(7 dias por defecto\\)\n"
        "`🤖 Config LLM` — Cambiar proveedor/modelo sin reiniciar\n\n"
        "*Trace y Debug*\n"
        "`/trace on|off|estado` — Activar/desactivar traza global del LLM\n"
        "`🔍 Trace LLM` — Toggle desde el menu inline\n\n"
        "*Utilidades*\n"
        "`/menu` — Mostrar el menu inline\n"
        "`/cancelar_nota` — Cancelar captura de nota en progreso\n\n"
        "Tip: Reenvia un mensaje de un usuario al bot para agregarlo como VIP\\."
    )
    await _edit_or_send(query, text, _build_back_to_menu_keyboard())


async def handle_admin_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Captura la nota escrita desde el menu admin inline."""
    msg = update.message
    if not msg or not msg.text:
        return False
    if msg.from_user.id not in state.awaiting_admin_note:
        return False

    stripped = msg.text.strip()

    # /cancelar_nota: cancel admin note capture, then let approval handler
    # also check so it can cancel approval notes if both are active
    if stripped.startswith("/"):
        base_cmd = stripped.split()[0].split("@")[0]
        if base_cmd == "/cancelar_nota":
            note_ctx = state.awaiting_admin_note.pop(msg.from_user.id)
            # If also in approval-note mode, return False so handle_diana_note runs
            also_approval = msg.from_user.id in state.awaiting_note
            await msg.reply_text(
                f"Nota cancelada para {note_ctx['username']}."
                + (" El borrador sigue pendiente." if also_approval else "")
            )
            return not also_approval  # False if also approval, so chain continues
        return False

    note_ctx = state.awaiting_admin_note[msg.from_user.id]
    from services import sandbox
    if sandbox.is_active(note_ctx["user_id"]):
        state.awaiting_admin_note.pop(msg.from_user.id, None)
        await msg.reply_text("Nota deshabilitada en sandbox.")
        return True

    from services import llm as llm_mod

    if not llm_mod.memory_service:
        await msg.reply_text("Memoria no disponible.")
        state.awaiting_admin_note.pop(msg.from_user.id, None)
        return True

    try:
        saved = llm_mod.memory_service.add_note(note_ctx["user_id"], stripped)
    except Exception as e:
        log.error(
            f"Error guardando nota admin | usuario {note_ctx['user_id']}: {e}"
        )
        await msg.reply_text("Error al guardar la nota. Intenta de nuevo o /cancelar_nota.")
        return True

    state.awaiting_admin_note.pop(msg.from_user.id, None)

    if not saved:
        await msg.reply_text(
            "La nota esta vacia o no es valida. Escribe de nuevo o /cancelar_nota."
        )
        return True

    await msg.reply_text(
        f"✓ Nota guardada para {note_ctx['username']}.\n"
        "Se usara en todas las respuestas futuras."
    )
    log.info(
        f"Nota admin guardada | usuario {note_ctx['user_id']} "
        f"({note_ctx['username']}): {stripped[:60]}"
    )
    return True


async def _handle_sandbox_command(msg) -> bool:
    from services import sandbox

    text = (msg.text or "").strip()
    parts = text.split()

    if text == "/sandbox":
        await msg.reply_text(
            "Uso sandbox:\n"
            "/sandbox on <chat_id> — Activar modo prueba (perfil nuevo)\n"
            "/sandbox off <chat_id> — Desactivar y limpiar RAM\n"
            "/sandbox perfil <name> — Cambiar perfil del último on\n"
            "/sandbox perfiles — Listar perfiles disponibles\n"
            "/sandbox estado — Sesiones activas\n"
            "/sandbox reset — Limpiar RAM del chat en foco\n\n"
            "⚠️ La entrega al VIP sigue activa — solo se desactiva la persistencia."
        )
        return True

    if len(parts) < 2:
        await msg.reply_text("Comando sandbox incompleto. Usa /sandbox para ayuda.")
        return True

    sub = parts[1].lower()

    if sub == "on":
        if len(parts) < 3:
            await msg.reply_text("Uso: /sandbox on <chat_id>")
            return True
        try:
            chat_id = int(parts[2])
        except ValueError:
            await msg.reply_text("chat_id debe ser numérico.")
            return True
        ok, err = sandbox.activate(chat_id)
        if not ok:
            await msg.reply_text(f"Error: {err}")
            return True
        reply = (
            f"✓ Sandbox activo en chat {chat_id} — perfil: {sandbox.get_profile(chat_id)}"
        )
        if is_authorized(chat_id, chat_id):
            reply += (
                "\n\n⚠️ VIP autorizado: los mensajes aprobados SÍ se entregan al usuario real."
            )
        await msg.reply_text(reply)
        return True

    if sub == "off":
        if len(parts) < 3:
            await msg.reply_text("Uso: /sandbox off <chat_id>")
            return True
        try:
            chat_id = int(parts[2])
        except ValueError:
            await msg.reply_text("chat_id debe ser numérico.")
            return True
        if not sandbox.is_active(chat_id):
            await msg.reply_text(f"Chat {chat_id} no tenía sandbox activo.")
            return True
        sandbox.reset_chat_state(chat_id)
        sandbox.deactivate(chat_id)
        await msg.reply_text(f"✓ Sandbox desactivado en chat {chat_id}")
        return True

    if sub == "perfil":
        if len(parts) < 3:
            await msg.reply_text("Uso: /sandbox perfil <name>")
            return True
        name = parts[2].lower()
        ok, err = sandbox.set_focus_profile(name)
        if not ok:
            await msg.reply_text(f"Error: {err}")
            return True
        focus = sandbox.get_focus_chat_id()
        await msg.reply_text(
            f"✓ Perfil {name} aplicado al chat {focus}"
        )
        return True

    if sub == "perfiles":
        lines = ["Perfiles sandbox:"]
        for prof in sandbox.list_profiles():
            lines.append(f"  {prof['name']} — {prof['label']}")
        await msg.reply_text("\n".join(lines))
        return True

    if sub == "estado":
        await msg.reply_text(sandbox.format_estado())
        return True

    if sub == "reset":
        focus = sandbox.get_focus_chat_id()
        if focus is None or not sandbox.is_active(focus):
            await msg.reply_text("Sin chat sandbox en foco. Usa /sandbox on <chat_id>.")
            return True
        sandbox.reset_chat_state(focus)
        await msg.reply_text(f"✓ RAM limpiada — sandbox sigue activo en chat {focus}")
        return True

    await msg.reply_text("Subcomando desconocido. Usa /sandbox para ayuda.")
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

    if msg.text and msg.text.startswith("/escalaciones"):
        parts = msg.text.split()
        days = 7
        if len(parts) > 1:
            try:
                days = max(1, min(int(parts[1]), 90))
            except ValueError:
                await msg.reply_text("Uso: /escalaciones [días]  (ej: /escalaciones 7)")
                return True
        await send_escalaciones(context.bot, msg.chat_id, days=days)
        return True

    if msg.text and msg.text.startswith("/trace"):
        from services import trace
        parts = msg.text.split()
        if len(parts) < 2:
            await msg.reply_text(
                f"🔍 Trace LLM: {'ON' if trace.is_enabled() else 'OFF'}\n\n"
                "Uso: /trace on | off | estado"
            )
            return True
        sub = parts[1].lower()
        if sub == "on":
            trace.enable()
            await msg.reply_text("✓ Trace LLM activado. Registrando en diana_traces.jsonl")
        elif sub == "off":
            trace.disable()
            await msg.reply_text("✓ Trace LLM desactivado.")
        elif sub == "estado":
            await msg.reply_text(
                f"🔍 Trace LLM: {'ON' if trace.is_enabled() else 'OFF'}\n"
                f"Archivo: diana_traces.jsonl"
            )
        else:
            await msg.reply_text("Uso: /trace on | off | estado")
        return True

    if msg.text and msg.text.startswith("/sandbox"):
        return await _handle_sandbox_command(msg)

    # /notas before /nota (space) — prefix collision guard
    if msg.text and msg.text.startswith("/notas"):
        parts = msg.text.split()
        if len(parts) < 2:
            await msg.reply_text("Uso: /notas <user_id>")
            return True
        try:
            target_id = int(parts[1])
        except ValueError:
            await msg.reply_text("ID inválido.")
            return True
        from services import llm as llm_mod
        svc = llm_mod.memory_service
        if not svc:
            await msg.reply_text("Memoria no disponible.")
            return True
        from services.memory import extract_note_display_date, extract_note_display_text

        notes = svc.get_notes(target_id)
        facts = {k: v for k, v in svc.get_facts(target_id).items() if k != "notes"}
        display_notes = []
        for n in notes:
            text = extract_note_display_text(n, target_id)
            if text:
                date = extract_note_display_date(n, target_id)
                display_notes.append((date, text))
        if not display_notes and not facts:
            await msg.reply_text(f"Sin datos para {target_id}.")
            return True
        lines = [f"Perfil — {target_id}", "─" * 28]
        if display_notes:
            lines.append("Notas de Diana:")
            for date_str, text in display_notes:
                lines.append(f"  [{date_str}] {text}")
        if facts:
            lines.append("Datos extraídos:")
            for k, v in facts.items():
                lines.append(f"  {k}: {v}")
        await msg.reply_text("\n".join(lines))
        return True

    if msg.text and msg.text.startswith("/nota "):
        parts = msg.text.split(maxsplit=2)
        if len(parts) < 3:
            await msg.reply_text(
                "Uso: /nota <user_id> <texto>\n"
                "Ejemplo: /nota 123456 Es muy sensible, no hacer bromas pesadas"
            )
            return True
        try:
            target_id = int(parts[1])
        except ValueError:
            await msg.reply_text("El user_id debe ser numérico.")
            return True
        from services import sandbox
        if sandbox.is_active(target_id):
            await msg.reply_text("Nota deshabilitada — chat en sandbox.")
            return True
        from services import llm as llm_mod
        if not llm_mod.memory_service:
            await msg.reply_text("Memoria no disponible.")
            return True
        note_text = parts[2].strip()
        try:
            saved = llm_mod.memory_service.add_note(target_id, note_text)
        except Exception as e:
            log.error(f"Error guardando nota manual | usuario {target_id}: {e}")
            await msg.reply_text("Error al guardar la nota. Intenta de nuevo.")
            return True
        if saved:
            await msg.reply_text(f"✓ Nota guardada para {target_id}.")
            log.info(
                f"Nota manual guardada | usuario {target_id}: {note_text[:60]}"
            )
        else:
            await msg.reply_text("La nota está vacía o no es válida.")
        return True

    if msg.text and msg.text.startswith("/borrar_notas"):
        parts = msg.text.split()
        if len(parts) < 2:
            await msg.reply_text("Uso: /borrar_notas <user_id>")
            return True
        try:
            target_id = int(parts[1])
        except ValueError:
            await msg.reply_text("ID inválido.")
            return True
        from services import sandbox
        if sandbox.is_active(target_id):
            await msg.reply_text("Nota deshabilitada — chat en sandbox.")
            return True
        from services import llm as llm_mod
        if not llm_mod.memory_service:
            await msg.reply_text("Memoria no disponible.")
            return True
        ok = llm_mod.memory_service.clear_notes(target_id)
        await msg.reply_text(
            f"✓ Notas borradas para {target_id}." if ok
            else f"No había notas para {target_id}."
        )
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