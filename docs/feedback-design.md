# Design: Consulta de Zona Gris (Guidance → Topic Policies)

**Status:** Final design (product-locked; planning next)  
**Source:** `docs/feedback_pre-design.md` + code review against main (2026-07-13) + product lock 2026-07-13  
**Name:** Consulta de Zona Gris / **Guidance**  
**Callback prefix:** `g:` (next free after `a:`, `t:`, `e:`, `au:`)

---

## 0. Problem statement

When the bot faces a **gray zone** (no clear doctrine: limits, exceptions, one-off commercial requests, policy ambiguity), it must:

1. **Pause** before sending anything to the VIP.
2. **Ask Diana a concrete question** via the bot DM UI (not “approve this draft”).
3. **Distill** Diana’s free-text answer into reusable topic knowledge with **high priority**.
4. **Inject** that doctrine on future (and the current) replies so the same gap is not re-asked.

This is a **fourth flow**. It must not be conflated with approval, escalation, or per-user notes.

### 0.1 VIP freeze contract (product-locked — non-negotiable)

While a guidance consult is open for a chat, the bot **must not touch the VIP channel at all**:

| Action toward VIP | Allowed while `pending_guidance`? |
|-------------------|-----------------------------------|
| Send any text (including “wait” / filler) | **No** |
| `mark_as_read` / read receipt (blue ticks) | **No** |
| Typing indicator (`send_chat_action`) | **No** |
| Reengagement messages | **No** |
| Approval delivery / auto-send of the draft | **No** |
| `save_example` of the gap draft | **No** |

**What does happen:** after the silence timer, LLM runs, detects gap, bot DMs **Diana only**, stores `pending_guidance`, finishes the timer. VIP sees no bot activity until one of:

1. **Diana answers** the consult → distill → regen → normal path (approval draft or deliver).
2. **Diana taps a concrete action** (`g:use_draft` / `g:skip`).
3. **Timeout (12h)** → same as `g:use_draft`: open a **normal draft** (`pending_approval` + notify) if supervised, or deliver draft if autonomous — i.e. re-enter the existing draft/send pipeline, not a special VIP message.
4. **Owner (Diana) writes in the Business chat** → guidance `superseded`; human already owns the thread.
5. **VIP sends another message** → `reply_gen` bumps; open guidance becomes stale (do not deliver the old turn).

**Implementation invariant:** open-guidance path must never call `deliver_vip_response`, `mark_as_read`, or `simulate_typing`. Read/typing only occur later inside the normal delivery path after Diana resolved or timeout entered the normal draft/send pipeline.

---

## 1. Reality check vs pre-design

### 1.1 What the three existing flows actually do today

| Flow | Pre-design claim | Verdict | Code reality |
|------|------------------|---------|--------------|
| **Approval** | Every draft in supervised mode; approve/fix/regen; final text as few-shot by topic | **Partial** | Supervised = `APPROVAL_MODE and not auto_send(vip)` (`handlers/timer.py`). `save_example` runs **before** Diana acts (`status=pending`). Few-shots only after `reviewed` + `good\|corrected\|diana_manual` (`get_few_shots`). Per-VIP `auto_send` skips the gate. |
| **Escalation** | Keywords or `escalado_humano`; Diana replies manually; nothing structured | **Partial / storage wrong** | Keyword path pre-LLM (`business.py`); LLM path in `timer.py`. Structured `escalation_events` + triage UI (`e:valid` / `e:fp` / `e:gen`). FPs inject via `build_escalation_fp_block`. Manual reply only after `valid`. |
| **Note** | 📝 free text; raw per-user; no topic analysis | **Accurate** | `a:note` → `awaiting_note` → `MemoryService.add_note`. Optional `_regen_approval_variant` when from draft. Chat-scoped, not doctrine. |

**Conclusion:** Pre-design correctly identifies a missing fourth flow. Claims about approval/escalation need the corrections above when documenting or implementing.

