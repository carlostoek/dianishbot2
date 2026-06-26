import asyncio
import logging
from datetime import datetime
from telegram.ext import ContextTypes
from config import ESCALATE_FILE, ESCALATE_KEYWORDS, OBSERVE_UNAUTHORIZED
import auth_users
from state import (
    connections, history, chat_bc, chat_meta, pending_msg, reply_gen, timers,
    _save_connections_state,
)
from services.training import save_observed_example
from .callbacks import notify_diana_escalation
from .timer import auto_reply
log = logging.getLogger("diana")


def log_escalation(user_id: int, username: str, reason: str, context: list[dict]):
    with open(ESCALATE_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{'═' * 50}\n")
        f.write(f"ESCALACIÓN — {datetime.now().strftime('%d/%m/%Y %H:%M')}\n")
        f.write(f"Usuario: {username} (ID: {user_id})\n")
        f.write(f"Motivo: {reason}\n")
        f.write("Últimos mensajes:\n")
        for msg in context[-6:]:
            role = "Él" if msg["role"] == "user" else "Diana (auto)"
            f.write(f"  {role}: {msg['content']}\n")


def _resolve_sender_id(msg) -> int | None:
    if msg.from_user:
        return msg.from_user.id
    if msg.chat and msg.chat.type == "private":
        return msg.chat.id
    return None


def _resolve_vip_id(msg) -> int | None:
    sender_id = _resolve_sender_id(msg)
    if sender_id and auth_users.is_authorized(sender_id, msg.chat.id):
        return sender_id
    if auth_users.is_authorized(None, msg.chat.id):
        return msg.chat.id
    return sender_id


def needs_escalation(text: str) -> str | None:
    lower = text.lower()
    for kw in ESCALATE_KEYWORDS:
        if kw in lower:
            return f"Keyword detectada: '{kw}'"
    return None


async def _handle_business_message(
    msg,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    edited: bool = False,
):
    bc_id     = msg.business_connection_id
    chat_id   = msg.chat.id
    text      = msg.text or msg.caption or ""
    sender_id = _resolve_sender_id(msg)
    vip_id    = _resolve_vip_id(msg)
    username  = (
        (msg.from_user.username or msg.from_user.first_name)
        if msg.from_user else str(chat_id)
    )

    owner_id = connections.get(bc_id)
    if not owner_id and bc_id:
        try:
            conn = await context.bot.get_business_connection(bc_id)
            if conn.is_enabled:
                connections[bc_id] = conn.user.id
                _save_connections_state()
                owner_id = conn.user.id
                log.info(f"Conexión resuelta via API: {bc_id}")
        except Exception as e:
            log.debug(f"get_business_connection({bc_id}): {e}")

    if owner_id and sender_id == owner_id:
        if edited:
            return
        log.info(f"Diana retomó con {chat_id}: {text[:60]}")
        prior = history.get(chat_id, [])
        history.setdefault(chat_id, []).append({"role": "assistant", "content": text})
        if chat_id in timers:
            timers.pop(chat_id).cancel()
            log.info(f"Timer cancelado para {chat_id}")
        if OBSERVE_UNAUTHORIZED and text.strip():
            meta = chat_meta.get(chat_id, {})
            vip = meta.get("vip_id")
            if vip and not auth_users.is_authorized(vip, chat_id):
                ex_id = save_observed_example(
                    chat_id, meta.get("username", str(chat_id)), prior, text,
                )
                if ex_id:
                    log.info(
                        f"Ejemplo observado {ex_id} — Diana respondió en chat "
                        f"no autorizado ({meta.get('username', chat_id)})"
                    )
        return

    authorized = bool(vip_id and auth_users.is_authorized(vip_id, chat_id))

    if not authorized:
        if OBSERVE_UNAUTHORIZED and text.strip() and not edited:
            log.info(f"OBSERVADO {username}: {text[:100]}")
            history.setdefault(chat_id, []).append({"role": "user", "content": text})
            chat_bc[chat_id] = bc_id
            if vip_id:
                chat_meta[chat_id] = {"vip_id": vip_id, "username": username}
        else:
            log.info(
                f"Mensaje ignorado — no autorizado | sender:{sender_id} "
                f"chat:{chat_id} vip:{vip_id} edited:{edited}"
            )
        return

    if edited:
        log.info(f"Edición ignorada de {username} ({vip_id})")
        return

    log.info(f"ENTRADA {username}: {text[:100]}")

    history.setdefault(chat_id, []).append({"role": "user", "content": text})
    chat_bc[chat_id] = bc_id
    pending_msg[chat_id] = msg.message_id
    reason = needs_escalation(text)
    if reason:
        log_escalation(vip_id, username, reason, history[chat_id])
        log.info(f"ESCALADO {username} — {reason}")
        if chat_id in timers:
            timers.pop(chat_id).cancel()
        await notify_diana_escalation(
            context.bot,
            user_id=vip_id,
            username=username,
            chat_id=chat_id,
            reason=reason,
            trigger_text=text,
            context=history[chat_id],
        )
        return

    if chat_id in timers:
        timers.pop(chat_id).cancel()

    reply_gen[chat_id] = reply_gen.get(chat_id, 0) + 1
    gen = reply_gen[chat_id]
    task = asyncio.create_task(
        auto_reply(context.bot, chat_id, username, bc_id, gen)
    )
    timers[chat_id] = task
