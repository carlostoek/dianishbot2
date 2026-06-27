"""Admin menu system for Diana bot.

Provides a persistent ReplyKeyboardMarkup-based menu and new admin commands
(/start, /menu, /estado, /ayuda, /ocultar_menu). Integrates with the existing
router by intercepting admin messages before the legacy slash handler.
"""

import logging

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import ContextTypes

from config import (
    APPROVAL_MODE,
    CONFIDENCE_THRESHOLD,
    LLM_PROVIDER,
    OBSERVE_UNAUTHORIZED,
    RESPONSE_DELAY_MAX,
    RESPONSE_DELAY_MIN,
    SILENCE_MINUTES,
)
from state import pending_approval
import auth_users

log = logging.getLogger("diana")

# ── Keyboard layout ────────────────────────────────────────

_MAIN_BUTTONS = [
    ["👥 Usuarios", "📊 Estado"],
    ["📈 Fallos", "❓ Ayuda"],
]

HIDE_KEYBOARD = ReplyKeyboardRemove()


def build_main_keyboard() -> ReplyKeyboardMarkup:
    """Build the persistent ReplyKeyboardMarkup for the admin menu."""
    return ReplyKeyboardMarkup(
        _MAIN_BUTTONS,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Toca un boton o escribe / para comandos...",
    )


# ── Routing tables ─────────────────────────────────────────

_MENU_TEXT_MAP: dict[str, str] = {
    "Usuarios": "usuarios",
    "Estado": "estado",
    "Fallos": "fallos",
    "Ayuda": "ayuda",
}

# Strip leading emoji (and optional space) to look up plain label.
# e.g. "👥 Usuarios" -> "Usuarios",  "📊 Estado" -> "Estado"
def _strip_emoji(text: str) -> str:
    for i, ch in enumerate(text):
        if ch.isalnum() or ch in "/":
            return text[i:].strip()
    return text.strip()


_NEW_SLASH_COMMANDS = frozenset({
    "/start", "/menu", "/estado", "/ayuda", "/ocultar_menu",
})


# ── Router entry point ─────────────────────────────────────

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Route menu button presses and new admin commands to their handlers.

    Must be called BEFORE ``auth_users.handle_admin_message()`` in the router
    so that menu button texts and new slash commands are intercepted before
    the legacy handler.
    """
    msg = update.message
    if not msg or not msg.text:
        return False

    text = msg.text.strip()

    # ── Button texts (emoji-prefixed) ─────────────────────
    plain = _strip_emoji(text)
    if plain in _MENU_TEXT_MAP:
        action = _MENU_TEXT_MAP[plain]
        return await _route_menu_action(action, msg, context)

    # ── New slash commands ────────────────────────────────
    cmd = text.split()[0]  # "/estado" from "/estado" or "/estado extra"
    if cmd in _NEW_SLASH_COMMANDS:
        return await _route_slash_command(cmd, msg, context)

    return False


# ── Internal routing ───────────────────────────────────────

async def _route_menu_action(action: str, msg, context: ContextTypes.DEFAULT_TYPE) -> bool:
    kb = build_main_keyboard()

    if action == "usuarios":
        await auth_users.send_user_list(context.bot, msg.chat_id)
        return True

    if action == "estado":
        await _send_estado(msg, kb)
        return True

    if action == "fallos":
        from services.training import format_llm_failure_report
        report = format_llm_failure_report(days=7)
        await msg.reply_text(report, reply_markup=kb)
        return True

    if action == "ayuda":
        await _send_ayuda(msg, kb)
        return True

    return False


async def _route_slash_command(cmd: str, msg, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if cmd == "/start":
        await _send_start(msg)
        return True

    if cmd == "/menu":
        await msg.reply_text("Menu principal", reply_markup=build_main_keyboard())
        return True

    if cmd == "/estado":
        await _send_estado(msg, build_main_keyboard())
        return True

    if cmd == "/ayuda":
        await _send_ayuda(msg, build_main_keyboard())
        return True

    if cmd == "/ocultar_menu":
        await msg.reply_text(
            "Teclado ocultado. Escribe /menu para mostrarlo de nuevo.",
            reply_markup=HIDE_KEYBOARD,
        )
        return True

    return False


# ── Response builders ──────────────────────────────────────

async def _send_start(msg) -> None:
    """Welcome message with menu keyboard (first interaction)."""
    text = (
        "Hola, Diana. El bot esta activo.\n\n"
        "Usa los botones para acceder rapido a las funciones principales.\n"
        "Tambien podes escribir / para ver todos los comandos disponibles."
    )
    await msg.reply_text(text, reply_markup=build_main_keyboard())


async def _send_estado(msg, kb: ReplyKeyboardMarkup) -> None:
    """Bot status report: mode, delay, VIP count, pending drafts."""
    mode = "Supervisado" if APPROVAL_MODE else "Autonomo"
    delay = (
        f"{SILENCE_MINUTES} min (supervisado)"
        if APPROVAL_MODE
        else f"{RESPONSE_DELAY_MIN}-{RESPONSE_DELAY_MAX} min"
    )
    vip_count = len(auth_users.get_authorized_ids())
    pending = len(pending_approval)

    text = (
        f"*Estado del Bot*\n\n"
        f"*Modo:* {mode}\n"
        f"*Delay:* {delay}\n"
        f"*Umbral confianza:* {CONFIDENCE_THRESHOLD}%\n"
        f"*VIPs autorizados:* {vip_count}\n"
        f"*Borradores pendientes:* {pending}\n"
        f"*Observar no auth:* {'Si' if OBSERVE_UNAUTHORIZED else 'No'}\n"
        f"*LLM:* {LLM_PROVIDER}"
    )
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def _send_ayuda(msg, kb: ReplyKeyboardMarkup) -> None:
    """Full command reference for the admin."""
    text = (
        "*Comandos disponibles*\n\n"
        "*Gestion de VIPs*\n"
        "`/usuarios` - Listar, agregar (reenviando mensaje), eliminar VIPs\n"
        "`/notas <id>` - Ver notas y datos extraidos de un VIP\n"
        "`/nota <id> <texto>` - Agregar nota manual para un VIP\n"
        "`/borrar_notas <id>` - Limpiar todas las notas de un VIP\n\n"
        "*Estado y monitoreo*\n"
        "`/estado` - Estado actual del bot\n"
        "`/fallos [dias]` - Reporte de fallos del LLM (7 dias por defecto)\n\n"
        "*Utilidades*\n"
        "`/menu` - Mostrar el menu de botones\n"
        "`/cancelar_nota` - Cancelar captura de nota en progreso\n"
        "`/ocultar_menu` - Ocultar el teclado de botones\n\n"
        "Tip: Reenvia un mensaje de un usuario al bot para agregarlo como VIP."
    )
    await msg.reply_text(text, parse_mode="Markdown", reply_markup=kb)