### 1.2 Actual VIP path (anchor)

```
business inbound (VIP)
  → keyword escalate? → escalate_to_diana; STOP
  → schedule auto_reply (gen tracked)
       → get_diana_response
       → topic escalado_* (and not known FP)? → escalate; STOP
       → save_example (or synthetic id)
       → supervised? pending_approval + notify
         else deliver (+ notify if confidence < 70)
```

Guidance hooks **after** a successful LLM parse, **before** `save_example` / approval / deliver, and **after** escalation-topic handling (escalation wins).

### 1.3 Prompt assembly today (`get_diana_response`)

```
base_prompt
+ temporal_block
+ memory_block          # notes + facts (UNTRUSTED wrapper)
+ few_shots             # max 3 reviewed examples by topic
+ escalation_fp_block
+ optional no_escalation_block
+ JSON format + style rules
```

`DIANA_RESPONSE_SCHEMA`: `{response, confidence, topic}` only. `confidence` does **not** open a consult — only autonomous post-send notify when `< CONFIDENCE_THRESHOLD` (70).

### 1.4 Persistence facts the pre-design understated

| State | Persisted in `diana_runtime.json`? |
|-------|-------------------------------------|
| `pending_approval`, `pending_escalations`, timers meta, `reply_gen`, history (active) | Yes |
| `awaiting_note`, `awaiting_correction`, `awaiting_admin_note` | **No** (runtime-only) |
| Reengagement blocks on | `timers` + **`pending_approval` only** — **not** escalations |

Guidance must extend runtime snapshot, recovery, data_pause, sandbox cleanup, and **reengagement block** explicitly.

---

## 2. Goals / non-goals

### Goals

- Explicit LLM signal `knowledge_gap` + `gap_question` (not low confidence).
- Before asking Diana: try to resolve against stored `topic_policies`.
- DM consult UI with free-text answer capture (pattern of `awaiting_note`).
- Distill answer → durable policy with high priority.
- Inject policies as **instructions** (not few-shots) on every relevant LLM call.
- Regenerate VIP draft after doctrine is known; re-enter normal send path.
- Feature flag + strict prompt criteria to avoid noise.
- Respect module boundaries: logic in `services/`, Telegram I/O in `handlers/`.

### Non-goals (first delivery)

- Vector/semantic search for policies (keyword + topic match is enough).
- Changing escalation keywords or approval UX.
- Weight columns on `examples` table.
- Full policy editor UI (minimal list/delete is enough).
- Any VIP-side signal while waiting (messages, read receipts, typing) — see §0.1 freeze.
- Sandbox training of real policies.

---

## 3. Architecture decisions (locked)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| New flow vs overload note/approval | **New flow** (`g:`, `pending_guidance`) | Different intent: doctrine, not personal note or draft edit |
| Detection signal | **`knowledge_gap` + `gap_question` in LLM JSON** | `confidence` already means “generic / unsure wording”, not “missing policy” |
| Precedence after LLM | **1) escalation topic → 2) knowledge_gap → 3) approval/deliver** | Crisis/payment escalation must not become a doctrine Q&A |
| Policy storage | **New tables in `diana_training.db`** | Same as `escalation_events` / `promo_informed` |
| Service module | **`services/knowledge.py`** | Handlers stay I/O-only |
| Injection weight | **Dedicated prompt block labeled as mandatory instructions** | Stronger than competing for `MAX_FEW_SHOTS=3` slots |
| Injection order | After `memory_block`, before `few_shots` | Notes = person; policies = constitutional doctrine; few-shots = style |
| First-slice training side-effect | **Policy only** (no auto few-shot from distill) | Avoid noisy examples; tone still comes from regen + normal approval |
| When gap fires | **Do not `save_example` yet** | Avoid weak drafts becoming pending few-shot candidates |
| Runtime state | **`pending_guidance` persisted**; `awaiting_guidance_answer` not | Same pattern as escalations vs note await |
| Callback prefix | **`g:`** | Free; keep 3-part `g:action:id` |
| Feature flag | **`KNOWLEDGE_GAP_ENABLED` default False** until measured | Pre-design guardrail kept |
| Timeout | **`GUIDANCE_TIMEOUT_HOURS = 12`** → same as `g:use_draft` (normal draft/send pipeline) | Product-locked |
| VIP freeze while pending | **Zero VIP I/O** (no msg, no read, no typing) | Product-locked §0.1 |
| Reengagement | **Block when `pending_guidance` for chat** | Part of freeze; stronger than escalations today |
| Sandbox | **No real policy write; no real consult to Diana** (or synthetic-only offline path) | Mirror notes/escalation persist gates |
| Autonomous VIPs (`auto_send`) | After distill+regen: **deliver directly**; optional low-conf notify | Pre-design assumed always approval; real code has per-VIP auto_send |
| Supervised VIPs | After distill+regen: **`pending_approval` + notify** | Same as `_generate_from_escalation` |
| Owner answers VIP in Business while guidance open | **Mark guidance `superseded` and drop pending** | Owner takeover = human already handled |

