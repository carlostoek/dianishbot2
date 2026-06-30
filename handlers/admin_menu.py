"""Admin slash commands for Diana bot.

Routes /start, /menu, /estado and /ayuda to the inline menu system in auth_users.py.
"""

import logging

from telegram import ReplyKeyboardRemove, Update
from telegram.ext import ContextTypes

import auth_users

log = logging.getLogger("diana")

_SLASH_COMMANDS = frozenset({"/start", "/menu", "/estado", "/ayuda"})


async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Route admin slash commands before the legacy handler in auth_users."""
    msg = update.message
    if not msg or not msg.text:
        return False

    cmd = msg.text.strip().split()[0]
    if cmd not in _SLASH_COMMANDS:
        return False

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

    return False


async def _send_start(msg) -> None:
    """Welcome message with inline main menu."""
    text = (
        "Hola, Diana. El bot esta activo.\n\n"
        "Usa el menu inline para navegacion o escribe / para ver comandos."
    )
    await msg.reply_text(text, reply_markup=ReplyKeyboardRemove())
    await auth_users.send_main_menu(msg.get_bot(), msg.chat_id)