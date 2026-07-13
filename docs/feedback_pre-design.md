# Diseño: Consulta de Zona Gris → Conocimiento con Peso

Antes de proponer nada, ancло el diseño en lo que el bot ya hace, porque hay tres flujos parecidos que **no** resuelven lo que pides y es importante no confundirlos:

| Flujo actual | Qué dispara | Qué hace Diana | Qué queda guardado |
|---|---|---|---|
| **Aprobación** (`pending_approval`, `handlers/callbacks/approval.py`) | Cada borrador en modo supervisado | Aprueba / corrige / regenera | El texto final tal cual, como few-shot por `topic` |
| **Escalación** (`pending_escalations`, `handlers/callbacks/escalation.py`) | Keywords o `topic="escalado_humano"` del LLM | Responde manualmente en el chat real | Nada estructurado — solo el evento de escalación |
| **Nota** (`awaiting_note`, `MemoryService`) | Diana pulsa 📝 | Escribe algo libre | Texto crudo, **por usuario**, sin análisis ni alcance por tema |

Ninguno de los tres hace lo que describes: **pausar antes de responder, preguntar algo puntual (no "aprueba esto"), y convertir la respuesta de Diana en una regla reutilizable por tema con prioridad alta**. Eso es un cuarto flujo nuevo. Lo llamo **"Consulta de Zona Gris"**.

---

## 1. Señal de detección (dónde nace la "duda")

Hoy el LLM ya devuelve `confidence` y `topic` en el JSON de `get_diana_response` (`services/llm.py`). Pero `confidence` bajo ≠ "no tengo doctrina para esto" — puede ser solo una respuesta genérica. Necesitas una señal distinta y explícita.

Propongo extender el schema (`DIANA_RESPONSE_SCHEMA`) con dos campos nuevos:

```json
{
  "response": "...",          // sigue existiendo: borrador tentativo, por si Diana decide usarlo
  "confidence": 85,
  "topic": "limites_contenido",
  "knowledge_gap": true,
  "gap_question": "Un usuario pide una videollamada privada fuera de las tarifas normales. No tengo una política clara para esto — ¿cómo debo manejarlo?"
}
```

Y en el prompt, una sección nueva con criterios estrictos de cuándo marcar `knowledge_gap=true` (para no dispararlo por cualquier cosa): situaciones **nuevas o sin precedente** en `brief.md`/notas/políticas ya aprendidas, no simples preguntas de precio/horario que ya cubre `TOPIC_MAP`. Esto es autocontenido — nada nuevo en infraestructura, solo prompt engineering + un `if` sobre el JSON parseado.

---

## 2. Antes de repreguntar: ¿ya existe doctrina para esto?

Este es el punto que evita que el sistema sea molesto: **una vez que Diana responde algo una vez, no debe volver a preguntarse**. Por eso, antes de mostrarle la consulta a Diana, el bot debe intentar resolver el `knowledge_gap` contra una base de políticas ya aprendidas (tabla nueva, ver §4) usando el mismo `topic` + matching por keywords. Si hay match → se inyecta esa política al prompt y se regenera sin molestar a Diana. Solo si no hay match se abre la consulta real.

---

## 3. Flujo de la consulta (paralelo al de aprobación/escalación)

```
LLM detecta knowledge_gap=true
        │
        ▼
¿hay política previa para topic/keywords? ──sí──► inyectar política, responder normal
        │no
        ▼
crear pending_guidance[id] = {chat_id, bc_id, username, gen,
                               topic, gap_question, draft_response}
        │
        ▼
notify a Diana (DM) — mensaje NUEVO, distinto al de aprobación:
  "🧭 Diana, necesito tu criterio:
   {gap_question}
   (contexto del chat abajo)"
  Botones: [Responder]  [Enviar borrador igual]  [Ignorar/Fix manual]
        │
   Diana pulsa "Responder" → awaiting_guidance_answer[diana_id] = guidance_id
   Diana escribe su respuesta en texto libre (igual que awaiting_note)
        │
        ▼
   PASO NUEVO — destilación por LLM (no solo guardar el texto crudo):
   distill_guidance(pregunta, respuesta_de_diana, contexto) → JSON:
     {
       "topic": "limites_contenido",
       "policy_summary": "regla reusable en 1-3 frases, tono Diana",
       "example_response": "ejemplo de cómo respondería Diana en este caso",
       "keywords": ["videollamada", "privado", "fuera de tarifa"],
       "priority": "alta"
     }
        │
        ▼
   guardar en tabla topic_policies (peso alto, no expira)
        │
        ▼
   regenerar el borrador para el VIP (ya con la política inyectada)
   → entra al flujo de aprobación normal (approve/fix) para el envío final
```

Esto reutiliza casi 1:1 el patrón que ya existe para "nota que dispara regeneración de variante" (`handle_diana_note` cuando viene desde un borrador — `_regen_approval_variant`, `draft_chat_id`/`draft_message_id`). No es una arquitectura nueva, es el mismo patrón con un paso de análisis LLM intercalado.

---

## 4. Modelo de datos

**Nueva tabla** (vive en `services/training.py::init_db()`, mismo patrón que `chat_history`/`promo_info`):

