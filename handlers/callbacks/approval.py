"""Supervised approval callbacks (a: prefix) and Diana correction/note flows."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import DIANA_ADMIN_CHAT_ID
from state import (
    awaiting_correction,
    awaiting_note,
    chat_write_lock,
    history,
    pending_approval,
    reply_gen,
    _save_runtime_state,
)
from services.llm import FAIL_ABORTED, failure_label
from services.memory import schedule_memory_extract
from services import sandbox

from .shared import (
    EXPIRED_DRAFT_TEXT,
    MAX_APPROVAL_VARIANTS,
    RegenResult,
    _approval_message_parts,
    _build_approval_keyboard,
    _clear_awaiting_note_with_prompt_restore,
    _edit_approval_message,
    _edit_draft_message_expired,
    _format_approval_text,
    _refresh_approval_message,
    _safe_cq_answer,
    _selected_variant,
)

log = logging.getLogger("diana")


def _regen_blocked_reason(ex_id: int) -> str | None:
    if ex_id not in pending_approval:
        return "expired"
    pending = pending_approval[ex_id]
    if pending.get("regenerating"):
        return "regenerating"
    if reply_gen.get(pending["chat_id"]) != pending["gen"]:
        return "stale"
    if len(pending["variants"]) >= MAX_APPROVAL_VARIANTS:
        return "max_variants"
    return None


async def _regen_approval_variant(ex_id: int) -> RegenResult:
    import handlers.callbacks as cb

    blocked = _regen_blocked_reason(ex_id)
    if blocked:
        return RegenResult(blocked_reason=blocked)

    pending = pending_approval[ex_id]
    async with chat_write_lock(pending["chat_id"]):
        pending["regenerating"] = True
    response = confidence = topic = failure = None
    regen_error = False
    try:
        response, confidence, topic, _knowledge_gap, _gap_question, failure = await cb.get_diana_response(
            pending["chat_id"],
            should_abort=lambda: reply_gen.get(pending["chat_id"]) != pending["gen"],
        )
    except Exception as e:
        log.error(f"Regen error ejemplo {ex_id}: {e}")
        regen_error = True
    finally:
        async with chat_write_lock(pending["chat_id"]):
            if ex_id in pending_approval:
                pending_approval[ex_id]["regenerating"] = False

    if ex_id not in pending_approval:
        return RegenResult(blocked_reason="expired")

    pending = pending_approval[ex_id]
    if regen_error:
        return RegenResult(failure_note="⚠️ Regeneración falló: error inesperado")
    if reply_gen.get(pending["chat_id"]) != pending["gen"]:
        failure_note = (
            "⚠️ Regeneración cancelada: el chat se actualizó mientras generaba."
        )
        log.warning(f"Regen abortado post-LLM ejemplo {ex_id}: gen desactualizado")
        return RegenResult(failure_note=failure_note)
    if not response:
        if failure and failure.reason == FAIL_ABORTED:
            failure_note = (
                "⚠️ Regeneración cancelada: llegó un mensaje nuevo en el chat."
            )
            log.warning(f"Regen abortado ejemplo {ex_id}: {failure_label(FAIL_ABORTED)}")
        elif failure:
            failure_note = f"⚠️ Regeneración falló: {failure_label(failure.reason)}"
            log.warning(
                f"Regen fallido ejemplo {ex_id}: {failure_label(failure.reason)}"
            )
        else:
            failure_note = "⚠️ Regeneración falló: sin respuesta"
            log.warning(f"Regen fallido ejemplo {ex_id}: sin respuesta")
        return RegenResult(failure_note=failure_note)

    async with chat_write_lock(pending["chat_id"]):
        pending["variants"].append({
            "response": response,
            "confidence": confidence,
            "topic": topic,
        })
        pending["selected"] = len(pending["variants"]) - 1
        _save_runtime_state()
    return RegenResult(appended=True)


async def notify_diana_approval(
    bot, example_id: int, username: str, context: list,
    response: str, confidence: int, topic: str,
    *, chat_id: int, gen: int,
):
    """Envía el borrador a Diana ANTES de mandarlo al usuario."""
    if not DIANA_ADMIN_CHAT_ID:
        return
    pending = {
        "chat_id": chat_id,
        "username": username,
        "gen": gen,
        "variants": [{"response": response, "confidence": confidence, "topic": topic}],
        "selected": 0,
        "regenerating": False,
    }
    texto = _format_approval_text(username, context, pending)
    teclado = _build_approval_keyboard(example_id, chat_id)
    try:
        await bot.send_message(
            chat_id=DIANA_ADMIN_CHAT_ID,
            text=texto,
            reply_markup=teclado,
        )
        log.info(f"Borrador enviado a Diana: ejemplo {example_id} ({username})")
    except Exception as e:
        log.error(f"notify_diana_approval error: {e}")


async def handle_approval_action(
    cq, context: ContextTypes.DEFAULT_TYPE, action: str, ex_id: int,
) -> None:
    """Maneja callbacks a: (approve, fix, note, regen, prev, next)."""
    import handlers.callbacks as cb

    if action == "approve":
        await _clear_awaiting_note_with_prompt_restore(context.bot, cq.from_user.id)
        if ex_id not in pending_approval:
            await cq.answer()
            await cq.edit_message_text("Este borrador ya expiró o fue procesado.")
            return
        pending = pending_approval[ex_id]
        if pending.get("regenerating"):
            await cq.answer("Espera a que termine la regeneración")
            return
        if reply_gen.get(pending["chat_id"]) != pending["gen"]:
            await cq.answer("Chat actualizado — borrador obsoleto")
            return
        variant = _selected_variant(pending)
        text = variant["response"]
        await cq.answer("Enviando...")
        await cq.edit_message_text(
            f"Enviando a {pending['username']}...",
            reply_markup=None,
        )
        ok = await cb.deliver_vip_response(
            context.bot,
            chat_id=pending["chat_id"],
            bc_id=pending["bc_id"],
            username=pending["username"],
            gen=pending["gen"],
            text=text,
        )
        if ok:
            async with chat_write_lock(pending["chat_id"]):
                pending_approval.pop(ex_id)
                _save_runtime_state()
            if sandbox.should_persist(pending["chat_id"]):
                if pending["selected"] != 0:
                    cb.update_bot_response(ex_id, text)
                cb.update_rating(ex_id, "good")
                cb.schedule_memory_extract(
                    cb.llm_mod.memory_service,
                    pending["chat_id"],
                    history.get(pending["chat_id"], []),
                    cb.llm_mod.raw_call,
                )
            await cq.edit_message_text(f"Enviado a {pending['username']}.")
            log.info(f"Aprobado y enviado: ejemplo {ex_id} → {pending['username']}")
        else:
            await _refresh_approval_message(
                cq, pending, ex_id, history.get(pending["chat_id"], []),
                failure_note="⚠️ No enviado: el chat tiene un mensaje más reciente.",
            )
            log.warning(f"Aprobación {ex_id} obsoleta — gen desactualizado")

    elif action == "note":
        if ex_id not in pending_approval:
            await cq.answer()
            await cq.edit_message_text("Este borrador ya expiró o fue procesado.")
            return
        pending = pending_approval[ex_id]
        if sandbox.is_active(pending["chat_id"]):
            await cq.answer("Nota deshabilitada en sandbox")
            return
        if pending.get("regenerating"):
            await cq.answer("Espera a que termine la regeneración")
            return
        if cq.from_user.id in awaiting_note:
            await cq.answer(
                "Ya estás escribiendo una nota. Termina o usa /cancelar_nota."
            )
            return
        awaiting_correction.pop(cq.from_user.id, None)
        awaiting_note[cq.from_user.id] = {
            "user_id": pending["chat_id"],
            "username": pending["username"],
            "example_id": ex_id,
            "draft_chat_id": cq.message.chat_id,
            "draft_message_id": cq.message.message_id,
        }
        stale_hint = ""
        if reply_gen.get(pending["chat_id"]) != pending["gen"]:
            stale_hint = (
                "\n\n⚠️ El chat tiene un mensaje más reciente — tras guardar la nota, "
                "el borrador puede quedar obsoleto."
            )
        await cq.answer()
        await cq.edit_message_text(
            f"✏️ Escribe tu nota para {pending['username']}:\n\n"
            f"Se guardará en su perfil y se usará en todas las respuestas futuras.\n"
            f"Escribe /cancelar_nota para cancelar (el borrador sigue pendiente)."
            f"{stale_hint}"
        )

    elif action == "fix":
        if ex_id not in pending_approval:
            await cq.answer()
            await cq.edit_message_text("Este borrador ya expiró o fue procesado.")
            return
        pending = pending_approval[ex_id]
        if pending.get("regenerating"):
            await cq.answer("Espera a que termine la regeneración")
            return
        await _clear_awaiting_note_with_prompt_restore(context.bot, cq.from_user.id)
        awaiting_correction[cq.from_user.id] = ex_id
        variant = _selected_variant(pending)
        await cq.answer()
        await cq.edit_message_text(
            f"Escribe la respuesta corregida para {pending['username']}:\n\n"
            f"Borrador actual:\n{variant['response'][:200]}"
        )

    elif action == "regen":
        import handlers.callbacks as cb

        blocked = _regen_blocked_reason(ex_id)
        if blocked == "expired":
            await _safe_cq_answer(cq)
            await cq.edit_message_text(EXPIRED_DRAFT_TEXT)
            return
        if blocked == "regenerating":
            await _safe_cq_answer(cq, "Ya generando...")
            return
        if blocked == "stale":
            await _safe_cq_answer(cq, "Chat actualizado — borrador obsoleto")
            return
        if blocked == "max_variants":
            await _safe_cq_answer(cq, "Máximo de variantes alcanzado")
            return
        await _safe_cq_answer(cq, "Generando...")
        result = await cb._regen_approval_variant(ex_id)
        if result.blocked_reason == "expired" or ex_id not in pending_approval:
            await cq.edit_message_text(EXPIRED_DRAFT_TEXT)
            return
        pending = pending_approval[ex_id]
        chat_context = history.get(pending["chat_id"], [])
        await _refresh_approval_message(
            cq, pending, ex_id, chat_context, failure_note=result.failure_note,
        )
        if result.appended:
            k = pending["selected"] + 1
            n = len(pending["variants"])
            log.info(f"Regenerado borrador ejemplo {ex_id} → variante {k}/{n}")

    elif action == "prev":
        if ex_id not in pending_approval:
            await cq.answer()
            await cq.edit_message_text("Este borrador ya expiró o fue procesado.")
            return
        pending = pending_approval[ex_id]
        if pending.get("regenerating"):
            await cq.answer("Espera a que termine la regeneración")
            return
        if pending["selected"] > 0:
            async with chat_write_lock(pending["chat_id"]):
                pending["selected"] -= 1
                _save_runtime_state()
            await cq.answer()
            await _refresh_approval_message(
                cq, pending, ex_id, history.get(pending["chat_id"], []),
            )
        else:
            await cq.answer("Primera opción")

    elif action == "next":
        if ex_id not in pending_approval:
            await cq.answer()
            await cq.edit_message_text("Este borrador ya expiró o fue procesado.")
            return
        pending = pending_approval[ex_id]
        if pending.get("regenerating"):
            await cq.answer("Espera a que termine la regeneración")
            return
        last = len(pending["variants"]) - 1
        if pending["selected"] < last:
            async with chat_write_lock(pending["chat_id"]):
                pending["selected"] += 1
                _save_runtime_state()
            await cq.answer()
            await _refresh_approval_message(
                cq, pending, ex_id, history.get(pending["chat_id"], []),
            )
        else:
            await cq.answer("Última opción")


async def handle_diana_correction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Captura correcciones de Diana — envía al usuario (aprobación) o solo guarda (autónomo)."""
    import handlers.callbacks as cb

    msg = update.message
    if not msg or not msg.text:
        return False
    if msg.from_user.id not in awaiting_correction:
        return False

    stripped = msg.text.strip()
    if stripped.startswith("/"):
        return False

    ex_id = awaiting_correction[msg.from_user.id]
    if ex_id in pending_approval and pending_approval[ex_id].get("regenerating"):
        await msg.reply_text("Espera a que termine la regeneración del borrador.")
        return True

    ex_id = awaiting_correction.pop(msg.from_user.id)
    correction = stripped

    if ex_id in pending_approval:
        pending = pending_approval[ex_id]
        ok = await cb.deliver_vip_response(
            context.bot,
            chat_id=pending["chat_id"],
            bc_id=pending["bc_id"],
            username=pending["username"],
            gen=pending["gen"],
            text=correction,
        )
        if ok:
            async with chat_write_lock(pending["chat_id"]):
                pending_approval.pop(ex_id)
                _save_runtime_state()
            if sandbox.should_persist(pending["chat_id"]):
                cb.update_rating(ex_id, "corrected", correction)
                cb.schedule_memory_extract(
                    cb.llm_mod.memory_service,
                    pending["chat_id"],
                    history.get(pending["chat_id"], []),
                    cb.llm_mod.raw_call,
                )
                confirm = (
                    f"Correccion enviada a {pending['username']} "
                    "y guardada como ejemplo de entrenamiento."
                )
            else:
                confirm = (
                    f"Correccion enviada a {pending['username']} "
                    "(sandbox — sin persistencia)."
                )
            await msg.reply_text(confirm)
            log.info(f"Corrección enviada (aprobación): ejemplo {ex_id} → {pending['username']}")
        else:
            await msg.reply_text(
                f"Corrección no enviada a {pending['username']}: "
                "el chat tiene un mensaje más reciente. El borrador sigue pendiente."
            )
            log.warning(f"Corrección {ex_id} obsoleta — gen desactualizado")
    else:
        if sandbox.is_synthetic_id(ex_id):
            await msg.reply_text("Sandbox — sin persistencia.")
        else:
            cb.update_rating(ex_id, "corrected", correction)
            await msg.reply_text(
                f"Corrección guardada (ejemplo {ex_id}). Se usará en respuestas futuras."
            )
            log.info(f"Corrección guardada (autónomo): ejemplo {ex_id} → '{correction[:60]}'")

    return True