---

## 4. Detection signal

### 4.1 Schema extension (`services/llm.py`)

```json
{
  "response": "tentative draft (may still be used)",
  "confidence": 85,
  "topic": "limites_contenido",
  "knowledge_gap": true,
  "gap_question": "Un usuario pide videollamada privada fuera de tarifas. ¿Cómo debo manejarlo?"
}
```

- Extend `DIANA_RESPONSE_SCHEMA` (`additionalProperties: False` remains).
- Required: keep `response`, `confidence`, `topic`. Make `knowledge_gap` / `gap_question` optional in schema if provider is strict; normalize missing → `false` / `""`.
- Return type of `get_diana_response` should grow carefully (tuple extension or small dataclass) so call sites stay explicit.

### 4.2 Prompt criteria (strict)

Mark `knowledge_gap=true` **only** when:

- Situation is **new / no clear rule** in system prompt, notes, or known policies; **or**
- Contradictory options (e.g. exception to a stated limit); **or**
- Commercial/operational commitment the model must not invent.

**Never** for routine FAQ already covered by `TOPIC_MAP` / schedule / published prices, pure tone uncertainty (use low `confidence` instead), or cases that must escalate (`escalado_humano`).

If `knowledge_gap` and escalation topic both set → **escalation wins**; ignore gap.

### 4.3 Flag

```python
KNOWLEDGE_GAP_ENABLED = False          # config.py — flip after dry-run metrics
GUIDANCE_TIMEOUT_HOURS = 12
GUIDANCE_POLICY_PRIORITY = 100         # default priority for distilled policies
```

When flag is off: ignore gap fields; behavior identical to today.

---

## 5. End-to-end flow

```
get_diana_response → (response, confidence, topic, knowledge_gap, gap_question, failure)

if failure / no response → existing LLM failure path
if is_llm_escalation_topic(topic) and not known FP → escalate (existing); STOP
if KNOWLEDGE_GAP_ENABLED and knowledge_gap and gap_question:
      matched = knowledge.match_policies(topic, last_user_text, gap_question)
      if matched:
          # inject already happens on next call if we re-call with force; or
          # re-call get_diana_response (policies always injected when match exists)
          regenerate once with policies in prompt → fall through to save/approve/deliver
          # anti-loop: only one auto-retry per timer fire
      else:
          create guidance_requests row (status=pending)
          pending_guidance[gid] = {chat_id, bc_id, username, gen, topic,
                                   gap_question, draft_response, confidence,
                                   created_at, draft_message_id?}
          notify Diana (g: UI)
          finish timer WITHOUT save_example / WITHOUT send
          STOP
else:
      existing save_example → approval | deliver
```

### 5.1 Diana DM (consult, not approval)

Message shape (distinct copy from approval/escalation):

