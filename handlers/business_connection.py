"""Handler for Telegram Business connection enable/disable updates."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

import auth_users
from state import _save_connections_state, connections
import state

log = logging.getLogger("diana")


async def handle_business_connection(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Registra conexión Business activada o desactivada por Diana."""
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