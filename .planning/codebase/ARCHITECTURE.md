<!-- refreshed: 2026-06-25 -->
# Architecture

**Analysis Date:** 2026-06-25

## System Overview

```text
┌─────────────────────────────────────────────────────────────┐
│                   Telegram (Business Mode)                   │
│   VIP chats ←→ Diana's account via Chat Automation          │
├──────────────────┬──────────────────┬───────────────────────┤
│  VIP messages    │  Diana manual    │  Admin DM (/usuarios) │
│  (authorized)    │  replies         │  callbacks            │
└────────┬─────────┴────────┬─────────┴──────────┬────────────┘
         │                  │                     │
         ▼                  ▼                     ▼
┌─────────────────────────────────────────────────────────────┐
│              process_update() — central router               │
│                    `diana.py`                                │
├──────────────────┬──────────────────┬───────────────────────┤
│ _handle_business │ auth_users       │ handle_callback /       │
│ _message         │ handlers         │ handle_diana_correction │
└────────┬─────────┴──────────────────┴──────────┬────────────┘
         │                                        │
         ▼                                        ▼
┌─────────────────────┐              ┌─────────────────────────┐
│ Timer + auto_reply  │              │ Admin approval / rating │
│ get_diana_response  │              │ notify_diana_*          │
└────────┬────────────┘              └────────────┬────────────┘
         │                                        │
         ▼                                        ▼
┌─────────────────────┐              ┌─────────────────────────┐
│ DeepSeek API (LLM)  │              │ SQLite + few-shot block │
│ JSON response       │              │ `diana_training.db`     │
└────────┬────────────┘              └─────────────────────────┘
         │
         ▼
┌─────────────────────┐
│ deliver_vip_response│
│ read → pause → type │
│ → send_message      │
└─────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| Main bot & orchestration | Polling, routing, timers, LLM calls, delivery chain | `diana.py` |
| Auth allowlist | VIP user CRUD, admin commands, callback deletes | `auth_users.py` |
| Training store | Persist examples, few-shot retrieval, ratings | `diana.py` (SQLite helpers) |
| Persona prompt | Diana voice, escalation rules, response style | `DIANA_SYSTEM_PROMPT` in `diana.py` |
| State persistence | Business connections, authorized users | `diana_state.json`, `diana_authorized_users.json` |

## Pattern Overview

**Overall:** Single-process async event loop with timer-based deferred responses and human-in-the-loop approval

**Key Characteristics:**
- Monolithic two-module layout — nearly all logic in `diana.py` (~1150 lines)
- Telegram Business Connection as the integration surface (not standard user-bot DMs for VIP flow)
- Deferred reply via `asyncio.create_task` + cancellable timers per chat
- LLM returns structured JSON (response, confidence, topic) for routing and training
- Few-shot injection from reviewed SQLite examples before each LLM call

## Layers

**Update routing layer:**
- Purpose: Dispatch all Telegram updates to the correct handler
- Location: `process_update()` in `diana.py`
- Contains: Callback routing, admin DM handling, business connection lifecycle, business messages
- Depends on: `auth_users`, in-memory state dicts
- Used by: `TypeHandler(Update, process_update)` registered on `Application`

**Business message layer:**
- Purpose: Ingest VIP/Diana messages, manage history, schedule coverage
- Location: `_handle_business_message()` in `diana.py`
- Contains: Authorization checks, escalation keyword detection, timer management, observation mode
- Depends on: `auth_users.is_authorized()`, `history`, `timers`, `reply_gen`
- Used by: `process_update()` for `business_message` and `edited_business_message`

**LLM & delivery layer:**
- Purpose: Generate response, simulate human presence, send to VIP
- Location: `get_diana_response()`, `auto_reply()`, `deliver_vip_response()` in `diana.py`
- Contains: DeepSeek HTTP call, read receipts, typing simulation, message send
- Depends on: `history`, SQLite few-shots, `aiohttp`, Telegram bot API
- Used by: Timer expiry path and approval/correction paths

**Training & admin layer:**
- Purpose: Supervised mode, feedback capture, few-shot learning
- Location: `notify_diana_approval()`, `notify_diana()`, `handle_callback()`, `handle_diana_correction()` in `diana.py`
- Contains: Inline keyboard workflows, rating updates, correction capture
- Depends on: `pending_approval`, `awaiting_correction`, SQLite `examples` table
- Used by: `APPROVAL_MODE=True` path (current default)

## Data Flow

### Primary VIP message → response path

1. VIP sends message on Diana's business chat → `process_update()` receives `business_message` (`diana.py:1061`)
2. `_handle_business_message()` resolves sender, checks `auth_users.is_authorized()` (`diana.py:916-1008`)
3. Message appended to `history[chat_id]`; escalation keywords short-circuit to `log_escalation()` (`diana.py:992-998`)
4. Previous timer cancelled; new `asyncio.Task` runs `auto_reply()` after delay (`diana.py:1000-1008`)
5. `auto_reply()` calls `get_diana_response()` → DeepSeek with system prompt + few-shots + history (`diana.py:499-560`)
6. Example saved to SQLite; if `APPROVAL_MODE`, draft sent to admin via `notify_diana_approval()` (`diana.py:881-892`)
7. On admin approve → `deliver_vip_response()`: read receipt → random pause → typing → `send_message` (`diana.py:613-656`)

### Diana manual takeover path

1. Diana (owner) sends message in same business chat → detected via `sender_id == owner_id` (`diana.py:944-965)
2. Active timer cancelled; message recorded as assistant turn in `history`
3. If `OBSERVE_UNAUTHORIZED` and chat was observed, Diana's reply saved as `diana_manual` training example (`diana.py:953-964`)