```
🧭 Necesito tu criterio (zona gris)

VIP: @{username} ({chat_id})
Tema: {topic}

Pregunta:
{gap_question}

Contexto (últimos mensajes):
…

Borrador tentativo (no enviado):
"{draft_response}"
```

Buttons (`g:`):

| Action | Callback | Behavior |
|--------|----------|----------|
| Responder | `g:answer:{id}` | Set `awaiting_guidance_answer[admin]=id`; prompt free text |
| Usar borrador | `g:use_draft:{id}` | Skip distill; proceed with draft via normal approval/deliver; mark request `skipped` |
| Yo me encargo | `g:skip:{id}` | Close without send; Diana handles VIP manually; status `skipped` |

### 5.2 Free-text capture

Router order for admin DM (insert **before** note/correction):

```
handle_admin_note
→ handle_diana_guidance_answer   # NEW
→ handle_diana_note
→ handle_diana_correction
```

Mutual exclusion (same as note/fix):

- `g:answer` clears `awaiting_note` / `awaiting_correction`.
- `a:note` / `a:fix` / approve / training actions clear `awaiting_guidance_answer` with prompt restore if needed.

### 5.3 Distillation

`knowledge.distill_guidance(gap_question, diana_answer, context, topic_hint) →`:

```json
{
  "topic": "limites_contenido",
  "policy_summary": "1–3 sentence reusable rule, Diana voice",
  "example_response": "how Diana would answer this case",
  "keywords": ["videollamada", "privado", "fuera de tarifa"],
  "priority": 100
}
```

- Separate LLM call with a small strict schema (not the VIP persona schema).
- On distill failure: keep raw answer; save policy with `policy_summary = raw` truncated; still proceed (degraded but usable).
- Persist `source_question` + `source_answer_raw` for audit.

### 5.4 After distill

1. Insert `topic_policies` (`is_active=1`, high priority).
2. Link `guidance_requests.policy_id`, status `answered`.
3. Clear `pending_guidance` / await state.
4. If `reply_gen[chat_id] != gen` → stale; notify Diana “VIP wrote again; consult closed”.
5. Else `get_diana_response` (policies now inject) →  
   - supervised → `save_example` + `pending_approval` + `notify_diana_approval`  
   - autonomous → `save_example` + deliver (+ low-conf notify if needed)

Mirrors `_generate_from_escalation` in `handlers/callbacks/escalation.py`.

### 5.5 Timeout (product-locked)

Background check (piggyback reengagement scheduler or a small interval task):

- If `pending_guidance` older than **`GUIDANCE_TIMEOUT_HOURS` (12)** and still open:
  - status `timeout`
  - fall back: **identical to `g:use_draft`** — re-enter the **existing** pipeline with the stored tentative `draft_response`:
    - supervised → `save_example` + `pending_approval` + `notify_diana_approval` (normal draft UI Diana already knows)
    - autonomous → deliver via normal `deliver_vip_response` (read/typing only **now**, not during the wait)
  - notify Diana that timeout fired and a normal draft/send path was opened
- During the entire wait until that moment: **VIP freeze §0.1** still holds.

---

## 6. Data model

### 6.1 `topic_policies`

```sql
CREATE TABLE IF NOT EXISTS topic_policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    keywords TEXT,                 -- JSON list
    policy_summary TEXT NOT NULL,
    example_response TEXT,
    priority INTEGER DEFAULT 100,  -- higher = stronger / earlier in block
    source_question TEXT,
    source_answer_raw TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_topic_policies_topic
  ON topic_policies(topic) WHERE is_active = 1;
```

### 6.2 `guidance_requests`

```sql
CREATE TABLE IF NOT EXISTS guidance_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    username TEXT,
    ts TEXT NOT NULL,
    topic TEXT,
    gap_question TEXT NOT NULL,
    context TEXT,                  -- JSON history slice
    draft_response TEXT,
    diana_answer_raw TEXT,
    policy_id INTEGER,
    status TEXT DEFAULT 'pending', -- pending|answered|skipped|timeout|superseded
    resolved_at TEXT
);
```

