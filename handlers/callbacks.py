import logging
from dataclasses import dataclass
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from config import DIANA_ADMIN_CHAT_ID
from state import (
    awaiting_correction, awaiting_note, history, pending_approval, reply_gen,
)
from services.delivery import deliver_vip_response
from services.training import update_rating, update_bot_response
from services.memory import schedule_memory_extract
from services import llm as llm_mod
from services.llm import FAIL_ABORTED, failure_label, get_diana_response

MAX_APPROVAL_VARIANTS = 10

log = logging.getLogger("diana")


@dataclass
class RegenResult:
    appended: bool = False
    failure_note: str | None = None
    blocked_reason: str | None = None  # "regenerating" | "stale" | "max_variants" | "expired"


def _clamp_selected(pending: dict) -> int:
    last = len(pending["variants"]) - 1
    return max(0, min(pending["selected"], last))


def _selected_variant(pending: dict) -> dict:
    return pending["variants"][_clamp_selected(pending)]


def _format_approval_text(
    username: str,
    context: list,
    pending: dict,
    *,
    failure_note: str | None = None,
) -> str:
    variant = _selected_variant(pending)
    k = _clamp_selected(pending) + 1
    n = len(pending["variants"])
    preview = "\n".join([
        f"{'[Usuario]' if m['role'] == 'user' else '[Bot]'} {m['content'][:80]}"
        for m in context[-4:]
    ])
    response_display = variant["response"]
    max_response_chars = 2800
    if len(response_display) > max_response_chars:
        response_display = response_display[:max_response_chars] + "…"
    stale = reply_gen.get(pending["chat_id"]) != pending["gen"]
    stale_line = (
        "\n⚠️ Chat tiene mensaje más reciente — borrador puede estar obsoleto.\n"
        if stale else ""
    )
    failure_line = f"\n{failure_note}\n" if failure_note else ""
    return (
        f"Borrador {k}/{n} para {username} "
        f"(conf {variant['confidence']}% | tema: {variant['topic']})"
        f"{stale_line}{failure_line}\n\n"
        f"Contexto:\n{preview}\n\n"
        f"Respuesta propuesta:\n{response_display}"
    )


def _build_approval_keyboard(example_id: int) -> InlineKeyboardMarkup:
    row1 = [
        InlineKeyboardButton("Enviar tal cual", callback_data=f"a:approve:{example_id}"),
        InlineKeyboardButton("Corregir antes", callback_data=f"a:fix:{example_id}"),
        InlineKeyboardButton("📝 Nota", callback_data=f"a:note:{example_id}"),
    ]
    row2 = [
        InlineKeyboardButton("◀ Anterior", callback_data=f"a:prev:{example_id}"),
        InlineKeyboardButton("🔄 Regenerar", callback_data=f"a:regen:{example_id}"),
        InlineKeyboardButton("Siguiente ▶", callback_data=f"a:next:{example_id}"),
    ]
    return InlineKeyboardMarkup([row1, row2])