async def handle_diana_note(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Captura la nota que Diana escribe tras pulsar el botón 📝 Nota."""
    import handlers.callbacks as cb

    msg = update.message
    if not msg or not msg.text:
        return False
    if msg.from_user.id not in awaiting_note:
        return False

    stripped = msg.text.strip()
    if stripped.startswith("/"):
        base_cmd = stripped.split()[0].split("@")[0]
        if base_cmd == "/cancelar_nota":
            note_ctx = awaiting_note[msg.from_user.id]
            ex_id = note_ctx.get("example_id")
            draft_chat_id = note_ctx.get("draft_chat_id")
            draft_message_id = note_ctx.get("draft_message_id")
            draft_still_pending = ex_id is not None and ex_id in pending_approval
            has_coords = (
                draft_chat_id is not None and draft_message_id is not None
            )
            regenerating = False
            if has_coords:
                if draft_still_pending:
                    pending = pending_approval[ex_id]
                    regenerating = pending.get("regenerating", False)
                    if not regenerating:
                        chat_context = history.get(pending["chat_id"], [])
                        try:
                            await _edit_approval_message(
                                context.bot, draft_chat_id, draft_message_id,
                                pending, ex_id, chat_context,
                            )
                        except Exception as e:
                            log.error(
                                f"Error restaurando borrador al cancelar nota "
                                f"ejemplo {ex_id}: {e}"
                            )
                else:
                    await _edit_draft_message_expired(
                        context.bot, draft_chat_id, draft_message_id,
                    )
            awaiting_note.pop(msg.from_user.id)
            ex_ref = note_ctx.get("example_id")
            ex_line = f" (ejemplo {ex_ref})" if ex_ref is not None else ""
            if ex_id is not None and not draft_still_pending:
                await msg.reply_text(
                    f"Nota cancelada. El borrador{ex_line} para {note_ctx['username']} "
                    "ya expiró o fue procesado."
                )
            elif draft_still_pending and regenerating:
                await msg.reply_text(
                    f"Nota cancelada. Espera a que termine la regeneración del borrador "
                    f"para {note_ctx['username']}{ex_line}."
                )
            else:
                await msg.reply_text(
                    f"Nota cancelada. El borrador para {note_ctx['username']}{ex_line} "
                    "sigue pendiente."
                )
            return True
        return False

    note_ctx = awaiting_note[msg.from_user.id]
    if sandbox.is_active(note_ctx["user_id"]):
        awaiting_note.pop(msg.from_user.id, None)
        await msg.reply_text("Nota deshabilitada en sandbox.")
        return True

    if not cb.llm_mod.memory_service:
        await msg.reply_text("Memoria no disponible.")
        return True

    try:
        saved = cb.llm_mod.memory_service.add_note(
            note_ctx["user_id"], stripped,
        )
    except Exception as e:
        log.error(
            f"Error guardando nota manual | usuario {note_ctx['user_id']}: {e}"
        )
        await msg.reply_text(
            "Error al guardar la nota. Intenta de nuevo o /cancelar_nota."
        )
        return True

    if not saved:
        await msg.reply_text(
            "La nota está vacía o no es válida. Escribe de nuevo o /cancelar_nota."
        )
        return True

    awaiting_note.pop(msg.from_user.id)

    recovery_copy = (
        f"✓ Nota guardada para {note_ctx['username']}.\n"
        "Hubo un error al actualizar el borrador. "
        "Revisa los borradores pendientes o pulsa 🔄 Regenerar."
    )
    try:
        ex_id = note_ctx.get("example_id")
        draft_chat_id = note_ctx.get("draft_chat_id")
        draft_message_id = note_ctx.get("draft_message_id")
        has_draft_coords = (
            ex_id is not None
            and draft_chat_id is not None
            and draft_message_id is not None
        )
        expired_copy = (
            f"✓ Nota guardada para {note_ctx['username']}.\n"
            "El borrador ya no está pendiente; la nota se aplica en la próxima respuesta."
        )

        if has_draft_coords and ex_id not in pending_approval:
            await _edit_draft_message_expired(
                context.bot, draft_chat_id, draft_message_id,
            )
            await msg.reply_text(expired_copy)
        elif has_draft_coords:
            result = await cb._regen_approval_variant(ex_id)
            if result.blocked_reason == "expired" or ex_id not in pending_approval:
                await _edit_draft_message_expired(
                    context.bot, draft_chat_id, draft_message_id,
                )
                await msg.reply_text(expired_copy)
            else:
                pending = pending_approval[ex_id]
                chat_context = history.get(pending["chat_id"], [])
                edit_success = False
                try:
                    await _edit_approval_message(
                        context.bot, draft_chat_id, draft_message_id,
                        pending, ex_id, chat_context, failure_note=result.failure_note,
                    )
                    edit_success = True
                except Exception as e:
                    log.error(f"Error restaurando borrador tras nota ejemplo {ex_id}: {e}")

                base = f"✓ Nota guardada para {note_ctx['username']}."
                if result.appended:
                    k = pending["selected"] + 1
                    n = len(pending["variants"])
                    if edit_success:
                        detail = f"\nBorrador regenerado (variante {k}/{n})."
                        log.info(
                            f"Nota + regen borrador ejemplo {ex_id} → variante {k}/{n}"
                        )
                    else:
                        detail = (
                            "\nNo se pudo actualizar el mensaje del borrador. "
                            "Pulsa 🔄 Regenerar en el borrador pendiente para ver "
                            "la nueva variante."
                        )
                elif result.blocked_reason == "max_variants":
                    detail = (
                        "\nMáximo de variantes alcanzado — borrador restaurado "
                        "sin regenerar."
                    )
                elif result.blocked_reason == "stale":
                    detail = (
                        "\nBorrador restaurado sin regenerar — el chat tiene un mensaje "
                        "más reciente."
                    )
                elif result.blocked_reason == "regenerating":
                    detail = (
                        "\nBorrador restaurado — ya había una regeneración en curso. "
                        "Pulsa 🔄 Regenerar cuando termine para aplicar la nota."
                    )
                elif result.failure_note:
                    detail = (
                        "\nNo se pudo regenerar el borrador; revisa el mensaje "
                        "del borrador."
                    )
                else:
                    detail = ""
                await msg.reply_text(base + detail)
        else:
            await msg.reply_text(
                f"✓ Nota guardada para {note_ctx['username']}.\n"
                "Se aplica a partir de la próxima respuesta."
            )
    except Exception as e:
        log.error(
            f"Error post-guardado nota | usuario {note_ctx['user_id']}: {e}"
        )
        await msg.reply_text(recovery_copy)

    log.info(
        f"Nota manual guardada | usuario {note_ctx['user_id']} "
        f"({note_ctx['username']}): {stripped[:60]}"
    )
    return True