Init via `knowledge.init_schema(conn)` called from `training.init_db()` (same pattern as `promo_info` / `chat_history`).

### 6.3 Runtime (`state.py`)

```python
pending_guidance: dict[int, dict] = {}
# id → {chat_id, bc_id, username, gen, topic, gap_question,
#        draft_response, confidence, created_at, notify_message_id?}

awaiting_guidance_answer: dict[int, int] = {}  # admin_id → guidance_id
```

- Include `pending_guidance` in `_build_runtime_snapshot` / load / empty-file deletion condition / `_active_chat_ids`.
- Do **not** persist `awaiting_guidance_answer`.
- Recovery: after load, re-notify open guidances (or edit existing if `notify_message_id` known) so Diana can answer after restart.

---

## 7. Matching & injection

### 7.1 `match_policies(topic, *texts) -> list[policy]`

1. Load active policies ordered by `priority DESC`, `id DESC`.
2. Score:
   - exact topic match (normalized): +100
   - keyword hit in any of `texts` (gap question, last user message): +10 per distinct keyword
3. Keep policies with score ≥ 100 **or** ≥ 1 keyword hit (configurable floor).
4. Cap block size (e.g. top 5) to protect context window.

Important: LLM `topic` is free-form (`precio_vip` vs `precio`). **Never rely on exact topic alone** — keywords are mandatory for robust matching. Pre-LLM `TOPIC_MAP` remains only for few-shot selection.

### 7.2 Always inject on normal calls

Not only after a gap: every `get_diana_response` should:

```python
policies = knowledge.match_policies(topic_guess, last_user, *(optional))
policy_block = knowledge.build_policy_block(policies)
```

Assembly:

```
base + temporal + memory + policy_block + few_shots + escalation_fp + format
```

### 7.3 Block format (instruction weight)

```
POLÍTICAS DE DIANA (instrucciones vigentes — síguelas siempre;
tienen prioridad sobre tu criterio genérico; NO las contradigas):
  [limites_contenido] Regla: {policy_summary}
    Ejemplo de tono: "{example_response}"
```

Label deliberately as **regla/instrucción**, not “ejemplo aprendido”.

### 7.4 Anti-reask

- Before opening consult: if match non-empty → one regen with policies, no DM.
- After policy saved: subsequent gaps on same keywords should match and not re-ask.
- Optional later: if gap fires again with an active match, log metric `gap_despite_policy` for prompt tuning.

---

## 8. Integration map (files)

### New

| File | Role |
|------|------|
| `services/knowledge.py` | Schema, CRUD, match, distill, `build_policy_block` |
| `handlers/callbacks/guidance.py` | Notify, `g:` actions, free-text handler |
| `tests/unit/test_knowledge_*.py` | Match, distill, timer branch, router, reengage, timeout |

### Modify

| File | Change |
|------|--------|
| `config.py` | Flags, timeout, priority default |
| `services/llm.py` | Schema, parse, return gap fields, inject policy block |
| `handlers/timer.py` | Gap branch before save/approve/deliver |
| `state.py` | `pending_guidance`, await dict, runtime I/O |
| `handlers/callbacks/__init__.py` | Route `g:` |
| `handlers/router.py` | Free-text guidance capture |
| `services/training.py` | `knowledge.init_schema` from `init_db` |
| `services/reengagement.py` | `_has_pending_guidance` |
| `services/data_pause.py` | Clear pending guidance for chat |
| `services/sandbox.py` | Clear/synthetic gates |
| `handlers/recovery.py` | Restore + re-notify open guidances |
| `handlers/business.py` | Owner inbound → supersede open guidance for chat |
| `tests/conftest.py` | New tables in test DB |
| `AGENTS.md` | Fourth flow + `g:` prefix |

