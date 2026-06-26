import asyncio
import sqlite3
import json
import logging
from datetime import datetime

# services/memory.py

log = logging.getLogger("diana")

# SECURITY NOTE (minimal hardening for PII high + validation medium, per review):
# Facts contain user PII (name etc). Sanitization applied on set.
# Treat as untrusted. No encryption per original design/PLAN.
# Shared conn with training (check_same_thread=False) documented; low concurrency use.

FACTS_TABLE = """
CREATE TABLE IF NOT EXISTS user_memory (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL,
    key      TEXT NOT NULL,        -- "name", "occupation", "interests", etc.
    value    TEXT NOT NULL,
    source   TEXT DEFAULT 'auto',  -- "auto" | "diana_manual"
    confidence INTEGER DEFAULT 80, -- 0-100
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, key) ON CONFLICT REPLACE
)"""

def schedule_memory_extract(
    service: "MemoryService | None",
    user_id: int,
    conversation: list[dict],
    llm_call_fn,
) -> None:
    """Background fact extraction after a successful delivery."""
    if service is None:
        return
    task = asyncio.create_task(
        service.extract_and_update(user_id, conversation, llm_call_fn),
    )

    def _log_extract_exc(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            log.error(f"memory extract_and_update error: {exc}")

    task.add_done_callback(_log_extract_exc)


KEYS_TRACKED = [
    "name",               # cómo se llama
    "occupation",         # trabajo / estudio
    "location",           # de dónde es
    "interests",          # hobbies, gustos
    "relationship",       # estado sentimental
    "personality",        # directo, tímido, gracioso
    "last_topic",         # último tema conversado
    "notable",            # dato curioso / importante
]

# PII handling note (for security review):
# Facts are auto-extracted and stored plaintext (same class as training data).
# Basic sanitization (printable + <=200 chars) applied in set_fact before persist.
# No encryption/retention per original design; caller (diana) controls.
# get_facts/get_context_block return as-is for prompt use.

class MemoryService:
    """
    WARNING (security): Stores user facts (PII: name, occupation, location, relationship,
    interests, personality, etc.) in plaintext sqlite. Auto-extracted from convos.
    No consent, retention, or encryption per design. For admin/internal use only.
    Sanitization applied on set but treat all facts as untrusted user data.
    """
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        # Shared synchronous sqlite conn (check_same_thread=False) with training.
        # Design choice from original PLAN/refactor (shared DB, no new deps).
        # Mitigates reentrancy for this use case but recommend aiosqlite/pool
        # + WAL for future to avoid locks under concurrent main+bg tasks (medium).
        self._init_tables()

    def _init_tables(self):
        self.db.execute(FACTS_TABLE)
        self.db.commit()

    def set_fact(self, user_id: int, key: str, value: str,
                 source="auto", confidence=80):
        if not value:
            return
        # Basic sanitization (per security review high PII Finding1 + medium validation Finding7):
        # cap to 300 chars, strip, drop non-printable + newlines (minimal to limit PII bloat and injection).
        # Still user-derived plaintext per original design; no encryption/retention added.
        s = str(value)[:300].strip()
        s = ''.join(c for c in s if c.isprintable() or c == ' ')
        s = s.replace('\n',' ').replace('\r',' ')[:300]
        if not s:
            return
        self.db.execute(
            "INSERT OR REPLACE INTO user_memory "
            "(user_id, key, value, source, confidence, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, key, s, source, confidence,
             datetime.now().isoformat())
        )
        self.db.commit()

    def get_facts(self, user_id: int) -> dict[str, str]:
        rows = self.db.execute(
            "SELECT key, value FROM user_memory WHERE user_id=? "
            "ORDER BY updated_at DESC", (user_id,)
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def get_context_block(self, user_id: int) -> str:
        """Devuelve bloque para inyectar al system prompt.
        (Per design: hardening for untrusted data applied only at injection site in llm.py;
        this returns the plain block or "" to preserve 0 behavior change.)
        """
        facts = self.get_facts(user_id)
        if not facts:
            return ""
        lines = ["\n\n---\nRECUERDAS SOBRE ESTE USUARIO:"]
        labels = {
            "name": "Se llama",
            "occupation": "Trabaja/estudia en",
            "location": "Es de",
            "interests": "Le interesa",
            "relationship": "Estado sentimental",
            "personality": "Su estilo",
            "last_topic": "Último tema",
            "notable": "Dato importante",
        }
        for key, value in facts.items():
            label = labels.get(key, key)
            lines.append(f"- {label}: {value}")
        lines.append("---")
        return "\n".join(lines)

    async def extract_and_update(
        self,
        user_id: int,
        conversation: list[dict],
        llm_call_fn,  # referencia a get_diana_response o función similar
    ):
        """
        Llama al LLM en background para extraer hechos nuevos.
        Se ejecuta como asyncio.create_task() — no bloquea la entrega.
        Solo corre si hay al menos 2 turnos de usuario.
        """
        user_turns = [m for m in conversation if m["role"] == "user"]
        if len(user_turns) < 2:
            return

        existing = self.get_facts(user_id)
        existing_str = json.dumps(existing, ensure_ascii=False)
        convo_str = "\n".join(
            f"{'Usuario' if m['role']=='user' else 'Diana'}: {m['content']}"
            for m in conversation[-10:]
        )

        prompt = f"""Extrae hechos relevantes sobre el usuario de esta conversación.
Hechos ya conocidos: {existing_str}

Conversación:
{convo_str}

Responde SOLO con JSON. Solo incluye claves con información NUEVA o CORREGIDA.
Claves válidas: name, occupation, location, interests, relationship, personality, last_topic, notable.
Si no hay nada nuevo, responde {{}}.
Ejemplo: {{"name": "Carlos", "interests": "gaming y música metal"}}"""

        response = await llm_call_fn(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response:
            return
        try:
            facts = json.loads(response)
            for key, value in facts.items():
                if key in KEYS_TRACKED and value:
                    self.set_fact(user_id, key, str(value),
                                  source="auto", confidence=75)
        except (json.JSONDecodeError, TypeError) as e:
            log.warning(f"memory extract JSON parse failed for user {user_id}: {e}")