```sql
CREATE TABLE IF NOT EXISTS topic_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    keywords TEXT,              -- JSON list, para matching además del topic exacto
    policy_summary TEXT NOT NULL,
    example_response TEXT,
    priority INTEGER DEFAULT 100,
    source_question TEXT,
    source_answer_raw TEXT,     -- lo que Diana escribió, sin editar (auditoría)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS guidance_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    username TEXT,
    ts TEXT NOT NULL,
    topic TEXT,
    gap_question TEXT NOT NULL,
    context TEXT,
    diana_answer_raw TEXT,
    policy_id INTEGER,          -- FK a topic_policies una vez destilado
    status TEXT DEFAULT 'pending'  -- pending | answered | skipped | timeout
);
```

`guidance_requests` es el equivalente durable de `escalation_events` — sirve para un futuro `/consultas` de reporting, igual que `/escalaciones` y `/fallos` hoy.

**Estado en runtime** (`state.py`), mismo patrón que `pending_escalations`:

```python
pending_guidance: dict[int, dict] = {}
awaiting_guidance_answer: dict[int, int] = {}  # {diana_id: guidance_id}
```

Con persistencia en `diana_runtime.json` igual que `pending_approval`/`pending_escalations` (para sobrevivir reinicios).

---

## 5. Inyección con peso — el punto central de tu pedido

En `get_diana_response` (`services/llm.py`), justo donde hoy se arma:

```python
system = base_prompt + temporal_block + memory_block + few_shots + escalation_fp_block + ...
```

se agrega un bloque nuevo, **antes** de los few-shots pero **después** de las notas de usuario (las notas son sobre *esa persona*; las políticas son doctrina general del tema, más "constitucional"):

```
NOTAS PERSONALES (del usuario)          ← ya existe, máxima prioridad personal
POLÍTICAS APRENDIDAS (por tema)         ← NUEVO: doctrina de Diana, prioridad alta
ESCALACIONES FALSO-POSITIVO             ← ya existe
EJEMPLOS APRENDIDOS (few-shots)         ← ya existe, tono/estilo
```

`get_topic_policies(topic, keywords)` recupera activas ordenadas por `priority`, y `build_topic_policy_block(...)` las formatea así — deliberadamente etiquetado como regla, no como ejemplo, para que el LLM lo trate como instrucción y no como "inspiración":

```
POLÍTICAS DE DIANA (instrucciones vigentes — síguelas siempre, tienen prioridad sobre tu criterio):
  [limites_contenido] Regla: {policy_summary}
    Ejemplo de tono: "{example_response}"
```

Esto es exactamente el "peso importante para ese tema" que pides: no es un few-shot que compite por espacio con otros 3 ejemplos — es una instrucción fija por tema mientras esté activa.

---

## 6. Guardrails necesarios (para que esto no se vuelva pesado de usar)

- **Anti-repregunta**: como en §2, siempre buscar política existente antes de molestar a Diana.
- **No bloquear todo el bot**: mientras `pending_guidance` está abierto, ese VIP específico simplemente no recibe respuesta (igual que hoy pasa con escalaciones) — no hace falta un mensaje de "espera" automático, pero es una decisión de producto que vale la pena confirmar contigo.
- **Timeout / expiración**: si Diana no contesta en X horas, ¿el bot manda el `draft_response` tentativo, o sigue esperando indefinidamente? Sugiero un timeout configurable (ej. `GUIDANCE_TIMEOUT_HOURS`) que, al vencer, cae a comportamiento actual (approval flow normal con el draft tentativo), para no dejar al VIP colgado para siempre.
- **Criterios estrictos en el prompt** para `knowledge_gap=true` — si se dispara demasiado seguido, se vuelve ruido. Vale la pena arrancar con un flag `KNOWLEDGE_GAP_ENABLED` y medir cuántas veces se activa antes de dejarlo siempre encendido.
- **Edición de políticas**: con el tiempo van a acumularse `topic_policies` — conviene un comando admin simple (`/politicas <topic>`, `/borrar_politica <id>`) similar a `/nota`/`/borrar_notas`, para que Diana pueda corregir si una regla quedó mal destilada.

---

## 7. Plan de implementación (siguiendo el patrón `openspec/` que ya usa el repo)

Dado que el proyecto ya versiona cambios como `openspec/changes/<nombre>/{proposal,design,tasks}.md` (ver el ejemplo de `non-vip-promo-info-autoreply`), yo lo dividiría en 3 unidades de trabajo, igual de independientes que ese caso:

1. **WU1 — Detección + persistencia base**: schema `topic_policies`/`guidance_requests`, extensión del JSON schema del LLM, `services/knowledge.py` con `get_matching_policy()` y `distill_guidance()`.
2. **WU2 — Flujo de consulta**: `state.pending_guidance`/`awaiting_guidance_answer`, handler nuevo (`handlers/callbacks/guidance.py`), notificación a Diana, captura de respuesta, wiring en `router.py` (con exclusión mutua respecto a `awaiting_note`/`awaiting_correction`, mismo patrón que ya existe entre esos dos).
3. **WU3 — Inyección + regeneración**: bloque de políticas en `get_diana_response`, regeneración automática del borrador tras destilar, reentrada al flujo de aprobación normal.

