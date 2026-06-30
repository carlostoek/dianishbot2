import logging
from telegram import Update
from telegram.ext import Application, ContextTypes
import auth_users
from config import DIANA_ADMIN_CHAT_ID
from state import _load_connections_state, _save_connections_state, connections
import state
from .business import _handle_business_message
from .recovery import recover_runtime_on_startup
from .callbacks import handle_callback, handle_diana_correction, handle_diana_note
from .admin_menu import handle_admin_input
log = logging.getLogger("diana")


async def process_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Router principal — despacha según tipo de update."""

    if update.callback_query:
        if await handle_callback(update, context):
            return
        if await auth_users.handle_callback(update, context):
            return

    if (
        update.message
        and not update.business_message
        and update.message.chat.id == DIANA_ADMIN_CHAT_ID
    ):
        if await auth_users.handle_admin_note(update, context):
            return
        if await handle_diana_note(update, context):
            return
        if await handle_diana_correction(update, context):
            return

    if update.message and not update.business_message:
        admin_id = auth_users.get_admin_id()
        if admin_id and update.message.from_user.id == admin_id:
            # Admin slash commands (/start, /menu, /estado, /ayuda)
            if await handle_admin_input(update, context):
                return
            # Legacy slash commands (/usuarios, /notas, /fallos, etc.)
            if await auth_users.handle_admin_message(update, context):
                return
        elif update.message.from_user:
            sender = update.message.from_user.id
            text = update.message.text or update.message.caption or ""
            log.info(
                f"Mensaje directo al bot ignorado | user:{sender} "
                f"auth:{auth_users.is_authorized(sender)} text:{text[:60]}"
            )
            return

    # ── Conexión activada / desactivada por Diana ────
    if update.business_connection:
        conn = update.business_connection
        if conn.is_enabled:
            connections[conn.id] = conn.user.id
            state.diana_user_id = conn.user.id
            auth_users.set_admin_id(conn.user.id)
            _save_connections_state()
            log.info(f"Conexión activa: {conn.id} | Diana ID: {conn.user.id}")
        else:
            connections.pop(conn.id, None)
            _save_connections_state()
            log.info(f"Conexión desactivada: {conn.id}")
        return

    if update.business_message:
        await _handle_business_message(update.business_message, context)
        return

    if update.edited_business_message:
        await _handle_business_message(
            update.edited_business_message, context, edited=True,
        )
        return


async def _post_init(app: Application) -> None:
    _load_connections_state()
    await recover_runtime_on_startup(app.bot)
