"""Escalation triage callbacks (e: prefix)."""

import logging

from telegram.ext import ContextTypes

from config import DIANA_ADMIN_CHAT_ID
from state import (
    chat_write_lock,
    history,
    pending_approval,
    pending_escalations,
    reply_gen,
    _save_runtime_state,
)
from services.llm import FAIL_ABORTED, failure_label
from services import sandbox

from .shared import (
    _build_escalation_keyboard,
    _clear_awaiting_note_with_prompt_restore,
    _format_escalation_text,
    _refresh_escalation_message,
)


log = logging.getLogger("diana")


async def notify_diana_escalation(
    bot,
    *,
    esc_id: int,
    user_id: int,
    username: str,
    chat_id: int,
    reason: str,
    trigger_text: str,
    context: list[dict],
    pending: dict,
):
    """Alerta a Diana cuando un VIP necesita atención personal."""
    if not DIANA_ADMIN_CHAT_ID:
        return
    pending["user_id"] = user_id
    texto = _format_escalation_text(
        esc_id=esc_id,
        user_id=user_id,
        username=username,
        chat_id=chat_id,
        reason=reason,
        trigger_text=trigger_text,
        context=context,
        pending=pending,
    )
    teclado = _build_escalation_keyboard(esc_id, pending)
    try:
        await bot.send_message(
            chat_id=DIANA_ADMIN_CHAT_ID,
            text=texto,
            reply_markup=teclado,
        )
        log.info(
            f"Escalación notificada a Diana: #{esc_id} {username} ({user_id}) — {reason}"
        )
    except Exception as e:
        log.error(f"notify_diana_escalation error: {e}")


async def _generate_from_escalation(bot, esc_id: int) -> str | None:
    """Genera borrador tras FP. Devuelve mensaje de error o None si OK."""
    import handlers.callbacks as cb

    if esc_id not in pending_escalations:
        return "expired"
    pending = pending_escalations[esc_id]
    if pending.get("verdict") != "false_positive":
        return "not_fp"

    chat_id = pending["chat_id"]
    reply_gen[chat_id] = reply_gen.get(chat_id, 0) + 1
    gen = reply_gen[chat_id]
    pending["gen"] = gen
    _save_runtime_state()

    response, confidence, topic, _knowledge_gap, _gap_question, failure = await cb.get_diana_response(
        chat_id,
        no_escalation=True,
        should_abort=lambda: reply_gen.get(chat_id) != gen,
    )
    if not response:
        if failure and failure.reason == FAIL_ABORTED:
            return "stale"
        detail = failure_label(failure.reason) if failure else "sin respuesta"
        return f"llm_fail:{detail}"

    from services import data_pause

    if data_pause.uses_synthetic_examples(chat_id):
        example_id = sandbox.allocate_draft_id()
    else:
        example_id = cb.save_example(
            chat_id, pending["username"], history.get(chat_id, []),
            response, confidence, topic,
        )

    async with chat_write_lock(chat_id):
        pending_approval[example_id] = {
            "chat_id": chat_id,
            "bc_id": pending["bc_id"],
            "username": pending["username"],
            "gen": gen,
            "variants": [{"response": response, "confidence": confidence, "topic": topic}],
            "selected": 0,
            "regenerating": False,
        }
        _save_runtime_state()
    await cb.notify_diana_approval(
        bot, example_id, pending["username"], history.get(chat_id, []),
        response, confidence, topic,
        chat_id=chat_id, gen=gen,
    )
    pending_escalations.pop(esc_id, None)
    _save_runtime_state()
    return None


async def handle_escalation_action(
    cq, context: ContextTypes.DEFAULT_TYPE, action: str, esc_id: int,
) -> None:
    """Maneja callbacks e: (valid, fp, gen)."""
    import handlers.callbacks as cb

    if esc_id not in pending_escalations:
        await cq.answer()
        await cq.edit_message_text("Esta escalación ya expiró o fue procesada.")
        return
    pending = pending_escalations[esc_id]

    if action == "valid":
        await _clear_awaiting_note_with_prompt_restore(context.bot, cq.from_user.id)
        pending["verdict"] = "valid"
        if sandbox.should_persist(pending["chat_id"]) and not sandbox.is_synthetic_id(esc_id):
            cb.review_escalation(esc_id, "valid")
        pending_escalations.pop(esc_id, None)
        _save_runtime_state()
        await cq.answer("Registrada")
        await _refresh_escalation_message(
            cq, esc_id, pending, history.get(pending["chat_id"], []),
        )
        log.info(f"Escalación {esc_id} → valid")

    elif action == "fp":
        await _clear_awaiting_note_with_prompt_restore(context.bot, cq.from_user.id)
        pending["verdict"] = "false_positive"
        if sandbox.should_persist(pending["chat_id"]) and not sandbox.is_synthetic_id(esc_id):
            cb.review_escalation(esc_id, "false_positive")
        _save_runtime_state()
        await cq.answer("Falso positivo registrado")
        await _refresh_escalation_message(
            cq, esc_id, pending, history.get(pending["chat_id"], []),
        )
        log.info(f"Escalación {esc_id} → false_positive")

    elif action == "gen":
        if pending.get("verdict") != "false_positive":
            await cq.answer("Marca primero como falso positivo")
            return
        await cq.answer("Generando...")
        err = await _generate_from_escalation(context.bot, esc_id)
        if err == "expired":
            await cq.edit_message_text("Esta escalación ya expiró o fue procesada.")
        elif err == "not_fp":
            await cq.answer("Marca primero como falso positivo")
        elif err == "stale":
            await cq.edit_message_text(
                "No se generó: el chat tiene un mensaje más reciente.",
            )
        elif err and err.startswith("llm_fail:"):
            await cq.edit_message_text(
                f"No se pudo generar respuesta: {err.split(':', 1)[1]}",
            )
        else:
            await cq.edit_message_text(
                f"Generando borrador para {pending['username']}... "
                "Revisa el mensaje de aprobación.",
                reply_markup=None,
            )
            log.info(f"Escalación {esc_id} → borrador generado")