import asyncio
import random
import logging
from config import (
    APPROVAL_MODE, SILENCE_MINUTES, RESPONSE_DELAY_MIN, RESPONSE_DELAY_MAX,
    CONFIDENCE_THRESHOLD, KNOWLEDGE_GAP_ENABLED, is_llm_escalation_topic,
)
from services import auth_service
from state import (
    chat_write_lock, history, reply_gen, timers, pending_approval, chat_meta,
    _clear_timer_schedule, _save_runtime_state,
)
from services.llm import FAIL_ABORTED, get_diana_response, raw_call
from services.training import save_example, save_llm_failure
from services.delivery import deliver_vip_response
from services.memory import schedule_memory_extract
from .callbacks import notify_diana_approval, notify_diana, notify_diana_llm_failure
# wired at runtime from diana main (preserves memory extract task from item2)
memory_service = None
log = logging.getLogger("diana")


def _is_supervised_for_chat(chat_id: int) -> bool:
    """True when this chat still requires Diana approval before sending."""
    if not APPROVAL_MODE:
        return False
    vip_id = chat_meta.get(chat_id, {}).get("vip_id", chat_id)
    return not auth_service.is_auto_send_enabled(vip_id)


def compute_reply_delay(chat_id: int | None = None) -> float:
    supervised = (
        APPROVAL_MODE if chat_id is None else _is_supervised_for_chat(chat_id)
    )
    if supervised:
        return SILENCE_MINUTES * 60
    return random.uniform(RESPONSE_DELAY_MIN * 60, RESPONSE_DELAY_MAX * 60)


def _finish_timer(chat_id: int) -> None:
    if timers.get(chat_id) is asyncio.current_task():
        timers.pop(chat_id, None)
    _clear_timer_schedule(chat_id)
    _save_runtime_state()


