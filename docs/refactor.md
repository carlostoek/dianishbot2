
**Plan de refactorización:****Qué va a cada módulo:**

`config.py` — todo lo que está entre las líneas 30-65 de `diana.py` más `DIANA_SYSTEM_PROMPT`, `TOPIC_MAP`, `ESCALATE_KEYWORDS`. `diana.py` queda solo con `main()` y el `app.run_polling`.

`state.py` — los dicts globales actuales: `history`, `timers`, `connections`, `chat_bc`, `pending_msg`, `reply_gen`, `pending_approval`, `chat_meta`, `diana_user_id`, `awaiting_correction`.

`services/training.py` — `init_db`, `save_example`, `save_observed_example`, `update_rating`, `get_few_shots`, `build_few_shot_block`.

`services/llm.py` — `get_diana_response`, `guess_topic`.

`services/delivery.py` — `mark_as_read`, `simulate_typing`, `deliver_vip_response`.

`handlers/business.py` — `_handle_business_message`, `_resolve_sender_id`, `_resolve_vip_id`, `log_escalation`, `needs_escalation`.

`handlers/callbacks.py` — `handle_callback`, `handle_diana_correction`, `notify_diana_approval`, `notify_diana`.

`handlers/timer.py` — `auto_reply`.

`handlers/router.py` — `process_update`, `_post_init`.

---

**Diseño del servicio de memoria (`services/memory.py`):**

Tabla SQLite nueva. Dos enfoques para guardar datos: facts individuales (precisos, actualizables) + un snapshot JSON por usuario para inyección rápida al prompt.

```python
# services/memory.py

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

class MemoryService:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self._init_tables()

    def _init_tables(self):
        self.db.execute(FACTS_TABLE)
        self.db.commit()

    def set_fact(self, user_id: int, key: str, value: str,
                 source="auto", confidence=80):
        self.db.execute(
            "INSERT OR REPLACE INTO user_memory "
            "(user_id, key, value, source, confidence, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, key, value, source, confidence,
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
        """Devuelve bloque para inyectar al system prompt."""
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
        )

        if not response:
            return
        try:
            facts = json.loads(response)
            for key, value in facts.items():
                if key in KEYS_TRACKED and value:
                    self.set_fact(user_id, key, str(value),
                                  source="auto", confidence=75)
        except (json.JSONDecodeError, TypeError):
            pass
```

**Cómo se integra en el flujo actual:**

En `handlers/timer.py`, al final de `auto_reply` (después de que se entrega la respuesta), se dispara la extracción:

```python
# al final de auto_reply(), después de deliver_vip_response
asyncio.create_task(
    memory_service.extract_and_update(
        user_id=chat_id,
        conversation=state.history.get(chat_id, []),
        llm_call_fn=llm_service.raw_call,  # versión sin few-shots
    )
)
```

Y en `services/llm.py`, `get_diana_response` inyecta el bloque de memoria antes de los few-shots:

```python
memory_block = memory_service.get_context_block(chat_id)
few_shots_block = build_few_shot_block(examples)
system = DIANA_SYSTEM_PROMPT + memory_block + few_shots_block + "..."
```

---

**Plan de migración sugerido:** hazlo en 3 pasos, uno a la vez — primero extraer `config.py` + `state.py` (cero riesgo), luego los `services/`, luego los `handlers/`. Así el bot sigue funcionando entre pasos y puedes probar cada uno por separado.