### Unchanged intentionally

- Escalation keyword lists
- Approval keyboard layout (except no coupling)
- Few-shot rating model (first slice)

---

## 9. Guardrails

| Guardrail | Mechanism |
|-----------|-----------|
| Noise | `KNOWLEDGE_GAP_ENABLED` + strict prompt rules + metrics log on every gap |
| Anti-reask | Match before DM; always inject policies |
| No global block | Only that VIP waits; other chats unaffected |
| Timeout | `GUIDANCE_TIMEOUT_HOURS` → use draft path |
| Stale gen | Abort post-answer regen if VIP messaged again |
| Mutual exclusion | One await mode for Diana DM at a time |
| Sandbox | No production policy pollution |
| data_pause | Drop guidance with other pending state |
| Reengage | Do not poke VIP while waiting for doctrine |
| Owner takeover | Supersede pending guidance |
| Audit | Raw Diana answer + distilled policy retained |
| Admin hygiene | Minimal `/politicas [topic]` list + `/borrar_politica <id>` (can ship in same WU or WU3) |

---

## 10. Work units (implementation)

Aligned with openspec-style slices; each independently testable.

### WU1 — Detection + persistence

- Schema `topic_policies` / `guidance_requests`
- `services/knowledge.py` CRUD + match + `build_policy_block` + distill stub/real
- LLM schema + prompt criteria + flag (no UI yet; gap can log-only when flag on)
- Tests: schema, match scoring, parse fields

### WU2 — Consult flow

- `pending_guidance` / await + runtime persistence
- `handlers/callbacks/guidance.py` + router wiring + mutual exclusion
- Timer branch: open consult, no send
- Reengage / data_pause / sandbox / recovery / owner supersede
- Tests: callback, free-text, stale gen, restart re-notify

### WU3 — Injection + regen + timeout + admin list

- Policy block in `get_diana_response`
- Post-answer distill → regen → approval/deliver
- Timeout fallback
- `/politicas`, `/borrar_politica`
- Tests: injection order, anti-reask, timeout, end-to-end happy path

---

## 11. Success criteria

1. Gray-zone VIP message with flag on and no policy → Diana gets **consult** DM (not approval-only).
2. Diana answers → policy stored → VIP draft regenerated under doctrine → supervised approval or autonomous send.
3. Same topic/keywords later → **no second consult**; policy appears in prompt as instruction.
4. Escalation topics never open guidance.
5. Restart mid-consult does not lose `pending_guidance`; Diana can still answer.
6. Reengagement does not fire for chats with open guidance.
7. Flag off → zero behavior change vs current main.

---

## 12. Product locks (confirmed)

| Topic | Lock |
|-------|------|
| VIP while waiting | **Total freeze** — no messages, no read receipt, no typing, no reengage (§0.1) |
| Timeout | **12 hours** |
| On timeout | Enter **normal draft path** (same as today’s approval drafts / autonomous send of that draft) — not a special VIP text |
| First slice knowledge | **Policy only** (no auto few-shot from distill) |
| After Diana answers | Regen with policy → supervised approval **or** autonomous deliver |

---

## 13. Diff summary vs pre-design

| Pre-design item | Final adjustment |
|-----------------|------------------|
| Escalation “nothing structured” | Document real `escalation_events` + FP injection; use as template |
| Always re-enter approval | Branch on supervised vs `auto_send` |
| Runtime: pending only | Also recovery re-notify; reengage block; owner supersede |
| `save_example` timing implicit | Explicit: **no** save while gap open |
| Matching by topic + keywords | Emphasize free-form topic risk; keyword scoring required |
| Service name knowledge | Confirmed `services/knowledge.py` |
| WU split 3 | Kept; WU2 expanded with reengage/sandbox/recovery/owner |
| Admin policy commands | Moved to WU3 as optional-but-recommended |
| Flag default | **False** until measured (pre-design suggested flag; we default off) |
