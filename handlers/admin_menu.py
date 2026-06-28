"""Admin menu system for Diana bot.

Provides a persistent ReplyKeyboardMarkup-based menu and new admin commands
(/start, /menu, /estado, /ayuda, /ocultar_menu). Integrates with the inline
menu system in auth_users.py for structured navigation.

The ReplyKeyboardMarkup acts as a quick-access convenience overlay.
The primary navigation uses InlineKeyboardMarkup callbacks (au: prefix).
"""

import logging

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import ContextTypes

import auth_users

log = logging.getLogger("diana")

# ── Keyboard layout ────────────────────────────────────────

_MAIN_BUTTONS = [
    ["📋 Menu", "👥 Usuarios"],
    ["📊 Estado", "📈 Fallos"],
    ["🤖 LLM"],
    ["❓ Ayuda"],
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
    "Menu": "menu",
    "Usuarios": "usuarios",
    "Estado": "estado",
    "Fallos": "fallos",
    "LLM": "llm",
    "Ayuda": "ayuda",
}

# Strip leading emoji (and optional space) to look up plain label.
# e.g. "👥 Usuarios" -> "Usuarios",  "📊 Estado" -> "Estado"
def _strip_emoji(text: str) -> str:
    for i, ch in enumerate(text):
        if ch.isalnum() or ch in "/":
            return text[i:].strip()
    return text.strip()


_SLASH_COMMANDS = frozenset({
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
    if cmd in _SLASH_COMMANDS:
        return await _route_slash_command(cmd, msg, context)

    return False


# ── Internal routing ───────────────────────────────────────

async def _route_menu_action(action: str, msg, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if action == "menu":
        await auth_users.send_main_menu(context.bot, msg.chat_id)
        return True

    if action == "usuarios":
        await auth_users.send_user_list(context.bot, msg.chat_id)
        return True

    if action == "estado":
        await auth_users.send_estado(context.bot, msg.chat_id)
        return True

    if action == "llm":
        await auth_users.send_llm_menu(context.bot, msg.chat_id)
        return True

    if action == "fallos":
        from services.training import format_llm_failure_report
        report = format_llm_failure_report(days=7)
        await msg.reply_text(report, reply_markup=auth_users._build_back_to_menu_keyboard())
        return True

    if action == "ayuda":
        await auth_users.send_main_menu(context.bot, msg.chat_id)
        return True

    return False


async def _route_slash_command(cmd: str, msg, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if cmd == "/start":
        await _send_start(msg)
        return True

    if cmd == "/menu":
        await auth_users.send_main_menu(context.bot, msg.chat_id)
        return True

    if cmd == "/estado":
        text = auth_users.build_estado_text()
        await msg.reply_text(
            text, parse_mode="Markdown",
            reply_markup=auth_users._build_back_to_menu_keyboard(),
        )
        return True

    if cmd == "/ayuda":
        await auth_users.send_main_menu(context.bot, msg.chat_id)
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
    """Welcome message with menu keyboard (first interaction).

    Sends both the ReplyKeyboardMarkup (persistent) and the inline main menu.
    """
    text = (
        "Hola, Diana. El bot esta activo.\n\n"
        "Usa los botones de abajo para acceso rapido "
        "o el menu inline para navegacion completa.\n"
        "Tambien podes escribir / para ver todos los comandos."
    )
    await msg.reply_text(text, reply_markup=build_main_keyboard())
    await auth_users.send_main_menu(msg.get_bot(), msg.chat_id)
