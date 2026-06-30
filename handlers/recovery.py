import asyncio
import logging
from datetime import datetime

from config import DIANA_ADMIN_CHAT_ID
from state import (
    history,
    reply_gen,
    chat_bc,
    chat_meta,
    timers,
    timer_schedule,
    pending_approval,
    _load_runtime_state,
    _save_runtime_state,
    _should_skip_timer_recovery,
)
from services.training import get_pending_examples
from .timer import auto_reply

log = logging.getLogger("diana")


def _restore_pending_from_db() -> int:
    """Reconstruye pending_approval desde SQLite si el runtime no tenía borradores."""
    if pending_approval:
        return 0
    try:
        examples = get_pending_examples()
    except RuntimeError:
        log.debug("DB no disponible — omitiendo fallback de borradores")
        return 0
    restored = 0
    for ex in examples:
        ex_id = ex["id"]
        chat_id = ex["chat_id"]
        if ex_id in pending_approval:
            continue
        history.setdefault(chat_id, ex["context"])
        from services.chat_history import merge_runtime_with_db
        merge_runtime_with_db(chat_id)
        bc_id = chat_bc.get(chat_id, "")
        gen = reply_gen.get(chat_id, 0)
        pending_approval[ex_id] = {
            "chat_id": chat_id,
            "bc_id": bc_id,
            "username": ex["username"],
            "gen": gen,
            "variants": [{
                "response": ex["response"],
                "confidence": ex["confidence"],
                "topic": ex["topic"],
            }],
            "selected": 0,
            "regenerating": False,
        }
        restored += 1
        if not bc_id:
            log.warning(
                f"Borrador {ex_id} restaurado sin bc_id para chat {chat_id}"
            )
    if restored:
        _save_runtime_state()
    return restored


async def recover_runtime_on_startup(bot) -> tuple[int, int]:
    """Carga runtime persistido, re-programa timers y devuelve (timers, borradores)."""
    _load_runtime_state()
    from services.chat_history import merge_all_loaded_chats
    merged = merge_all_loaded_chats()
    if merged:
        log.info(f"Historial DB fusionado en {merged} chat(s) tras runtime load")
    drafts_from_db = _restore_pending_from_db()
    merged_after_drafts = merge_all_loaded_chats()
    if merged_after_drafts:
        log.info(
            f"Historial DB fusionado en {merged_after_drafts} chat(s) tras restaurar borradores"
        )

    timers_recovered = 0
    for chat_id, meta in list(timer_schedule.items()):
        if _should_skip_timer_recovery(chat_id):
            log.info(f"Timer omitido para {chat_id}: último mensaje es de Diana")
            timer_schedule.pop(chat_id, None)
            continue
        if chat_id in timers:
            continue
        fire_at = datetime.fromisoformat(meta["fire_at"])
        remaining = max(0.0, (fire_at - datetime.now()).total_seconds())
        task = asyncio.create_task(
            auto_reply(
                bot,
                chat_id,
                meta["username"],
                meta["bc_id"],
                meta["gen"],
                delay_sec=remaining,
            ),
        )
        timers[chat_id] = task
        timers_recovered += 1
        log.info(
            f"Timer recuperado {meta['username']} ({chat_id}): "
            f"{remaining / 60:.1f} min restantes"
        )

    _save_runtime_state()

    drafts_recovered = len(pending_approval)
    if drafts_from_db:
        log.info(f"Borradores restaurados desde DB: {drafts_from_db}")

    total = timers_recovered + drafts_recovered
    if total and DIANA_ADMIN_CHAT_ID:
        try:
            await bot.send_message(
                chat_id=DIANA_ADMIN_CHAT_ID,
                text=(
                    "Recuperación tras reinicio:\n"
                    f"• {timers_recovered} timer(s) reanudado(s)\n"
                    f"• {drafts_recovered} borrador(es) restaurado(s)\n\n"
                    "Revisa borradores anteriores si los botones no responden."
                ),
            )
        except Exception as e:
            log.error(f"Error notificando recuperación a Diana: {e}")

    return timers_recovered, drafts_recovered