def _approval_message_parts(
    pending: dict, ex_id: int, context: list, *, failure_note: str | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    texto = _format_approval_text(
        pending["username"], context, pending, failure_note=failure_note,
    )
    teclado = _build_approval_keyboard(ex_id)
    return texto, teclado


async def _edit_approval_message(
    bot, chat_id: int, message_id: int,
    pending: dict, ex_id: int, context: list, *, failure_note: str | None = None,
) -> None:
    texto, teclado = _approval_message_parts(
        pending, ex_id, context, failure_note=failure_note,
    )
    await bot.edit_message_text(
        texto, chat_id=chat_id, message_id=message_id, reply_markup=teclado,
    )


async def _refresh_approval_message(
    cq, pending: dict, ex_id: int, context: list, *, failure_note: str | None = None,
) -> None:
    texto, teclado = _approval_message_parts(
        pending, ex_id, context, failure_note=failure_note,
    )
    await cq.edit_message_text(texto, reply_markup=teclado)


async def _regen_approval_variant(ex_id: int) -> RegenResult:
    if ex_id not in pending_approval:
        return RegenResult(blocked_reason="expired")
    pending = pending_approval[ex_id]
    if pending.get("regenerating"):
        return RegenResult(blocked_reason="regenerating")
    if reply_gen.get(pending["chat_id"]) != pending["gen"]:
        return RegenResult(blocked_reason="stale")
    if len(pending["variants"]) >= MAX_APPROVAL_VARIANTS:
        return RegenResult(blocked_reason="max_variants")

    pending["regenerating"] = True
    response = confidence = topic = failure = None
    regen_error = False
    try:
        response, confidence, topic, failure = await get_diana_response(
            pending["chat_id"],
            should_abort=lambda: reply_gen.get(pending["chat_id"]) != pending["gen"],
        )
    except Exception as e:
        log.error(f"Regen error ejemplo {ex_id}: {e}")
        regen_error = True
    finally:
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

    pending["variants"].append({
        "response": response,
        "confidence": confidence,
        "topic": topic,
    })
    pending["selected"] = len(pending["variants"]) - 1
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
    teclado = _build_approval_keyboard(example_id)
    try:
        await bot.send_message(
            chat_id=DIANA_ADMIN_CHAT_ID,
            text=texto,
            reply_markup=teclado,
        )
        log.info(f"Borrador enviado a Diana: ejemplo {example_id} ({username})")
    except Exception as e:
        log.error(f"notify_diana_approval error: {e}")


async def notify_diana_escalation(
    bot,
    *,
    user_id: int,
    username: str,
    chat_id: int,
    reason: str,
    trigger_text: str,
    context: list[dict],
):
    """Alerta a Diana cuando un VIP necesita atención personal."""
    if not DIANA_ADMIN_CHAT_ID:
        return
    preview = "\n".join([
        f"{'[Usuario]' if m['role'] == 'user' else '[Bot]'} {m['content'][:120]}"
        for m in context[-6:]
    ])
    texto = (
        "⚠️ ESCALACIÓN — atención personal requerida\n\n"
        f"Usuario: {username}\n"
        f"ID: {user_id} | Chat: {chat_id}\n"
        f"Motivo: {reason}\n\n"
        f"Mensaje que disparó la alerta:\n{trigger_text[:300]}\n\n"
        f"Contexto reciente:\n{preview}\n\n"
        "El bot no respondió. Responde tú desde Business."
    )
    try:
        await bot.send_message(
            chat_id=DIANA_ADMIN_CHAT_ID,
            text=texto,
        )
        log.info(f"Escalación notificada a Diana: {username} ({user_id}) — {reason}")
    except Exception as e:
        log.error(f"notify_diana_escalation error: {e}")


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


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Maneja callbacks de aprobación (a:) y retroalimentación post-envío (t:)."""
    cq = update.callback_query
    if not cq or not cq.data:
        return False

    parts = cq.data.split(":")
    if len(parts) != 3:
        return False

    prefix, action = parts[0], parts[1]
    try:
        ex_id = int(parts[2])
    except ValueError:
        return False
    if prefix not in ("a", "t"):
        return False

    if not (prefix == "a" and action in ("approve", "regen", "prev", "next", "fix", "note")):
        await cq.answer()

    # ══ MODO APROBACIÓN (a:) ═══════════════════════════════════════
    if prefix == "a":
        if action == "approve":
            awaiting_note.pop(cq.from_user.id, None)
            if ex_id not in pending_approval:
                await cq.answer()
                await cq.edit_message_text("Este borrador ya expiró o fue procesado.")
                return True
            pending = pending_approval[ex_id]
            if pending.get("regenerating"):
                await cq.answer("Espera a que termine la regeneración")
                return True
            if reply_gen.get(pending["chat_id"]) != pending["gen"]:
                await cq.answer("Chat actualizado — borrador obsoleto")
                return True
            variant = _selected_variant(pending)
            text = variant["response"]
            await cq.answer("Enviando...")
            await cq.edit_message_text(
                f"Enviando a {pending['username']}...",
                reply_markup=None,
            )
            ok = await deliver_vip_response(
                context.bot,
                chat_id=pending["chat_id"],
                bc_id=pending["bc_id"],
                username=pending["username"],
                gen=pending["gen"],
                text=text,
            )
            if ok:
                pending_approval.pop(ex_id)
                if pending["selected"] != 0:
                    update_bot_response(ex_id, text)
                update_rating(ex_id, "good")
                schedule_memory_extract(
                    llm_mod.memory_service,
                    pending["chat_id"],
                    history.get(pending["chat_id"], []),
                    llm_mod.raw_call,
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
                return True
            pending = pending_approval[ex_id]
            if pending.get("regenerating"):
                await cq.answer("Espera a que termine la regeneración")
                return True
            awaiting_correction.pop(cq.from_user.id, None)
            awaiting_note[cq.from_user.id] = {
                "user_id": pending["chat_id"],
                "username": pending["username"],
                "example_id": ex_id,
            }
            await cq.answer()
            await cq.edit_message_text(
                f"✏️ Escribe tu nota para {pending['username']}:\n\n"
                f"Se guardará en su perfil y se usará en todas las respuestas futuras.\n"
                f"Escribe /cancelar_nota para cancelar (el borrador sigue pendiente)."
            )

        elif action == "fix":
            if ex_id not in pending_approval:
                await cq.answer()
                await cq.edit_message_text("Este borrador ya expiró o fue procesado.")
                return True
            pending = pending_approval[ex_id]
            if pending.get("regenerating"):
                await cq.answer("Espera a que termine la regeneración")
                return True
            awaiting_note.pop(cq.from_user.id, None)
            awaiting_correction[cq.from_user.id] = ex_id
            variant = _selected_variant(pending)
            await cq.answer()
            await cq.edit_message_text(
                f"Escribe la respuesta corregida para {pending['username']}:\n\n"
                f"Borrador actual:\n{variant['response'][:200]}"
            )

        elif action == "regen":
            result = await _regen_approval_variant(ex_id)
            if result.blocked_reason == "expired":
                await cq.answer()
                await cq.edit_message_text("Este borrador ya expiró o fue procesado.")
                return True
            if result.blocked_reason == "regenerating":
                await cq.answer("Ya generando...")
                return True
            if result.blocked_reason == "stale":
                await cq.answer("Chat actualizado — borrador obsoleto")
                return True
            if result.blocked_reason == "max_variants":
                await cq.answer("Máximo de variantes alcanzado")
                return True
            await cq.answer("Generando...")
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
                return True
            pending = pending_approval[ex_id]
            if pending["selected"] > 0:
                pending["selected"] -= 1
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
                return True
            pending = pending_approval[ex_id]
            last = len(pending["variants"]) - 1
            if pending["selected"] < last:
                pending["selected"] += 1
                await cq.answer()
                await _refresh_approval_message(
                    cq, pending, ex_id, history.get(pending["chat_id"], []),
                )
            else:
                await cq.answer("Última opción")

    # ══ MODO AUTÓNOMO — retroalimentación post-envío (t:) ══════════
    elif prefix == "t":
        if action == "good":
            awaiting_note.pop(cq.from_user.id, None)
            update_rating(ex_id, "good")
            await cq.edit_message_text(f"Guardado como ejemplo positivo (ID {ex_id}).")
            log.info(f"Ejemplo {ex_id} → good")
        elif action == "bad":
            awaiting_note.pop(cq.from_user.id, None)
            update_rating(ex_id, "bad")
            await cq.edit_message_text(f"Marcado como mala respuesta (ID {ex_id}).")
            log.info(f"Ejemplo {ex_id} → bad")
        elif action == "fix":
            awaiting_note.pop(cq.from_user.id, None)
            awaiting_correction[cq.from_user.id] = ex_id
            await cq.edit_message_text(
                f"Esperando tu corrección para el ejemplo {ex_id}.\n\n"
                "Escribe la respuesta ideal:"
            )

    return True


async def handle_diana_correction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Captura correcciones de Diana — envía al usuario (aprobación) o solo guarda (autónomo)."""
    msg = update.message
    if not msg or not msg.text:
        return False
    if msg.from_user.id not in awaiting_correction:
        return False

    stripped = msg.text.strip()
    if stripped.startswith("/"):
        return False

    ex_id = awaiting_correction.pop(msg.from_user.id)
    correction = stripped

    if ex_id in pending_approval:
        pending = pending_approval[ex_id]
        ok = await deliver_vip_response(
            context.bot,
            chat_id=pending["chat_id"],
            bc_id=pending["bc_id"],
            username=pending["username"],
            gen=pending["gen"],
            text=correction,
        )
        if ok:
            pending_approval.pop(ex_id)
            update_rating(ex_id, "corrected", correction)
            schedule_memory_extract(
                llm_mod.memory_service,
                pending["chat_id"],
                history.get(pending["chat_id"], []),
                llm_mod.raw_call,
            )
            await msg.reply_text(
                f"Correccion enviada a {pending['username']} y guardada como ejemplo de entrenamiento."
            )
            log.info(f"Corrección enviada (aprobación): ejemplo {ex_id} → {pending['username']}")
        else:
            await msg.reply_text(
                f"Corrección no enviada a {pending['username']}: "
                "el chat tiene un mensaje más reciente. El borrador sigue pendiente."
            )
            log.warning(f"Corrección {ex_id} obsoleta — gen desactualizado")
    else:
        update_rating(ex_id, "corrected", correction)
        await msg.reply_text(
            f"Corrección guardada (ejemplo {ex_id}). Se usará en respuestas futuras."
        )
        log.info(f"Corrección guardada (autónomo): ejemplo {ex_id} → '{correction[:60]}'")

    return True


async def handle_diana_note(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Captura la nota que Diana escribe tras pulsar el botón 📝 Nota."""
    msg = update.message
    if not msg or not msg.text:
        return False
    if msg.from_user.id not in awaiting_note:
        return False

    stripped = msg.text.strip()
    if stripped.startswith("/"):
        base_cmd = stripped.split()[0].split("@")[0]
        if base_cmd == "/cancelar_nota":
            note_ctx = awaiting_note.pop(msg.from_user.id)
            ex_ref = note_ctx.get("example_id")
            ex_line = f" (ejemplo {ex_ref})" if ex_ref is not None else ""
            await msg.reply_text(
                f"Nota cancelada. El borrador para {note_ctx['username']}{ex_line} "
                "sigue pendiente."
            )
            return True
        return False

    note_ctx = awaiting_note[msg.from_user.id]
    if not llm_mod.memory_service:
        await msg.reply_text("Memoria no disponible.")
        return True

    try:
        saved = llm_mod.memory_service.add_note(
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
    await msg.reply_text(
        f"✓ Nota guardada para {note_ctx['username']}.\n"
        f"Se aplica a partir de la próxima respuesta."
    )
    log.info(
        f"Nota manual guardada | usuario {note_ctx['user_id']} "
        f"({note_ctx['username']}): {stripped[:60]}"
    )
    return True
