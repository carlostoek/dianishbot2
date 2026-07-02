"""Autonomous-mode training feedback callbacks (t: prefix)."""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import DIANA_ADMIN_CHAT_ID
from state import awaiting_correction
from services.llm import failure_label
from services.training import update_rating
from services import sandbox

from .shared import _clear_awaiting_note_with_prompt_restore

log = logging.getLogger("diana")


async def notify_diana_llm_failure(
    bot, *, username: str, chat_id: int, context: list, failure,
):
    """Avisa a Diana cuando el LLM no pudo generar respuesta."""
    if not DIANA_ADMIN_CHAT_ID:
        return
    preview = "\n".join([
        f"{'[Usuario]' if m['role'] == 'user' else '[Bot]'} {m['content'][:80]}"
        for m in context[-4:]
    ])
    texto = (
        f"⚠️ El bot NO pudo responder a {username}\n"
        f"Causa: {failure_label(failure.reason)}\n"
        f"Intentos: {failure.attempts}\n"
    )
    if failure.detail:
        texto += f"Detalle: {failure.detail[:200]}\n"
    texto += f"\nContexto:\n{preview}\n\n"
    texto += "Puedes responder manualmente en el chat del usuario."
    try:
        await bot.send_message(chat_id=DIANA_ADMIN_CHAT_ID, text=texto)
        log.info(
            f"Diana notificada de fallo LLM: {username} ({chat_id}) — "
            f"{failure_label(failure.reason)}"
        )
    except Exception as e:
        log.error(f"notify_diana_llm_failure error: {e}")


async def notify_diana(
    bot, example_id: int, username: str, context: list,
    response: str, confidence: int, topic: str,
):
    """Envía a Diana la notificación con los botones de calificación."""
    if not DIANA_ADMIN_CHAT_ID:
        return
    preview = "\n".join([
        f"{'[Usuario]' if m['role'] == 'user' else '[Bot]'} {m['content'][:80]}"
        for m in context[-4:]
    ])
    texto = (
        f"Respuesta con confianza baja ({confidence}%)\n"
        f"Usuario: {username} | Tema: {topic}\n\n"
        f"Contexto:\n{preview}\n\n"
        f"Lo que respondio el bot:\n{response[:250]}"
    )
    teclado = InlineKeyboardMarkup([[
        InlineKeyboardButton("Perfecta", callback_data=f"t:good:{example_id}"),
        InlineKeyboardButton("Corregir", callback_data=f"t:fix:{example_id}"),
        InlineKeyboardButton("Mala", callback_data=f"t:bad:{example_id}"),
    ]])
    try:
        await bot.send_message(
            chat_id=DIANA_ADMIN_CHAT_ID,
            text=texto,
            reply_markup=teclado,
        )
        log.info(f"Diana notificada: ejemplo {example_id} (conf={confidence}%)")
    except Exception as e:
        log.error(f"notify_diana error: {e}")


async def handle_training_action(
    cq, context: ContextTypes.DEFAULT_TYPE, action: str, ex_id: int,
) -> None:
    """Maneja callbacks t: (good, bad, fix)."""
    import handlers.callbacks as cb

    if sandbox.is_synthetic_id(ex_id):
        await cq.edit_message_text("Sandbox — sin persistencia.")
        return
    if action == "good":
        await _clear_awaiting_note_with_prompt_restore(context.bot, cq.from_user.id)
        cb.update_rating(ex_id, "good")
        await cq.edit_message_text(f"Guardado como ejemplo positivo (ID {ex_id}).")
        log.info(f"Ejemplo {ex_id} → good")
    elif action == "bad":
        await _clear_awaiting_note_with_prompt_restore(context.bot, cq.from_user.id)
        cb.update_rating(ex_id, "bad")
        await cq.edit_message_text(f"Marcado como mala respuesta (ID {ex_id}).")
        log.info(f"Ejemplo {ex_id} → bad")
    elif action == "fix":
        await _clear_awaiting_note_with_prompt_restore(context.bot, cq.from_user.id)
        awaiting_correction[cq.from_user.id] = ex_id
        await cq.edit_message_text(
            f"Esperando tu corrección para el ejemplo {ex_id}.\n\n"
            "Escribe la respuesta ideal:"
        )