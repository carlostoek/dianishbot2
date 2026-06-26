import asyncio
import random
import logging
from config import (
    APPROVAL_MODE, SILENCE_MINUTES, RESPONSE_DELAY_MIN, RESPONSE_DELAY_MAX, CONFIDENCE_THRESHOLD,
)
from state import history, reply_gen, timers, pending_approval
from services.llm import get_diana_response, raw_call
from services.training import save_example
from services.delivery import deliver_vip_response
from services.memory import schedule_memory_extract
from .callbacks import notify_diana_approval, notify_diana
# wired at runtime from diana main (preserves memory extract task from item2)
memory_service = None
log = logging.getLogger("diana")


async def auto_reply(
    bot, chat_id: int, username: str, bc_id: str, gen: int,
):
    if APPROVAL_MODE:
        delay_sec = SILENCE_MINUTES * 60
    else:
        delay_sec = random.uniform(RESPONSE_DELAY_MIN * 60, RESPONSE_DELAY_MAX * 60)
    log.info(f"⏳ {username}: respuesta programada en {delay_sec / 60:.1f} min")

    try:
        await asyncio.sleep(delay_sec)
    except asyncio.CancelledError:
        return

    if reply_gen.get(chat_id) != gen:
        return

    log.info(f"Cobertura activada para {username} ({chat_id})")

    response, confidence, topic = await get_diana_response(chat_id)
    if not response:
        log.warning(f"Sin respuesta LLM para {chat_id}")
        if timers.get(chat_id) is asyncio.current_task():
            timers.pop(chat_id, None)
        return

    if reply_gen.get(chat_id) != gen:
        return

    example_id = save_example(
        chat_id, username, history.get(chat_id, []),
        response, confidence, topic,
    )
    log.info(
        f"Ejemplo {example_id} | conf={confidence}% | topic={topic} | "
        f"modo={'supervisado' if APPROVAL_MODE else 'autónomo'}"
    )

    if APPROVAL_MODE:
        pending_approval[example_id] = {
            "chat_id": chat_id,
            "bc_id": bc_id,
            "username": username,
            "response": response,
            "gen": gen,
        }
        await notify_diana_approval(
            bot, example_id, username, history.get(chat_id, []),
            response, confidence, topic,
        )
    else:
        if confidence < CONFIDENCE_THRESHOLD:
            asyncio.create_task(
                notify_diana(
                    bot, example_id, username, history.get(chat_id, []),
                    response, confidence, topic,
                ),
            )
        try:
            ok = await deliver_vip_response(
                bot, chat_id=chat_id, bc_id=bc_id,
                username=username, gen=gen, text=response,
            )
            if ok:
                schedule_memory_extract(
                    memory_service, chat_id, history.get(chat_id, []), raw_call,
                )
        except Exception as e:
            log.error(f"Error enviando a {chat_id}: {e}")

    if timers.get(chat_id) is asyncio.current_task():
        timers.pop(chat_id, None)