async def enter_draft_pipeline(
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
    """Shared save → approve | deliver. Used by timer exit, g:use_draft, answer.

    Returns example_id, or None if gen is already stale.
    """
    if reply_gen.get(chat_id) != gen:
        log.warning(
            f"enter_draft_pipeline stale gen for {username} ({chat_id}): "
            f"expected {gen}, have {reply_gen.get(chat_id)}"
        )
        return None

    from services import data_pause, sandbox

    if data_pause.uses_synthetic_examples(chat_id):
        example_id = sandbox.allocate_draft_id()
    else:
        example_id = save_example(
            chat_id, username, history.get(chat_id, []),
            response, confidence, topic,
        )
    supervised = _is_supervised_for_chat(chat_id)
    log.info(
        f"Ejemplo {example_id} | conf={confidence}% | topic={topic} | "
        f"modo={'supervisado' if supervised else 'autónomo'}"
    )

    if supervised:
        async with chat_write_lock(chat_id):
            pending_approval[example_id] = {
                "chat_id": chat_id,
                "bc_id": bc_id,
                "username": username,
                "gen": gen,
                "variants": [
                    {"response": response, "confidence": confidence, "topic": topic},
                ],
                "selected": 0,
                "regenerating": False,
            }
            _clear_timer_schedule(chat_id)
            _save_runtime_state()
        await notify_diana_approval(
            bot, example_id, username, history.get(chat_id, []),
            response, confidence, topic,
            chat_id=chat_id, gen=gen,
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
            if ok and sandbox.should_persist(chat_id):
                schedule_memory_extract(
                    memory_service, chat_id, history.get(chat_id, []), raw_call,
                )
        except Exception as e:
            log.error(f"Error enviando a {chat_id}: {e}")
    return example_id


async def auto_reply(
    bot, chat_id: int, username: str, bc_id: str, gen: int,
    *, delay_sec: float | None = None,
):
    if delay_sec is None:
        delay_sec = compute_reply_delay(chat_id)
    log.info(f"⏳ {username}: respuesta programada en {delay_sec / 60:.1f} min")

    try:
        await asyncio.sleep(delay_sec)
    except asyncio.CancelledError:
        return

    if reply_gen.get(chat_id) != gen:
        return

    log.info(f"Cobertura activada para {username} ({chat_id})")

    response, confidence, topic, knowledge_gap, gap_question, failure = await get_diana_response(
        chat_id,
        should_abort=lambda: reply_gen.get(chat_id) != gen,
    )
    if not response:
        if failure and failure.reason != FAIL_ABORTED:
            from services import sandbox
            if sandbox.should_persist(chat_id):
                save_llm_failure(
                    chat_id, username, history.get(chat_id, []), failure, topic,
                )
            await notify_diana_llm_failure(
                bot, username=username, chat_id=chat_id,
                context=history.get(chat_id, []), failure=failure,
            )
        _finish_timer(chat_id)
        return

    if reply_gen.get(chat_id) != gen:
        _finish_timer(chat_id)
        return

    if is_llm_escalation_topic(topic):
        from .business import escalate_to_diana
        from services.training import is_known_false_positive

        msgs = history.get(chat_id, [])
        trigger = next(
            (m["content"] for m in reversed(msgs) if m["role"] == "user"), "",
        )
        if is_known_false_positive("llm", topic, trigger):
            log.info(
                f"Escalación LLM omitida — FP conocido para '{topic}' en {username}"
            )
        else:
            user_id = chat_meta.get(chat_id, {}).get("vip_id", chat_id)
            reason = f"Tema LLM: '{topic}'"
            await escalate_to_diana(
                bot,
                user_id=user_id,
                username=username,
                chat_id=chat_id,
                bc_id=bc_id,
                source="llm",
                reason=reason,
                trigger_text=trigger,
                context=msgs,
            )
            _finish_timer(chat_id)
            return

    # Gray-zone consult (after escalation, before save_example)
    if (
        KNOWLEDGE_GAP_ENABLED
        and knowledge_gap
        and (gap_question or "").strip()
    ):
        from services import data_pause, sandbox
        from services import knowledge as knowledge_mod
        from .callbacks.guidance import open_guidance_consult, notify_diana_guidance

        # Sandbox / data-pause: no real consult pollution — fall through normal path
        if sandbox.should_persist(chat_id) and not data_pause.uses_synthetic_examples(chat_id):
            msgs = history.get(chat_id, [])
            last_user = next(
                (m["content"] for m in reversed(msgs) if m["role"] == "user"), "",
            )
            matched = knowledge_mod.match_policies(topic, last_user, gap_question)
            if matched:
                # Anti-reask: one regen with policies injected; never open consult
                log.info(
                    f"Gap con política match ({len(matched)}) — un regen "
                    f"sin consulta para {username}"
                )
                if reply_gen.get(chat_id) != gen:
                    _finish_timer(chat_id)
                    return
                regen, r_conf, r_topic, _kg2, _gq2, r_fail = await get_diana_response(
                    chat_id,
                    should_abort=lambda: reply_gen.get(chat_id) != gen,
                )
                if regen:
                    response, confidence, topic = regen, r_conf, r_topic
                else:
                    log.warning(
                        f"Anti-reask regen failed for {username} ({r_fail}); "
                        f"using first draft"
                    )
                # Fall through to enter_draft_pipeline — do NOT re-check gap
            else:
                from state import pending_guidance as _pg
                gid = open_guidance_consult(
                    chat_id=chat_id,
                    bc_id=bc_id,
                    username=username,
                    gen=gen,
                    topic=topic,
                    gap_question=gap_question,
                    draft_response=response,
                    confidence=confidence,
                    context=msgs,
                )
                await notify_diana_guidance(
                    bot,
                    guidance_id=gid,
                    pending=_pg[gid],
                    context=msgs,
                )
                log.info(
                    f"Guidance consult #{gid} abierta para {username} — VIP freeze"
                )
                _finish_timer(chat_id)
                return

    await enter_draft_pipeline(
        bot,
        chat_id=chat_id,
        bc_id=bc_id,
        username=username,
        gen=gen,
        response=response,
        confidence=confidence,
        topic=topic,
    )
    _finish_timer(chat_id)
