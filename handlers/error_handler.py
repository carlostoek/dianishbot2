import logging
import traceback
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("diana")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Captura toda excepción no manejada. Registrado vía app.add_error_handler().

    PTB hardener pattern (references/python-telegram-bot-patterns.md).
    Garantiza que:
    - Errores en handlers no maten el polling loop.
    - El usuario reciba feedback amistoso cuando sea posible.
    - Logging completo para debugging (incluye update_id y user).
    """
    user_id = update.effective_user.id if update and update.effective_user else None
    update_id = update.update_id if update else None

    logger.error(
        "Unhandled exception | user_id=%s | update_id=%s | error=%s\n%s",
        user_id,
        update_id,
        context.error,
        traceback.format_exc(),
    )

    # Intenta notificar al usuario sin revelar detalles internos.
    # Varias rutas porque el update puede ser business_message, callback, etc.
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Ocurrió un error procesando tu mensaje. Intenta de nuevo en un momento."
            )
        elif update and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Ocurrió un error procesando tu mensaje. Intenta de nuevo en un momento.",
            )
        elif update and update.callback_query:
            await update.callback_query.answer(
                "⚠️ Error al procesar. Intenta de nuevo.", show_alert=True
            )
    except Exception:
        # Si ni siquiera podemos responder, al menos no rompemos más.
        pass
