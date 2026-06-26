import asyncio
import random
import aiohttp
import logging

from config import BOT_TOKEN
from state import history, pending_msg, reply_gen

log = logging.getLogger("diana")


async def mark_as_read(bot, bc_id: str, chat_id: int, message_id: int):
    """
    Marca el mensaje como leído → aparecen las dos palomitas azules.
    Usa Bot API 9.0 readBusinessMessage via HTTP directo.
    """
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/readBusinessMessage"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={
                "business_connection_id": bc_id,
                "chat_id":               chat_id,
                "message_id":            message_id,
            }) as resp:
                data = await resp.json()
                if data.get("ok"):
                    log.info(f"✓ leído msg {message_id} en chat {chat_id}")
                else:
                    log.warning(f"readBusinessMessage: {data.get('description')}")
    except Exception as e:
        log.error(f"mark_as_read error: {e}")


async def simulate_typing(bot, chat_id: int, bc_id: str, text: str):
    """
    Muestra 'escribiendo…' bajo el nombre de Diana.
    Duración proporcional al largo del mensaje (8 chars/seg ≈ ritmo humano).
    Loop porque la acción expira cada ~5 s.
    """
    delay = max(2.0, min(len(text) / 8, 15.0))   # 2–15 segundos
    elapsed = 0.0
    while elapsed < delay:
        try:
            await bot.send_chat_action(
                chat_id=chat_id,
                action="typing",
                business_connection_id=bc_id,
            )
        except Exception as e:
            log.debug(f"send_chat_action error: {e}")
        chunk = min(4.0, delay - elapsed)
        await asyncio.sleep(chunk)
        elapsed += chunk


async def deliver_vip_response(
    bot,
    *,
    chat_id: int,
    bc_id: str,
    username: str,
    gen: int,
    text: str,
) -> bool:
    """Leer → pausa → escribiendo → enviar. Retorna False si el turno quedó obsoleto."""
    if reply_gen.get(chat_id) != gen:
        log.info(f"Entrega cancelada (gen obsoleto) para {chat_id}")
        return False

    msg_id = pending_msg.get(chat_id)
    if msg_id:
        await asyncio.sleep(random.uniform(0.3, 1.0))
        await mark_as_read(bot, bc_id, chat_id, msg_id)

    if reply_gen.get(chat_id) != gen:
        return False

    await asyncio.sleep(random.uniform(1.5, 4.0))

    if reply_gen.get(chat_id) != gen:
        return False

    await simulate_typing(bot, chat_id, bc_id, text)

    if reply_gen.get(chat_id) != gen:
        return False

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            business_connection_id=bc_id,
        )
        history.setdefault(chat_id, []).append({"role": "assistant", "content": text})
        log.info(f"Enviado a {username}: {text[:80]}...")
        return True
    except Exception as e:
        log.error(f"Error enviando a {chat_id}: {e}")
        return False
