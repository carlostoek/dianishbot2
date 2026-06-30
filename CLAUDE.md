# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## CRITICAL
Never read training data unless the user explicitly requests it.

## Build & Run

```bash
source venv/bin/activate
python diana.py                           # Start the bot (long-polling)
pip install "python-telegram-bot>=21.0" python-dotenv aiohttp  # Runtime deps
pip install telethon                      # Extractor dependency
python extractor.py list                  # List exportable chats
python extractor.py export --chat <id> --format training --import-db
```

No build step, linter, formatter, or CI pipeline. Automated tests: `PYTHONPATH=. pytest tests/` (141 tests — see docs/TESTING.md). Manual validation still required against a Telegram test bot.

## Architecture

**Telegram Business Chat Automation bot** — covers VIP conversations on Diana's Telegram account using DeepSeek LLM with human-like delivery, supervised training, and per-user memory.

### Modular v2 layout (flat, no `src/` package)

```
diana.py              → Composition root — wiring, logging, Application setup, polling
config.py             → All constants, env vars, DIANA_SYSTEM_PROMPT, escalation keywords
state.py              → In-memory runtime dicts shared across handlers (history, timers, pending_approval)
auth_users.py         → VIP allowlist CRUD, admin slash commands (/usuarios, /nota, /notas, /borrar_notas), callback routing (au: prefix)
handlers/
  router.py           → process_update() — central Telegram update dispatch
  business.py         → _handle_business_message() — VIP message ingestion, escalation, timer management
  timer.py            → auto_reply() — deferred response task with cancellation and generation tracking
  callbacks.py        → Approval (a:), training feedback (t:), correction flows
services/
  llm.py              → get_diana_response(), raw_call() — DeepSeek API with memory + few-shot injection
  delivery.py         → deliver_vip_response() — read receipt → pause → typing → send (human-like timing)
  training.py         → SQLite persistence for training examples (examples table) and few-shot retrieval
  memory.py           → MemoryService — per-user facts + manual notes (user_memory table), background extraction
  telethon_import.py  → Shared Telethon fetch + messages_to_history (lazy import; optional at bot startup)
  history_backfill.py → VIP history queue + hourly asyncio scheduler → seed_chat_history (chat_history only)
extractor.py          → Standalone Telethon tool for chat history export; delegates fetch to telethon_import
```

### Module boundary rule

- `handlers/` → Telegram I/O only (routing, messages, callbacks, timers)
- `services/` → Business logic (LLM, delivery, persistence, memory)
- `auth_users.py` → VIP allowlist only
- `config.py` → Constants, prompts, keywords
- `diana.py` → Composition root; wires `db` and `memory_service` into service modules at startup

### Data flow for a VIP message

1. `process_update()` routes `business_message` → `_handle_business_message()`
2. `auth_users.is_authorized()` checks `diana_authorized_users.json`
3. `needs_escalation()` scans `ESCALATE_KEYWORDS`; matches skip auto-reply, log to `diana_escalaciones.txt`, notify Diana via DM
4. Cancellable `asyncio` timer schedules `auto_reply()` — delay depends on `APPROVAL_MODE`
5. `get_diana_response()` builds prompt: system + memory context + few-shots → DeepSeek returns JSON (`response`, `confidence`, `topic`)
6. **Supervised mode** (`APPROVAL_MODE=True`): draft sent to admin DM with approve/fix/regen/nav buttons; Diana can browse variants (`Borrador k/n`) before delivery. **Autonomous mode**: confidence < `CONFIDENCE_THRESHOLD` triggers Diana notification; otherwise delivers directly
7. `deliver_vip_response()` runs read receipt → random pause → simulated typing → send via `business_connection_id`
8. Response saved to `diana_training.db`. Background `MemoryService.extract_and_update()` runs fact extraction

### Two operating modes

| Mode | Config | Behavior |
|------|--------|----------|
| Supervised | `APPROVAL_MODE=True` | Every draft goes to admin DM for approve/fix before delivery. Delay = `SILENCE_MINUTES`. |
| Autonomous | `APPROVAL_MODE=False` | Bot sends directly. Delay = random(`RESPONSE_DELAY_MIN`–`RESPONSE_DELAY_MAX`). Low-confidence triggers `notify_diana()`. |

### Callback routing

Inline callbacks use prefixed `callback_data` (always 3 parts: `prefix:action:id`):

| Prefix | Actions | Purpose |
|--------|---------|---------|
| `a:` | `approve`, `fix`, `note`, `regen`, `prev`, `next` | Supervised approval — Enviar/Corregir/Nota on **selected** variant; regen appends variant; prev/next navigate |
| `t:` | `good`, `bad`, `fix` | Autonomous training feedback |
| `au:` | `del` | VIP allowlist management |

Examples: `a:approve:<id>`, `a:regen:<id>`, `a:prev:<id>`, `a:next:<id>`, `t:good:<id>`, `au:del:<id>`.

### Admin commands (Diana DM)

| Command | Purpose |
|---------|---------|
| `/usuarios` | VIP allowlist — list, add (forward user), delete |
| `/notas <user_id>` | View Diana notes + auto-extracted facts for a VIP |
| `/nota <user_id> <text>` | Add manual note (injected at top of LLM context) |
| `/borrar_notas <user_id>` | Clear all notes for a VIP |
| `/cancelar_nota` | Cancel in-progress note capture; approval draft stays pending |
| `/sandbox on <chat_id>` | Test mode — frozen profile memory, no persistence; VIP delivery still active |
| `/sandbox off <chat_id>` | Deactivate sandbox and clear chat RAM |
| `/sandbox perfil <name>` | Switch profile on focused chat (`nuevo`, `cercano`, `distante`, `intenso`, `vip_largo`, `inyeccion_previa`) |
| `/sandbox perfiles` \| `/sandbox estado` \| `/sandbox reset` | List profiles, active sessions, clear RAM (session stays on) |

Notes also attachable via 📝 **Nota** on approval drafts (disabled in sandbox). Command order in `auth_users.py`: `/notas` before `/nota ` (prefix guard).

### Generation tracking

`reply_gen[chat_id]` is incremented on each new user message. `auto_reply()` and `deliver_vip_response()` check it at multiple yield points — if another message arrived in the meantime, the stale delivery cancels itself before sending.

### Persistence

- `diana_authorized_users.json` — VIP allowlist (runtime, gitignored)
- `diana_state.json` — business connection IDs for recovery across restarts
- `diana_training.db` — SQLite with two tables: `examples` (training data) and `user_memory` (per-user facts)
- `diana_escalaciones.txt` — escalation audit log
- `diana_business.log` — application log

### Unauthorized observation

When `OBSERVE_UNAUTHORIZED=True`, messages from non-VIP chats are logged for context but not auto-answered. If Diana manually replies, her response is captured as `save_observed_example()` with `diana_manual` rating — filtered by `SKIP_OBSERVED_TOPICS` to exclude transactional FAQs.
