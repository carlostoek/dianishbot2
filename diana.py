#!/usr/bin/env python3
"""
Diana Business Bot v2.0 — Chat Automation
Usa Settings > Chat Automation de Telegram. Sin riesgo de baneo.
Requiere python-telegram-bot >= 21.0
"""

import logging

from telegram import Update
from telegram.ext import Application, ContextTypes, TypeHandler

import auth_users

from config import (
    ADMIN_USER_ID,
    APPROVAL_MODE,
    AUTH_USERS_FILE,
    AUTH_USERS_MAX,
    BOT_TOKEN,
    CONFIDENCE_THRESHOLD,
    DB_FILE,
    ANTHROPIC_KEY,
    ANTHROPIC_MODEL,
    DEEPSEEK_KEY,
    DEEPSEEK_MODEL,
    LLM_PROVIDER,
    DIANA_ADMIN_CHAT_ID,
    DIANA_SYSTEM_PROMPT,
    ESCALATE_FILE,
    ESCALATE_KEYWORDS,
    LOG_FILE,
    MAX_FEW_SHOTS,
    MAX_HISTORY,
    OBSERVE_UNAUTHORIZED,
    RESPONSE_DELAY_MAX,
    RESPONSE_DELAY_MIN,
    SILENCE_MINUTES,
    SKIP_OBSERVED_TOPICS,
    TG_CONNECT_TIMEOUT,
    TG_POLL_TIMEOUT,
    TG_POOL_TIMEOUT,
    TG_READ_TIMEOUT,
    TG_WRITE_TIMEOUT,
    TOPIC_MAP,
    VIP_USERS_SEED,
)
from state import (
    _save_connections_state,
    awaiting_correction,
    chat_bc,
    chat_meta,
    connections,
    history,
    pending_approval,
    pending_msg,
    reply_gen,
    timers,
)
import state

from services.training import (
    init_db,
    save_example,
    save_observed_example,
    update_rating,
    get_few_shots,
    build_few_shot_block,
)
from services.llm import guess_topic, get_diana_response, raw_call
from services.delivery import mark_as_read, simulate_typing, deliver_vip_response
from services.memory import MemoryService

# reexports for smoke compat: import diana; diana.guess_topic etc
# (from-imports above make them available on module)

from handlers.router import process_update, _post_init
from handlers.error_handler import error_handler

db: "sqlite3.Connection | None" = None
memory_service: MemoryService | None = None


# ═══════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("diana")



# ═══════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════

def main():
    global db, memory_service

    llm_key_name = "ANTHROPIC_KEY" if LLM_PROVIDER == "anthropic" else "DEEPSEEK_KEY"
    llm_key_val = ANTHROPIC_KEY if LLM_PROVIDER == "anthropic" else DEEPSEEK_KEY
    missing = [name for name, val in (
        ("BOT_TOKEN", BOT_TOKEN),
        (llm_key_name, llm_key_val),
    ) if not val]
    if missing:
        raise SystemExit(
            f"Faltan variables de entorno: {', '.join(missing)}. "
            "Copia .env.example a .env y configúralas."
        )
    if LLM_PROVIDER not in ("deepseek", "anthropic"):
        raise SystemExit(
            f"LLM_PROVIDER inválido: {LLM_PROVIDER!r}. Usa 'deepseek' o 'anthropic'."
        )

    db = init_db()
    import services.training as training_mod
    training_mod.db = db
    memory_service = MemoryService(db)
    import services.llm as llm_mod
    llm_mod.memory_service = memory_service
    import handlers.timer as timer_mod; timer_mod.memory_service = memory_service
    log.info(f"DB de entrenamiento lista: {DB_FILE}")
    llm_model = ANTHROPIC_MODEL if LLM_PROVIDER == "anthropic" else DEEPSEEK_MODEL
    log.info(f"Diana Business Bot v2.0 iniciando... | LLM: {LLM_PROVIDER} ({llm_model})")

    if ADMIN_USER_ID:
        auth_users.set_admin_id(ADMIN_USER_ID)

    auth_users.configure(
        users_file=AUTH_USERS_FILE,
        max_users=AUTH_USERS_MAX,
        seed_user_ids=VIP_USERS_SEED,
        admin_id=ADMIN_USER_ID,
    )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .connect_timeout(TG_CONNECT_TIMEOUT)
        .read_timeout(TG_READ_TIMEOUT)
        .write_timeout(TG_WRITE_TIMEOUT)
        .pool_timeout(TG_POOL_TIMEOUT)
        .get_updates_connect_timeout(TG_CONNECT_TIMEOUT)
        .get_updates_read_timeout(TG_READ_TIMEOUT + TG_POLL_TIMEOUT)
        .get_updates_write_timeout(TG_WRITE_TIMEOUT)
        .get_updates_pool_timeout(TG_POOL_TIMEOUT)
        .build()
    )

    # TypeHandler captura todos los updates, incluyendo business_*
    app.add_handler(TypeHandler(Update, process_update))

    # Error handler global (PTB hardener requirement).
    # Captura TODAS las excepciones no manejadas en handlers, jobs, etc.
    # Debe registrarse DESPUÉS de los handlers normales.
    app.add_error_handler(error_handler)

    modo = "supervisado" if APPROVAL_MODE else "autónomo"
    delay_info = (
        f"{SILENCE_MINUTES} min"
        if APPROVAL_MODE
        else f"{RESPONSE_DELAY_MIN}–{RESPONSE_DELAY_MAX} min"
    )
    log.info(
        f"VIPs autorizados: {len(auth_users.get_authorized_ids())} | "
        f"Observación no autorizados: {'sí' if OBSERVE_UNAUTHORIZED else 'no'} | "
        f"Modo: {modo} | Delay: {delay_info} | "
        f"Aprobación: manual (sin auto-envío) | "
        f"Umbral: {CONFIDENCE_THRESHOLD}%"
    )

    app.run_polling(
        allowed_updates=[
            "business_connection",
            "business_message",
            "edited_business_message",
            "message",
            "callback_query",
        ],
        timeout=TG_POLL_TIMEOUT,
        bootstrap_retries=-1,
    )

if __name__ == "__main__":
    main()
