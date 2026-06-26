import asyncio
import json
import logging
from pathlib import Path

from config import STATE_FILE

log = logging.getLogger("diana")

# ══ ESTADO DE CORRECCIÓN PENDIENTE ══════════════════════
awaiting_correction: dict[int, int] = {}

# ═══════════════════════════════════════════════════════
#  ESTADO EN MEMORIA
# ═══════════════════════════════════════════════════════

# historial por chat: {chat_id: [{"role": ..., "content": ...}]}
history: dict[int, list[dict]] = {}

# timers activos: {chat_id: asyncio.Task}
timers: dict[int, asyncio.Task] = {}

# business connections activas: {bc_id: owner_user_id}
connections: dict[str, int] = {}

# último bc_id usado por cada chat (para enviar respuesta correctamente)
chat_bc: dict[int, str] = {}

# último message_id VIP pendiente de respuesta (para marcar leído al contestar)
pending_msg: dict[int, int] = {}

# generación de timer por chat — evita respuestas duplicadas si el timer se reinicia
reply_gen: dict[int, int] = {}

# Borradores en espera de aprobación de Diana: {example_id: {chat_id, bc_id, username, response, gen}}
pending_approval: dict[int, dict] = {}

# Metadatos de chats observados (no autorizados): {chat_id: {vip_id, username}}
chat_meta: dict[int, dict] = {}

# ID de Diana — se resuelve al activar business_connection
diana_user_id: int | None = None


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
