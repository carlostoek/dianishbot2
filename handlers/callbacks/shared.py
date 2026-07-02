"""Shared helpers for approval, training, and escalation callbacks."""

import logging
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from state import (
    awaiting_note,
    history,
    pending_approval,
    reply_gen,
)
from services import sandbox

MAX_APPROVAL_VARIANTS = 10
EXPIRED_DRAFT_TEXT = "Este borrador ya expiró o fue procesado."

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


async def _safe_cq_answer(
    cq, text: str | None = None, *, show_alert: bool = False,
) -> bool:
    """Answer callback query; return False if Telegram already expired it."""
    try:
        if show_alert:
            await cq.answer(text, show_alert=True)
        elif text is not None:
            await cq.answer(text)
        else:
            await cq.answer()
        return True
    except BadRequest as e:
        msg = str(e).lower()
        if "query is too old" in msg or "query id is invalid" in msg:
            log.warning("Callback query expired before answer: %s", e)
            return False
        raise


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
    header = ""
    if sandbox.is_active(pending["chat_id"]):
        prof = sandbox.get_profile(pending["chat_id"]) or "?"
        header = f"🧪 SANDBOX — perfil: {prof}\n\n"
    return header + (
        f"Borrador {k}/{n} para {username} "
        f"(conf {variant['confidence']}% | tema: {variant['topic']})"
        f"{stale_line}{failure_line}\n\n"
        f"Contexto:\n{preview}\n\n"
        f"Respuesta propuesta:\n{response_display}"
    )


def _build_approval_keyboard(example_id: int, chat_id: int) -> InlineKeyboardMarkup:
    row1 = [
        InlineKeyboardButton("Enviar tal cual", callback_data=f"a:approve:{example_id}"),
        InlineKeyboardButton("Corregir antes", callback_data=f"a:fix:{example_id}"),
    ]
    if not sandbox.is_active(chat_id):
        row1.append(
            InlineKeyboardButton("📝 Nota", callback_data=f"a:note:{example_id}"),
        )
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
    teclado = _build_approval_keyboard(ex_id, pending["chat_id"])
    return texto, teclado


def _note_ctx_has_draft_coords(note_ctx: dict) -> bool:
    return (
        note_ctx.get("draft_chat_id") is not None
        and note_ctx.get("draft_message_id") is not None
    )


async def _edit_draft_message_expired(
    bot, draft_chat_id: int, draft_message_id: int,
) -> bool:
    try:
        await bot.edit_message_text(
            EXPIRED_DRAFT_TEXT,
            chat_id=draft_chat_id,
            message_id=draft_message_id,
            reply_markup=None,
        )
        return True
    except Exception as e:
        log.error(
            f"Error marcando borrador expirado (chat {draft_chat_id}, "
            f"msg {draft_message_id}): {e}"
        )
        return False


async def _restore_or_clear_note_prompt(bot, note_ctx: dict) -> None:
    """Restore approval UI or mark expired on a draft left in note-prompt state."""
    if not _note_ctx_has_draft_coords(note_ctx):
        return
    ex_id = note_ctx.get("example_id")
    draft_chat_id = note_ctx["draft_chat_id"]
    draft_message_id = note_ctx["draft_message_id"]
    draft_still_pending = ex_id is not None and ex_id in pending_approval
    if draft_still_pending:
        pending = pending_approval[ex_id]
        if not pending.get("regenerating"):
            chat_context = history.get(pending["chat_id"], [])
            try:
                await _edit_approval_message(
                    bot, draft_chat_id, draft_message_id,
                    pending, ex_id, chat_context,
                )
            except Exception as e:
                log.error(
                    f"Error restaurando borrador tras nota cruzada ejemplo {ex_id}: {e}"
                )
    else:
        await _edit_draft_message_expired(bot, draft_chat_id, draft_message_id)


async def _clear_awaiting_note_with_prompt_restore(bot, user_id: int) -> None:
    note_ctx = awaiting_note.get(user_id)
    if note_ctx:
        await _restore_or_clear_note_prompt(bot, note_ctx)
    awaiting_note.pop(user_id, None)


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


def _format_escalation_text(
    *,
    esc_id: int,
    user_id: int,
    username: str,
    chat_id: int,
    reason: str,
    trigger_text: str,
    context: list[dict],
    pending: dict,
) -> str:
    preview = "\n".join([
        f"{'[Usuario]' if m['role'] == 'user' else '[Bot]'} {m['content'][:120]}"
        for m in context[-6:]
    ])
    footer = "Elige cómo calificar esta escalación."
    if pending.get("verdict") == "valid":
        footer = "Escalación confirmada. Responde manualmente en Business."
    elif pending.get("verdict") == "false_positive":
        footer = (
            "Marcada como falso positivo. El patrón quedó registrado.\n"
            "Pulsa Generar respuesta si quieres que el bot conteste."
        )
    return (
        f"⚠️ ESCALACIÓN #{esc_id} — atención personal requerida\n\n"
        f"Usuario: {username}\n"
        f"ID: {user_id} | Chat: {chat_id}\n"
        f"Motivo: {reason}\n\n"
        f"Mensaje que disparó la alerta:\n{trigger_text[:300]}\n\n"
        f"Contexto reciente:\n{preview}\n\n"
        f"{footer}"
    )


def _build_escalation_keyboard(esc_id: int, pending: dict) -> InlineKeyboardMarkup | None:
    verdict = pending.get("verdict")
    if verdict == "valid":
        return None
    if verdict == "false_positive":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "Generar respuesta", callback_data=f"e:gen:{esc_id}",
            ),
        ]])
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "Escalación correcta", callback_data=f"e:valid:{esc_id}",
        ),
        InlineKeyboardButton(
            "Falso positivo", callback_data=f"e:fp:{esc_id}",
        ),
    ]])


async def _refresh_escalation_message(cq, esc_id: int, pending: dict, context: list) -> None:
    texto = _format_escalation_text(
        esc_id=esc_id,
        user_id=pending.get("user_id", pending["chat_id"]),
        username=pending["username"],
        chat_id=pending["chat_id"],
        reason=pending["reason"],
        trigger_text=pending["trigger_text"],
        context=context,
        pending=pending,
    )
    teclado = _build_escalation_keyboard(esc_id, pending)
    await cq.edit_message_text(texto, reply_markup=teclado)