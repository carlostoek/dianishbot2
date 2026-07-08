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
    from services.message_content import history_content_from_record

    out: list[dict] = []
    for m in messages:
        content = history_content_from_record(m)
        if not content:
            continue
        role = "assistant" if m.get("is_diana") else "user"
        out.append({"role": role, "content": content})
    return out


async def _message_to_record(msg, diana_id: int, entity) -> dict:
    from telethon.utils import get_display_name
    from services.message_content import telethon_media_kind

    text = (
        msg.text
        or getattr(msg, "message", None)
        or getattr(msg, "caption", None)
        or ""
    ).strip()
    media_kind = telethon_media_kind(msg) if msg.media else None

    sender_name = "Unknown"
    try:
        sender = await msg.get_sender()
        if sender:
            sender_name = get_display_name(sender)
    except Exception:
        log.debug("get_sender falló, usando Unknown", exc_info=True)

    return {
        "id": msg.id,
        "date": msg.date.isoformat() if msg.date else None,
        "sender_id": msg.sender_id,
        "sender_name": sender_name,
        "text": text,
        "is_diana": bool(msg.out or (msg.sender_id == diana_id)),
        "has_media": bool(msg.media),
        "media_kind": media_kind,
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


async def resolve_vip_entity(
    client,
    user_id: int,
    *,
    username: str | None = None,
) -> object:
    """Resolve a VIP user entity with cache-miss fallbacks.

    Telethon raises ValueError when the session cache lacks access_hash for a
    bare user_id. Fall back to @username and dialog scan before giving up.
    """
    errors: list[str] = []

    for spec in (user_id, f"@{username}" if username else None):
        if spec is None:
            continue
        try:
            return await client.get_entity(spec)
        except Exception as e:
            errors.append(f"{spec}: {type(e).__name__}: {e}")

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if getattr(entity, "id", None) == user_id:
            return entity

    detail = "; ".join(errors) if errors else "sin intentos previos"
    raise ValueError(
        f"Could not resolve VIP entity user_id={user_id} "
        f"(username={username!r}). {detail}"
    )


async def fetch_vip_history(
    user_id: int,
    limit: int,
    *,
    username: str | None = None,
) -> tuple[list[dict], str]:
    """Connect → fetch → convert → disconnect. Propagates FloodWaitError."""
    from telethon import TelegramClient

    api_id, api_hash = get_api_credentials()
    client = TelegramClient(SESSION_NAME, api_id, api_hash)
    try:
        await client.start()
        entity = await resolve_vip_entity(client, user_id, username=username)
        name = get_entity_name(entity)
        msgs = await fetch_all_messages(client, entity, limit)
        history = messages_to_history(msgs)
        return history, name
    finally:
        await client.disconnect()