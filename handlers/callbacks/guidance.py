"""Gray-zone guidance consult callbacks (g: prefix)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import DIANA_ADMIN_CHAT_ID, GUIDANCE_TIMEOUT_HOURS
from state import (
    awaiting_correction,
    awaiting_guidance_answer,
    awaiting_note,
    history,
    pending_guidance,
    reply_gen,
    _save_runtime_state,
)
from services import knowledge, sandbox
from services.llm import get_diana_response

from .shared import _clear_awaiting_note_with_prompt_restore

log = logging.getLogger("diana")

EXPIRED_GUIDANCE_TEXT = "Esta consulta ya expiró o fue procesada."

# Timeout scanner interval (seconds). Independent of reengagement enable flag.
_GUIDANCE_TIMEOUT_SCAN_SEC = 300


def _format_guidance_text(
    *,
    guidance_id: int,
    pending: dict,
    context: list[dict],
) -> str:
    preview = "\n".join([
        f"{'[Usuario]' if m['role'] == 'user' else '[Bot]'} {m['content'][:120]}"
        for m in context[-6:]
    ])
    draft = pending.get("draft_response") or ""
    if len(draft) > 800:
        draft = draft[:800] + "…"
    header = ""
    if sandbox.is_active(pending["chat_id"]):
        prof = sandbox.get_profile(pending["chat_id"]) or "?"
        header = f"🧪 SANDBOX — perfil: {prof}\n\n"
    return header + (
        f"🧭 Necesito tu criterio (zona gris) #{guidance_id}\n\n"
        f"VIP: @{pending.get('username', '?')} ({pending['chat_id']})\n"
        f"Tema: {pending.get('topic') or '—'}\n\n"
        f"Pregunta:\n{pending.get('gap_question') or '—'}\n\n"
        f"Contexto (últimos mensajes):\n{preview or '—'}\n\n"
        f"Borrador tentativo (no enviado):\n\"{draft}\""
    )


def _build_guidance_keyboard(guidance_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Responder", callback_data=f"g:answer:{guidance_id}"),
        InlineKeyboardButton("Usar borrador", callback_data=f"g:use_draft:{guidance_id}"),
        InlineKeyboardButton("Yo me encargo", callback_data=f"g:skip:{guidance_id}"),
    ]])


async def notify_diana_guidance(
    bot,
    *,
    guidance_id: int,
    pending: dict,
    context: list[dict] | None = None,
) -> None:
    """DM Diana with gray-zone consult UI (no VIP I/O)."""
    if not DIANA_ADMIN_CHAT_ID:
        return
    ctx = context if context is not None else history.get(pending["chat_id"], [])
    texto = _format_guidance_text(
        guidance_id=guidance_id, pending=pending, context=ctx,
    )
    teclado = _build_guidance_keyboard(guidance_id)
    try:
        msg = await bot.send_message(
            chat_id=DIANA_ADMIN_CHAT_ID,
            text=texto,
            reply_markup=teclado,
        )
        if msg is not None and getattr(msg, "message_id", None) is not None:
            pending["notify_message_id"] = msg.message_id
            _save_runtime_state()
        log.info(
            f"Guidance notificada a Diana: #{guidance_id} "
            f"{pending.get('username')} ({pending['chat_id']})"
        )
    except Exception as e:
        log.error(f"notify_diana_guidance error: {e}")


def clear_awaiting_guidance_answer(admin_id: int) -> None:
    awaiting_guidance_answer.pop(admin_id, None)


async def _close_pending(
    guidance_id: int,
    *,
    status: str,
    diana_answer_raw: str | None = None,
    policy_id: int | None = None,
) -> dict | None:
    """Pop runtime pending and resolve DB row. Returns pending dict or None."""
    pending = pending_guidance.pop(guidance_id, None)
    for admin_id, gid in list(awaiting_guidance_answer.items()):
        if gid == guidance_id:
            awaiting_guidance_answer.pop(admin_id, None)
    if pending is not None:
        _save_runtime_state()
    chat_id = pending["chat_id"] if pending else 0
    if pending is None or sandbox.should_persist(chat_id):
        try:
            knowledge.resolve_guidance_request(
                guidance_id,
                status=status,
                diana_answer_raw=diana_answer_raw,
                policy_id=policy_id,
            )
        except Exception as e:
            log.debug(f"resolve_guidance_request({guidance_id}): {e}")
    return pending


async def enter_normal_draft_path(
    bot,
    *,
    chat_id: int,
    bc_id: str,
    username: str,
    gen: int,
    response: str,
    confidence: int,
    topic: str,
) -> int | None:
    """Shared save → approve | deliver path used by timer, use_draft, answer (WU2).

    Returns example_id or None on failure/stale.
    """
    from handlers.timer import enter_draft_pipeline

    return await enter_draft_pipeline(
        bot,
        chat_id=chat_id,
        bc_id=bc_id,
        username=username,
        gen=gen,
        response=response,
        confidence=confidence,
        topic=topic,
    )


async def handle_guidance_action(
    cq, context: ContextTypes.DEFAULT_TYPE, action: str, guidance_id: int,
) -> None:
    """Handle g:answer / g:use_draft / g:skip."""
    if guidance_id not in pending_guidance:
        await cq.answer()
        await cq.edit_message_text(EXPIRED_GUIDANCE_TEXT)
        return

    pending = pending_guidance[guidance_id]

    if action == "answer":
        await _clear_awaiting_note_with_prompt_restore(context.bot, cq.from_user.id)
        awaiting_correction.pop(cq.from_user.id, None)
        awaiting_guidance_answer[cq.from_user.id] = guidance_id
        await cq.answer()
        await cq.edit_message_text(
            f"✏️ Escribe tu criterio para la zona gris "
            f"(VIP @{pending.get('username', '?')}):\n\n"
            f"Pregunta: {pending.get('gap_question') or '—'}\n\n"
            f"Tu respuesta se destilará como política de tema y se regenerará "
            f"el borrador del VIP con esa doctrina."
        )
        return

    if action == "skip":
        await _clear_awaiting_note_with_prompt_restore(context.bot, cq.from_user.id)
        clear_awaiting_guidance_answer(cq.from_user.id)
        await _close_pending(guidance_id, status="skipped")
        await cq.answer("Consulta cerrada — te encargas vos")
        await cq.edit_message_text(
            f"Consulta #{guidance_id} cerrada. "
            f"Diana se encarga del VIP @{pending.get('username', '?')} manualmente. "
            f"No se envió nada."
        )
        log.info(f"Guidance {guidance_id} → skipped (manual)")
        return

    if action == "use_draft":
        await _clear_awaiting_note_with_prompt_restore(context.bot, cq.from_user.id)
        clear_awaiting_guidance_answer(cq.from_user.id)
        chat_id = pending["chat_id"]
        if reply_gen.get(chat_id) != pending.get("gen"):
            await _close_pending(guidance_id, status="skipped")
            await cq.answer()
            await cq.edit_message_text(
                f"Consulta #{guidance_id} cerrada: el VIP escribió de nuevo "
                f"(borrador obsoleto). No se envió nada."
            )
            return
        draft = pending.get("draft_response") or ""
        conf = int(pending.get("confidence") or 0)
        topic = pending.get("topic") or "general"
        await _close_pending(guidance_id, status="skipped")
        await cq.answer("Abriendo borrador…")
        ex_id = await enter_normal_draft_path(
            context.bot,
            chat_id=chat_id,
            bc_id=pending.get("bc_id") or "",
            username=pending.get("username") or "",
            gen=pending["gen"],
            response=draft,
            confidence=conf,
            topic=topic,
        )
        if ex_id is None:
            await cq.edit_message_text(
                f"No se pudo abrir el borrador de la consulta #{guidance_id}."
            )
        else:
            await cq.edit_message_text(
                f"Consulta #{guidance_id}: se usó el borrador tentativo. "
                f"Revisá el flujo normal de aprobación/envío."
            )
        log.info(f"Guidance {guidance_id} → use_draft (example={ex_id})")
        return

    await cq.answer()


async def _persist_policy_from_answer(
    *,
    pending: dict,
    diana_answer: str,
    chat_id: int,
) -> int | None:
    """Distill + create_policy. Returns policy_id or None if sandbox/skip."""
    if not sandbox.should_persist(chat_id):
        return None
    distilled = await knowledge.distill_guidance(
        gap_question=pending.get("gap_question") or "",
        diana_answer=diana_answer,
        context=history.get(chat_id, []),
        topic_hint=pending.get("topic") or "",
    )
    try:
        return knowledge.create_policy(
            topic=distilled.get("topic") or pending.get("topic") or "general",
            keywords=distilled.get("keywords") or [],
            policy_summary=distilled.get("policy_summary") or diana_answer[:500],
            example_response=distilled.get("example_response") or "",
            priority=distilled.get("priority"),
            source_question=pending.get("gap_question") or "",
            source_answer_raw=diana_answer,
        )
    except Exception as e:
        log.error(f"create_policy after distill failed: {e}")
        return None


async def handle_diana_guidance_answer(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """Capture free-text after g:answer → distill → policy → regen → draft path.

    Stale gen: still stores policy + answered status, no VIP send.
    """
    msg = update.message
    if not msg or not msg.text:
        return False
    admin_id = msg.from_user.id
    if admin_id not in awaiting_guidance_answer:
        return False

    stripped = msg.text.strip()
    if stripped.startswith("/"):
        return False

    guidance_id = awaiting_guidance_answer.pop(admin_id)
    pending = pending_guidance.get(guidance_id)
    if pending is None:
        await msg.reply_text(EXPIRED_GUIDANCE_TEXT)
        return True

    chat_id = pending["chat_id"]
    topic = pending.get("topic") or "general"
    gen = pending.get("gen", 0)

    policy_id = await _persist_policy_from_answer(
        pending=pending, diana_answer=stripped, chat_id=chat_id,
    )

    # Stale VIP generation: keep policy, do not send old draft
    if reply_gen.get(chat_id) != gen:
        await _close_pending(
            guidance_id,
            status="answered",
            diana_answer_raw=stripped,
            policy_id=policy_id,
        )
        await msg.reply_text(
            "Guardé tu criterio como política, pero el VIP ya escribió de nuevo — "
            "no se envía el borrador viejo. La doctrina se aplicará en el próximo turno."
        )
        log.info(f"Guidance {guidance_id} answered+policy; stale gen — no VIP send")
        return True

    await _close_pending(
        guidance_id,
        status="answered",
        diana_answer_raw=stripped,
        policy_id=policy_id,
    )

    # Regen with policies now injectable via get_diana_response
    response, confidence, regen_topic, _kg, _gq, failure = await get_diana_response(
        chat_id,
        should_abort=lambda: reply_gen.get(chat_id) != gen,
    )
    if not response:
        # Fall back to stored tentative draft so VIP is not left frozen
        response = pending.get("draft_response") or ""
        confidence = int(pending.get("confidence") or 0)
        regen_topic = topic
        log.warning(
            f"Guidance {guidance_id} regen failed ({failure}); using stored draft"
        )

    if reply_gen.get(chat_id) != gen:
        await msg.reply_text(
            "Política guardada, pero el VIP escribió de nuevo durante la regeneración — "
            "no se envía el borrador."
        )
        return True

    ex_id = await enter_normal_draft_path(
        context.bot,
        chat_id=chat_id,
        bc_id=pending.get("bc_id") or "",
        username=pending.get("username") or "",
        gen=gen,
        response=response,
        confidence=confidence,
        topic=regen_topic or topic,
    )
    if ex_id is None:
        await msg.reply_text(
            "Criterio y política guardados, pero no se pudo abrir el borrador para el VIP."
        )
    else:
        degraded = ""
        await msg.reply_text(
            f"✓ Criterio guardado como política"
            f"{f' #{policy_id}' if policy_id else ''}"
            f" (consulta #{guidance_id}). "
            f"Borrador regenerado y abierto por el camino normal."
            f"{degraded}"
        )
    log.info(
        f"Guidance {guidance_id} answered → policy={policy_id} regen example={ex_id}"
    )
    return True


def open_guidance_consult(
    *,
    chat_id: int,
    bc_id: str,
    username: str,
    gen: int,
    topic: str,
    gap_question: str,
    draft_response: str,
    confidence: int,
    context: list | None = None,
) -> int:
    """Create DB request + runtime pending_guidance entry. Returns guidance id."""
    gid = knowledge.create_guidance_request(
        chat_id=chat_id,
        username=username,
        topic=topic,
        gap_question=gap_question,
        context=context,
        draft_response=draft_response,
    )
    pending_guidance[gid] = {
        "chat_id": chat_id,
        "bc_id": bc_id,
        "username": username,
        "gen": gen,
        "topic": topic,
        "gap_question": gap_question,
        "draft_response": draft_response,
        "confidence": confidence,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_runtime_state()
    return gid


async def supersede_guidance_for_chat(chat_id: int) -> int:
    """Owner inbound: mark all open guidances for chat as superseded. Returns count."""
    closed = 0
    for gid, pending in list(pending_guidance.items()):
        if pending.get("chat_id") != chat_id:
            continue
        await _close_pending(gid, status="superseded")
        closed += 1
        log.info(f"Guidance {gid} superseded (owner inbound chat {chat_id})")
    return closed


def _parse_created_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


async def process_guidance_timeouts(bot) -> int:
    """Close open guidances older than GUIDANCE_TIMEOUT_HOURS via use_draft path.

    Returns number of timed-out requests processed. VIP freeze holds until this runs.
    """
    cutoff = datetime.now() - timedelta(hours=GUIDANCE_TIMEOUT_HOURS)
    timed_out = 0
    for gid, pending in list(pending_guidance.items()):
        created = _parse_created_at(pending.get("created_at"))
        if created is None or created > cutoff:
            continue

        chat_id = pending["chat_id"]
        gen = pending.get("gen", 0)
        draft = pending.get("draft_response") or ""
        conf = int(pending.get("confidence") or 0)
        topic = pending.get("topic") or "general"
        username = pending.get("username") or ""

        await _close_pending(gid, status="timeout")
        timed_out += 1

        # Stale gen: close only, no VIP send
        if reply_gen.get(chat_id) != gen:
            log.info(f"Guidance {gid} timeout but gen stale — no VIP send")
            if DIANA_ADMIN_CHAT_ID:
                try:
                    await bot.send_message(
                        chat_id=DIANA_ADMIN_CHAT_ID,
                        text=(
                            f"⏱ Timeout consulta #{gid} (@{username}): "
                            f"el VIP ya escribió de nuevo — no se abrió el borrador viejo."
                        ),
                    )
                except Exception as e:
                    log.debug(f"timeout stale notify: {e}")
            continue

        ex_id = await enter_normal_draft_path(
            bot,
            chat_id=chat_id,
            bc_id=pending.get("bc_id") or "",
            username=username,
            gen=gen,
            response=draft,
            confidence=conf,
            topic=topic,
        )
        if DIANA_ADMIN_CHAT_ID:
            try:
                await bot.send_message(
                    chat_id=DIANA_ADMIN_CHAT_ID,
                    text=(
                        f"⏱ Timeout ({GUIDANCE_TIMEOUT_HOURS}h) consulta #{gid} "
                        f"(@{username}). Se abrió el borrador tentativo por el "
                        f"camino normal"
                        f"{f' (ejemplo {ex_id})' if ex_id else ''}."
                    ),
                )
            except Exception as e:
                log.error(f"timeout notify error: {e}")
        log.info(f"Guidance {gid} → timeout (example={ex_id})")

    return timed_out


async def _timeout_scheduler_loop(app) -> None:
    while True:
        try:
            await process_guidance_timeouts(app.bot)
        except Exception as e:
            log.error(f"Guidance timeout scanner error: {e}")
        await asyncio.sleep(float(_GUIDANCE_TIMEOUT_SCAN_SEC))


def start_timeout_scheduler(app) -> None:
    """Start background guidance timeout scanner."""
    asyncio.create_task(_timeout_scheduler_loop(app))
    log.info(
        "Guidance timeout scheduler started (interval=%ss, hours=%s)",
        _GUIDANCE_TIMEOUT_SCAN_SEC,
        GUIDANCE_TIMEOUT_HOURS,
    )