### Unauthorized observation path

1. Non-authorized sender message logged and stored in `history` + `chat_meta` (`diana.py:969-976`)
2. No auto-reply; bot learns from Diana's later manual responses in that chat

**State Management:**
- Conversation history: in-memory `history` dict (lost on restart)
- Business connections: `connections` dict persisted to `diana_state.json`
- Timer generation: `reply_gen` prevents stale timer deliveries
- Training data: durable in `diana_training.db`

## Key Abstractions

**Coverage timer:**
- Purpose: Delay bot response to mimic human availability
- Examples: `auto_reply()`, `timers`, `reply_gen` in `diana.py`
- Pattern: Cancellable `asyncio.Task` per chat; superseded messages bump `reply_gen`

**Few-shot learning block:**
- Purpose: Inject approved/corrected past responses into system prompt
- Examples: `get_few_shots()`, `build_few_shot_block()`, `TOPIC_MAP` / `guess_topic()` in `diana.py`
- Pattern: Topic-classify last user message → query SQLite → append to `DIANA_SYSTEM_PROMPT`

**Human presence simulation:**
- Purpose: Blue checkmarks and typing indicator before send
- Examples: `mark_as_read()`, `simulate_typing()`, `deliver_vip_response()` in `diana.py`
- Pattern: Random delays + proportional typing duration (8 chars/sec, 2–15s cap)

## Entry Points

**Process entry:**
- Location: `main()` in `diana.py`
- Triggers: `python diana.py` or `python3 diana.py`
- Responsibilities: Validate env, init DB, configure auth, build `Application`, start polling

**Update entry:**
- Location: `process_update()` via `TypeHandler(Update, ...)`
- Triggers: Any Telegram update in allowed types
- Responsibilities: Route to business, admin, callback, or correction handlers

## Architectural Constraints

- **Threading:** Single-threaded asyncio event loop; SQLite `check_same_thread=False` for safety
- **Global state:** Module-level dicts (`history`, `timers`, `connections`, `pending_approval`, `db`) — not multi-instance safe
- **Polling only:** No webhook server; one bot process per token
- **Business API dependency:** Requires Telegram Business / Chat Automation; standard bot-only mode insufficient for VIP flow

## Anti-Patterns

### Monolithic god module

**What happens:** ~1150 lines of routing, LLM, DB, training, and delivery live in `diana.py`
**Why it's wrong:** Hard to test, extend, or run multiple concerns in parallel without merge conflicts
**Do this instead:** Extract handlers (business, admin, training) and services (llm, delivery, db) into subpackages when adding features

### In-memory conversation history

**What happens:** `history` dict is not persisted; restart loses all active chat context
**Why it's wrong:** Bot may respond without context after crash/redeploy; inconsistent VIP experience
**Do this instead:** Persist recent history per chat (SQLite or Redis) with TTL aligned to `MAX_HISTORY`

### Hardcoded admin and seed IDs

**What happens:** `ADMIN_USER_ID`, `VIP_USERS_SEED` are constants in `diana.py`
**Why it's wrong:** Requires code change and redeploy for different environments
**Do this instead:** Move to env vars or config file loaded at startup

## Error Handling

**Strategy:** Log and continue — most integration errors are caught, logged, and return `None` or skip delivery

**Patterns:**
- DeepSeek failures → `log.error`, return `(None, 0, "general")` from `get_diana_response()`
- Telegram send failures → `log.error` in `deliver_vip_response()`, approval marked stale if gen mismatch
- JSON parse fallback → if DeepSeek ignores JSON mode, use raw text at confidence 100 (`diana.py:550-552`)
- Missing env at startup → `SystemExit` with Spanish message listing missing vars

## Cross-Cutting Concerns

**Logging:** File + stdout via `logging`; escalation audit in separate text file
**Validation:** Authorization via `auth_users`; escalation via keyword list `ESCALATE_KEYWORDS`
**Authentication:** Allowlist-based VIP gate; admin-only callbacks and `/usuarios` commands

---

*Architecture analysis: 2026-06-25*