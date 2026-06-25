<!-- generated-by: gsd-doc-writer -->
# Architecture

## System overview

Diana Business Bot is a single-process Python application that uses Telegram's **Business Connection / Chat Automation** API to respond on Diana's behalf in VIP chats. Incoming business messages are routed through a central update handler, authorized against a JSON allowlist, deferred via cancellable asyncio timers, enriched with few-shot examples from SQLite, and sent to the DeepSeek API. Responses are delivered with human-like timing (read receipts, typing indicators, randomized pauses). In supervised mode, Diana approves or corrects drafts before they reach users.

**Primary inputs:** Telegram business messages, admin DMs, callback queries  
**Primary outputs:** Business messages to VIP chats, admin notifications, SQLite training records  
**Style:** Monolithic async event loop with timer-based deferred responses and human-in-the-loop approval

## Component diagram

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
│                    diana.py                                  │
├──────────────────┬──────────────────┬───────────────────────┤
│ _handle_business │ auth_users       │ handle_callback /       │
│ _message         │ handlers           │ handle_diana_correction │
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
│ JSON response       │              │ diana_training.db       │
└────────┬────────────┘              └─────────────────────────┘
         │
         ▼
┌─────────────────────┐
│ deliver_vip_response│
│ read → pause → type │
│ → send_message      │
└─────────────────────┘
```

## Data flow

A typical VIP message flows through the system as follows:

1. **Ingress** — Telegram delivers a `business_message` update. `process_update()` in `diana.py` routes it to `_handle_business_message()`.
2. **Authorization** — The sender is resolved via `_resolve_vip_id()`. `auth_users.is_authorized()` checks `diana_authorized_users.json`.
3. **Escalation check** — `needs_escalation()` scans for keywords (payments, crisis, etc.). Matches are logged to `diana_escalaciones.txt` and skip auto-reply.
4. **Timer scheduling** — A cancellable `asyncio` task (`auto_reply`) is scheduled. In supervised mode, delay is `SILENCE_MINUTES`; otherwise a random range between `RESPONSE_DELAY_MIN` and `RESPONSE_DELAY_MAX`.
5. **LLM call** — `get_diana_response()` builds a system prompt from `DIANA_SYSTEM_PROMPT`, injects few-shots from `get_few_shots()`, and posts to DeepSeek. The model returns JSON: `response`, `confidence`, `topic`.
6. **Approval gate** — If `APPROVAL_MODE` is true or confidence is below `CONFIDENCE_THRESHOLD`, `notify_diana_approval()` sends a draft to Diana's admin DM with inline approve/fix buttons.
7. **Delivery** — `deliver_vip_response()` runs the human-like chain: `mark_as_read` → pause → `simulate_typing` → `send_message` with `business_connection_id`.
8. **Training** — Examples are saved via `save_example()`. Diana rates or corrects via callback handlers; reviewed examples feed future few-shots.

## Key abstractions

| Abstraction | Description | Location |
|-------------|-------------|----------|
| `process_update()` | Central router for all Telegram update types | `diana.py` |
| `_handle_business_message()` | VIP message ingestion, timer management | `diana.py` |
| `get_diana_response()` | DeepSeek LLM call with few-shot injection | `diana.py` |
| `deliver_vip_response()` | Human-like message delivery chain | `diana.py` |
| `auto_reply()` | Deferred reply task with generation tracking | `diana.py` |
| `auth_users.configure()` | Allowlist module initialization | `auth_users.py` |
| `auth_users.is_authorized()` | VIP authorization check | `auth_users.py` |
| `init_db()` / `save_example()` | SQLite training persistence | `diana.py` |
| `DIANA_SYSTEM_PROMPT` | Persona, voice rules, escalation policy | `diana.py` |

## Directory structure rationale

The project uses a **flat layout** — all application code lives in the repository root with no `src/` package.

```
diana/
├── diana.py              # Main bot (~1150 lines) — routing, LLM, delivery, training
├── auth_users.py         # VIP allowlist CRUD and admin commands
├── .env.example          # Environment variable template
├── docs/                 # Project documentation
├── diana_authorized_users.json   # Runtime: VIP allowlist (gitignored)
├── diana_state.json              # Runtime: business connection IDs (gitignored)
├── diana_training.db             # Runtime: SQLite training store (gitignored)
└── venv/                         # Python virtual environment (not committed)
```

**Why flat?** The bot is a focused two-module script with no package boundaries. Runtime data files (`diana_*.json`, `diana_*.db`, logs) colocate with code for simple deployment — copy the directory, set `.env`, run `python diana.py`.

## Callback routing

Inline keyboard callbacks use prefixed `callback_data` values:

| Prefix | Purpose |
|--------|---------|
| `a:` | Approval mode — approve or fix draft before sending |
| `t:` | Training feedback — rate bot responses |
| `au:` | Authorized user management — delete from allowlist |

## Persistence model

| Store | Format | Managed by |
|-------|--------|------------|
| VIP allowlist | `diana_authorized_users.json` | `auth_users.py` |
| Business connections | `diana_state.json` | `diana.py` (`_save_connections_state`) |
| Training examples | `diana_training.db` (SQLite `examples` table) | `diana.py` (`init_db`, `save_example`) |
| Escalation audit | `diana_escalaciones.txt` | `diana.py` (`log_escalation`) |
| Application log | `diana_business.log` | Python `logging` module |