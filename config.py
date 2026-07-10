import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ═══════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ═══════════════════════════════════════════════════════

BOT_TOKEN      = os.getenv("BOT_TOKEN")

# Proveedor LLM: "deepseek" (default) o "anthropic"
LLM_PROVIDER   = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()

DEEPSEEK_KEY   = os.getenv("DEEPSEEK_KEY")
DEEPSEEK_URL   = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")

ANTHROPIC_KEY      = os.getenv("ANTHROPIC_KEY")
ANTHROPIC_URL      = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL    = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
ANTHROPIC_VERSION  = "2023-06-01"
LLM_SETTINGS_FILE = "diana_llm_settings.json"
SYSTEM_PROMPT_FILE = os.getenv("DIANA_SYSTEM_PROMPT_FILE", "diana_system_prompt.md")
SANDBOX_PROFILES_FILE = "diana_sandbox_profiles.json"
TRACE_FILE = "diana_traces.jsonl"

# Usuarios VIP iniciales (se migran a diana_authorized_users.json al primer arranque)
VIP_USERS_SEED = {
    1280444712,
}
AUTH_USERS_FILE = "diana_authorized_users.json"
AUTH_USERS_MAX  = 10
STATE_FILE         = "diana_state.json"
RUNTIME_STATE_FILE = "diana_runtime.json"

RESPONSE_DELAY_MIN = 3   # minutos — inicio del rango de espera antes del flujo
RESPONSE_DELAY_MAX = 10  # minutos — fin del rango (aleatorio entre min y max)
MAX_HISTORY     = 50    # mensajes de contexto que se envían al LLM
MAX_STORED_HISTORY = 50   # mensajes persistidos en SQLite por chat (recorte en append)
BACKFILL_INTERVAL_SEC = 3600
BACKFILL_MSG_LIMIT = 100
BACKFILL_QUEUE_FILE = "diana_backfill_queue.json"

# VIP idle re-engagement (multi-day silence scanner; not auto_reply)
REENGAGE_ENABLED = True
REENGAGE_IDLE_DAYS = 2
REENGAGE_SCAN_INTERVAL_SEC = 3600
REENGAGE_STATE_FILE = "diana_reengage_state.json"
REENGAGE_TEMPLATES = [
    "Oye, ¿todo bien? Hace rato que no sé de ti 😊",
    "Hey, ¿seguís por acá? Me acordé de vos 💭",
    "¿Todo bien de tu lado? Si estás ocupado no hay problema ✨",
]
LLM_MAX_RETRIES = 3     # intentos totales ante fallo de red, JSON inválido o respuesta vacía
LLM_RETRY_DELAY_SEC = 2.0  # pausa entre reintentos

# Timeouts de red con Telegram (segundos) — defaults de PTB (5 s) causan ReadTimeout
TG_CONNECT_TIMEOUT = 15.0
TG_READ_TIMEOUT    = 30.0
TG_WRITE_TIMEOUT   = 30.0
TG_POOL_TIMEOUT    = 5.0
TG_POLL_TIMEOUT    = 30    # long-polling; get_updates_read_timeout debe ser mayor
LOG_FILE        = "diana_business.log"
ESCALATE_FILE   = "diana_escalaciones.txt"

ADMIN_USER_ID             = 6181290784   # Diana — DM privado con el bot (/start)

# ══ CONFIG DE ENTRENAMIENTO ══════════════════════════════
DIANA_ADMIN_CHAT_ID = ADMIN_USER_ID  # ← ID personal de Diana (mensaje @userinfobot)
CONFIDENCE_THRESHOLD = 70            # respuestas < 70 → notificar a Diana
APPROVAL_MODE = True                 # True = supervisado · False = autónomo
SILENCE_MINUTES = 2                  # espera en modo supervisado (Diana ya mira el chat)
OBSERVE_UNAUTHORIZED = True          # escuchar chats no autorizados (sin auto-respuesta)
DB_FILE = "diana_training.db"
MAX_FEW_SHOTS = 3

# Temas excluidos solo de ejemplos observados (chats no autorizados).
# FAQs transaccionales/informativas — no aportan al estilo personal de Diana.
SKIP_OBSERVED_TOPICS = {"contenido", "precio", "acceso", "horarios", "presentacion"}

# ══ CLASIFICADOR DE TEMA (para few-shots antes del LLM) ══
TOPIC_MAP = {
    "precio": ["precio", "costo", "cuánto", "cuanto", "pago", "cobro", "suscripción"],
    "contenido": ["foto", "video", "contenido", "publicación", "pack", "material"],
    "acceso": ["acceso", "link", "canal", "grupo", "entrar", "no puedo"],
    "horarios": ["cuando", "cuándo", "horario", "hora", "disponible", "activa"],
    "presentacion": ["hola", "saludos", "quién eres", "quien eres", "cuéntame"],
}

# ══ RUTINA SEMANAL (contexto temporal inyectado al LLM) ══
DIANA_TIMEZONE = "America/Mexico_City"

DIANA_WEEKLY_SCHEDULE = """
Consulta esta rutina antes de responder sobre qué estoy haciendo, disponibilidad u horarios.
No inventes actividades diurnas (servicio, prácticas, clases) fuera de estos bloques.

| Día | Horario | Actividad |
|-----|---------|-----------|
| L-V | 9:00-14:00 | Servicio social (instituto de adicciones) |
| L-J | 16:00-21:00 | Prácticas profesionales (casa hogar) |
| V | 17:00-20:00 | Diplomado de gamificación |
| S | 8:00-12:00 | Clases de inglés |
| S | desde 15:00 | Asesoría/ayuda con tareas a niños (3 niños, 1 h c/u, voy a cada casa) |
| D | todo el día | Visitar a mi hermana |
""".strip()

# ═══════════════════════════════════════════════════════
#  PROMPT — contenido en diana_system_prompt.md (gitignored)
# ═══════════════════════════════════════════════════════

_system_prompt_cache: str | None = None


def load_system_prompt(*, path: str | Path | None = None, force: bool = False) -> str:
    """Lee el system prompt desde disco y lo cachea en memoria."""
    global _system_prompt_cache
    if _system_prompt_cache is not None and not force:
        return _system_prompt_cache

    prompt_path = Path(path or SYSTEM_PROMPT_FILE)
    if not prompt_path.is_file():
        raise FileNotFoundError(
            f"No se encontró el system prompt en {prompt_path}. "
            f"Crea {SYSTEM_PROMPT_FILE} en la raíz del proyecto."
        )

    text = prompt_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"El system prompt en {prompt_path} está vacío.")

    _system_prompt_cache = text
    return text


def get_system_prompt() -> str:
    return load_system_prompt()


def reset_system_prompt_cache() -> None:
    global _system_prompt_cache
    _system_prompt_cache = None

# ═══════════════════════════════════════════════════════
#  PALABRAS QUE ACTIVAN ESCALACIÓN INMEDIATA
# ═══════════════════════════════════════════════════════

ESCALATE_KEYWORDS = [
    "pago", "cobro", "precio", "suscripción", "suscripcion",
    "cancelar", "reembolso", "reclamo", "queja",
    "bot", "robot", "automático", "automatico",
    "inteligencia artificial", " ia ",
]

# Topics que devuelve el LLM cuando el prompt pide escalar a Diana real
ESCALATE_LLM_TOPICS = frozenset({"escalado_humano", "escalado"})


def is_llm_escalation_topic(topic: str) -> bool:
    if not topic:
        return False
    norm = topic.strip().lower().replace(" ", "_")
    if norm in ESCALATE_LLM_TOPICS:
        return True
    return norm.startswith("escalado_")
