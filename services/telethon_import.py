"""Shared Telethon fetch helpers (no SQLite/queue/auth dependencies)."""
import asyncio
import logging
import os

from dotenv import load_dotenv

log = logging.getLogger("diana")

SESSION_NAME = "diana_session"
_FLOOD_WAIT_MAX_RETRIES = 5


def get_api_credentials() -> tuple[int, str]:
    """Lazy env load; raises RuntimeError (not SystemExit) for bot reuse."""
    load_dotenv()
    api_id = os.getenv("API_ID") or os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("API_HASH") or os.getenv("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise RuntimeError(
            "Faltan API_ID y API_HASH.\n"
            "Agregalos a tu .env (ejemplo):\n"
            "  API_ID=tu_api_id\n"
            "  API_HASH=tu_api_hash\n"
            "Obtenelos gratis en: https://my.telegram.org"
        )
    return int(api_id), api_hash


def messages_to_history(messages: list[dict]) -> list[dict]:
    """Raw Telethon message dicts → chat_history shape [{role, content}, ...]."""
    out: list[dict] = []
    for m in messages:
        text = (m.get("text") or "").strip()
        if not text:
            continue
        role = "assistant" if m.get("is_diana") else "user"
        out.append({"role": role, "content": text})
    return out


async def _message_to_record(msg, diana_id: int, entity) -> dict:
    from telethon.utils import get_display_name

    text = (
        msg.text
        or getattr(msg, "message", None)
        or getattr(msg, "caption", None)
        or ""
    ).strip()

    sender_name = "Unknown"
    try:
        sender = await msg.get_sender()
        if sender:
            sender_name = get_display_name(sender)
    except Exception:
        pass

    return {
        "id": msg.id,
        "date": msg.date.isoformat() if msg.date else None,
        "sender_id": msg.sender_id,
        "sender_name": sender_name,
        "text": text,
        "is_diana": bool(msg.out or (msg.sender_id == diana_id)),
        "has_media": bool(msg.media),
        "chat_id": getattr(entity, "id", None),
    }


async def fetch_all_messages(client, entity, limit: int | None) -> list[dict]:
    """Fetch messages in chronological order (oldest first).

    When limit is set, returns the NEWEST ``limit`` messages (LLM context window).
    Telethon iter_messages defaults to newest-first; we reverse before returning.
    """
    from telethon.errors import FloodWaitError

    me = await client.get_me()
    diana_id = me.id
    retries = 0
    batch_count = 0
    rate_limit_batch = 200

    while True:
        try:
            newest_first: list[dict] = []
            async for msg in client.iter_messages(entity, limit=limit):
                if msg is None:
                    continue
                newest_first.append(await _message_to_record(msg, diana_id, entity))
                batch_count += 1
                if batch_count % rate_limit_batch == 0:
                    await asyncio.sleep(0.5)
            newest_first.reverse()
            return newest_first
        except FloodWaitError as e:
            retries += 1
            if retries > _FLOOD_WAIT_MAX_RETRIES:
                log.warning(
                    "Demasiados flood waits (%s). Re-lanzando para reintento del worker.",
                    _FLOOD_WAIT_MAX_RETRIES,
                )
                raise
            log.warning(
                "Flood wait %ss — esperando (intento %s/%s)...",
                e.seconds,
                retries,
                _FLOOD_WAIT_MAX_RETRIES,
            )
            await asyncio.sleep(e.seconds)


def get_entity_name(entity) -> str:
    from telethon.tl.types import User, Chat, Channel
    from telethon.utils import get_display_name

    if isinstance(entity, (Chat, Channel)):
        return entity.title or str(entity.id)
    if isinstance(entity, User):
        return get_display_name(entity) or str(entity.id)
    return (
        getattr(entity, "title", None)
        or getattr(entity, "first_name", None)
        or str(getattr(entity, "id", "unknown"))
    )


async def fetch_vip_history(user_id: int, limit: int) -> tuple[list[dict], str]:
    """Connect → fetch → convert → disconnect. Propagates FloodWaitError."""
    from telethon import TelegramClient

    api_id, api_hash = get_api_credentials()
    client = TelegramClient(SESSION_NAME, api_id, api_hash)
    try:
        await client.start()
        entity = await client.get_entity(user_id)
        name = get_entity_name(entity)
        msgs = await fetch_all_messages(client, entity, limit)
        history = messages_to_history(msgs)
        return history, name
    finally:
        await client.disconnect()