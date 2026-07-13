import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from config import RUNTIME_STATE_FILE, STATE_FILE

log = logging.getLogger("diana")

RUNTIME_VERSION = 1

# ══ ESTADO DE CORRECCIÓN PENDIENTE ══════════════════════
awaiting_correction: dict[int, int] = {}

# Captura de nota manual: {diana_telegram_id: {"user_id", "username", "example_id"?,
#   "draft_chat_id"?, "draft_message_id"?}}
awaiting_note: dict[int, dict] = {}

# Captura de nota desde menu admin inline: {diana_telegram_id: {"user_id", "username"}}
awaiting_admin_note: dict[int, dict] = {}

# ═══════════════════════════════════════════════════════
#  ESTADO EN MEMORIA
# ═══════════════════════════════════════════════════════

# historial por chat: {chat_id: [{"role": ..., "content": ...}]}
history: dict[int, list[dict]] = {}

# timers activos: {chat_id: asyncio.Task}
timers: dict[int, asyncio.Task] = {}

# metadata persistible de timers: {chat_id: {username, bc_id, gen, fire_at}}
timer_schedule: dict[int, dict] = {}

# business connections activas: {bc_id: owner_user_id}
connections: dict[str, int] = {}

# último bc_id usado por cada chat (para enviar respuesta correctamente)
chat_bc: dict[int, str] = {}

# último message_id VIP pendiente de respuesta (para marcar leído al contestar)
pending_msg: dict[int, int] = {}

# generación de timer por chat — evita respuestas duplicadas si el timer se reinicia
reply_gen: dict[int, int] = {}

# Borradores en espera de aprobación: {example_id: {chat_id, bc_id, username, gen, variants[], selected, regenerating}}
# variants[i] = {"response": str, "confidence": int, "topic": str}
pending_approval: dict[int, dict] = {}

# Escalaciones pendientes de triage: {esc_id: {chat_id, bc_id, username, gen, source, reason, matched, trigger_text, verdict}}
pending_escalations: dict[int, dict] = {}

# Metadatos de chats observados (no autorizados): {chat_id: {vip_id, username}}
chat_meta: dict[int, dict] = {}

# ID de Diana — se resuelve al activar business_connection
diana_user_id: int | None = None

# Per-chat locks for history / pending_approval writes
_chat_locks: dict[int, asyncio.Lock] = {}


@asynccontextmanager
async def chat_write_lock(chat_id: int):
    lock = _chat_locks.setdefault(chat_id, asyncio.Lock())
    async with lock:
        yield


def _runtime_excluded(chat_id: int) -> bool:
    from services import data_pause, sandbox

    return sandbox.is_active(chat_id) or data_pause.is_paused(chat_id)


def _active_chat_ids() -> set[int]:
    ids = set(timer_schedule.keys())
    for pending in pending_approval.values():
        ids.add(pending["chat_id"])
    for pending in pending_escalations.values():
        ids.add(pending["chat_id"])
    return {cid for cid in ids if not _runtime_excluded(cid)}


def _build_runtime_snapshot() -> dict:
    active = _active_chat_ids()
    timers_out = []
    for chat_id, meta in timer_schedule.items():
        if chat_id in active:
            timers_out.append({"chat_id": chat_id, **meta})
    return {
        "version": RUNTIME_VERSION,
        "reply_gen": {str(k): v for k, v in reply_gen.items() if k in active},
        "chat_bc": {str(k): v for k, v in chat_bc.items() if k in active},
        "chat_meta": {str(k): v for k, v in chat_meta.items() if k in active},
        "pending_msg": {str(k): v for k, v in pending_msg.items() if k in active},
        "history": {
            str(k): v for k, v in history.items() if k in active
        },
        "timers": timers_out,
        "pending_approval": {
            str(k): v for k, v in pending_approval.items()
            if not _runtime_excluded(v.get("chat_id", 0))
        },
        "pending_escalations": {
            str(k): v for k, v in pending_escalations.items()
            if not _runtime_excluded(v.get("chat_id", 0))
        },
    }


def _save_runtime_state() -> None:
    path = Path(RUNTIME_STATE_FILE)
    snapshot = _build_runtime_snapshot()
    if (
        not snapshot["timers"]
        and not snapshot["pending_approval"]
        and not snapshot["pending_escalations"]
    ):
        if path.exists():
            try:
                path.unlink()
            except OSError as e:
                log.error(f"Error eliminando runtime vacío: {e}")
        return
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception as e:
        log.error(f"Error guardando runtime: {e}")


def _load_runtime_state() -> None:
    path = Path(RUNTIME_STATE_FILE)
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != RUNTIME_VERSION:
            log.warning(f"Runtime version {data.get('version')} distinta de {RUNTIME_VERSION}")
        for k, v in data.get("reply_gen", {}).items():
            reply_gen[int(k)] = v
        for k, v in data.get("chat_bc", {}).items():
            chat_bc[int(k)] = v
        for k, v in data.get("chat_meta", {}).items():
            chat_meta[int(k)] = v
        for k, v in data.get("pending_msg", {}).items():
            pending_msg[int(k)] = v
        for k, v in data.get("history", {}).items():
            history[int(k)] = v
        timer_schedule.clear()
        for entry in data.get("timers", []):
            chat_id = entry.pop("chat_id")
            timer_schedule[chat_id] = entry
        pending_approval.clear()
        for k, v in data.get("pending_approval", {}).items():
            pending = dict(v)
            pending["regenerating"] = False
            pending_approval[int(k)] = pending
        pending_escalations.clear()
        for k, v in data.get("pending_escalations", {}).items():
            pending_escalations[int(k)] = dict(v)
        if timer_schedule or pending_approval or pending_escalations:
            log.info(
                f"Runtime restaurado: {len(timer_schedule)} timer(s), "
                f"{len(pending_approval)} borrador(es), "
                f"{len(pending_escalations)} escalación(es)"
            )
    except Exception as e:
        log.error(f"Error cargando runtime: {e}")


def _clear_timer_schedule(chat_id: int) -> None:
    timer_schedule.pop(chat_id, None)


def _should_skip_timer_recovery(chat_id: int) -> bool:
    msgs = history.get(chat_id, [])
    return bool(msgs and msgs[-1]["role"] == "assistant")


def _load_connections_state() -> None:
    path = Path(STATE_FILE)
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for bc_id, owner_id in data.get("connections", {}).items():
            connections[bc_id] = owner_id
        if connections:
            log.info(f"Conexiones restauradas: {len(connections)}")
    except Exception as e:
        log.error(f"Error cargando estado: {e}")


def _save_connections_state() -> None:
    Path(STATE_FILE).write_text(
        json.dumps({"connections": connections}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )