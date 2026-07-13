# AGENTS.md

This file provides persistent instructions for AI coding agents (Grok, Claude Code, Cursor, Codex, etc.) working on this repository.

**Project**: Diana Business Bot — Telegram Business Chat Automation for VIP conversations using LLM (DeepSeek/Anthropic) with human-like behavior, supervised approval mode, per-user memory, and few-shot training.

---

## CRITICAL RULES

- **Never read or expose training data** (`diana_training.db` contents, few-shot examples, or real user conversations) unless the user explicitly asks.
- Always respect the **module boundaries** (see Architecture section).
- Prefer **small, focused changes** over large refactors in a single step. Explain the plan before making modifications.
- When modifying LLM-related code (`services/llm.py`, prompt construction, memory injection), be extremely careful with context and token usage.
- Update this file (`AGENTS.md`) and/or `CLAUDE.md` when you make significant architectural or behavioral changes.
- Never commit `.env`, `diana_authorized_users.json`, or real training data.

---

## Project Overview

**Diana Business Bot** automates high-value conversations on Diana's Telegram account using:
- DeepSeek (primary) or Anthropic as LLM
- Human-like delivery timing (read receipts + typing + random delays)
- **Supervised mode** (approval gate) or **Autonomous mode**
- Per-user memory + few-shot examples from real conversations
- Sandbox mode for safe testing with frozen profiles

The bot uses Telegram **Business Connections** (Chat Automation API).

---

## Architecture & Module Boundaries

**Strict separation of concerns** (do not violate these):

| Layer          | Responsibility                          | Files / Folders                  | Rules |
|----------------|-----------------------------------------|----------------------------------|-------|
| **Entry**      | Wiring and startup                      | `diana.py`                       | Only composition root |
| **Config**     | Constants, prompts, keywords            | `config.py`                      | All magic numbers and `DIANA_SYSTEM_PROMPT` live here |
| **State**      | Runtime in-memory state                 | `state.py`                       | Shared dicts (history, timers, pending approvals) |
| **Auth**       | VIP allowlist + admin commands          | `auth_users.py`                  | Only VIP management and `/usuarios`, `/nota`, etc. |
| **Handlers**   | Telegram I/O only                       | `handlers/`                      | Routing, business messages, timers, callbacks. **No business logic** |
| **Services**   | Core business logic                     | `services/`                      | LLM, delivery, training, memory, reengagement, knowledge |
| **Tools**      | Standalone utilities                    | `extractor.py`                   | Telethon-based chat export |

**Data flow for a VIP message** (memorize this):
1. `process_update()` → `business.py`
2. Authorization check
3. Escalation detection
4. Timer scheduling (`timer.py`)
5. `get_diana_response()` — prompt order: base → temporal → memory → **topic policies** → few-shots → escalation FP → format
6. Approval gate (if `APPROVAL_MODE=True`) or direct delivery
7. `deliver_vip_response()` (human-like behavior)
8. Save example + background memory extraction

**Fourth flow — gray-zone guidance** (after LLM success + escalation handling, before `save_example`):
- Flag: `KNOWLEDGE_GAP_ENABLED` (default **True**). When off, gap fields are ignored — zero path change.
- When on and LLM sets `knowledge_gap` + `gap_question`:
  1. `match_policies` → **hit**: one regen with policies injected → normal save/approve|deliver (anti-reask; no Diana DM).
  2. **miss**: open consult (`guidance_requests` + `pending_guidance`), DM Diana only (`g:` UI), **VIP freeze**, finish timer without save/send.
- VIP freeze while pending: no `deliver_vip_response` / `mark_as_read` / `simulate_typing` / reengage / `save_example` of the gap draft.
- Diana answer → `knowledge.distill_guidance` → `topic_policies` → regen → supervised approval **or** autonomous deliver.
- Timeout `GUIDANCE_TIMEOUT_HOURS` (12) ≡ `g:use_draft` (stored draft → normal pipeline). Statuses: pending|answered|skipped|timeout|superseded.
- Logic lives in `services/knowledge.py`; handlers are Telegram I/O only.

**Idle re-engagement** (independent of the VIP reply flow above):
- On authorized VIP inbound (not edit / owner / observe-only / sandbox), `business.py` calls `reengagement.touch_inbound` to advance the silence-cycle clock.
- `router._post_init` starts `reengagement.start_scheduler` alongside history backfill and the guidance timeout scanner.
- The scanner in `services/reengagement.py` is **independent of `auto_reply` / LLM / approval**: fixed Spanish templates, direct send, Diana notify. See `REENGAGE_*` in `config.py`.
- Open `pending_guidance` blocks re-engagement for that chat (freeze completeness).

---

## Operating Modes

| Mode          | Config                  | Behavior |
|---------------|-------------------------|----------|
| **Supervised**    | `APPROVAL_MODE=True`    | Every response goes to Diana's DM for approval/fix before sending to VIP |
| **Autonomous**    | `APPROVAL_MODE=False`   | Bot sends directly. Low confidence triggers notification to Diana |

---

## Callback System (Important)

All inline keyboard callbacks use the format: `prefix:action:id`

- `a:` → Approval flow (`approve`, `fix`, `note`, `regen`, `prev`, `next`)
- `t:` → Training feedback (`good`, `bad`, `fix`)
- `au:` → VIP allowlist management (`del`)
- `e:` → Escalation triage (`valid`, `fp`, `gen`)
- `g:` → Gray-zone guidance (`answer`, `use_draft`, `skip`)

Router free-text await order (Diana DM): admin_note → **guidance** → note → correction.

---

## Admin Commands (Diana DM)

Key commands:
- `/usuarios` — Manage VIP allowlist
- `/notas <user_id>`, `/nota <user_id> <text>`, `/borrar_notas <user_id>`
- `/politicas [topic]` — List active topic policies (doctrine)
- `/borrar_politica <id>` — Soft-deactivate a policy (`is_active=0`)
- `/sandbox on/off/perfil/...` — Test mode with frozen memory profiles

---

## Development Guidelines

### When making changes
1. **Always explain your plan** first (especially for LLM, memory, or flow changes).
2. Make **incremental changes** when possible.
3. After significant changes, suggest updating `AGENTS.md` / `CLAUDE.md`.
4. Prefer modifying existing patterns over introducing new ones.

### LLM & Memory
- The LLM call is centralized in `services/llm.py`.
- Prompt inject order in `get_diana_response()`: base → temporal → memory → **policies** → few-shots → escalation FP → format.
- Topic policies (`services/knowledge.py`) are mandatory instruction blocks, not few-shots. First-slice distill creates policies only (no auto few-shot).
- Memory extraction happens in background via `MemoryService`.
- Be careful with prompt size. Prefer precise context over dumping everything.

### Human-like Delivery
- `services/delivery.py` controls read receipt → pause → typing → send.
- This behavior is intentional and should be preserved/enhanced carefully.

### Testing & Validation
- Run with a test bot when possible.
- Use **Sandbox mode** (`/sandbox`) heavily during development.
- There are 141 automated tests (`pytest tests/`).
- Manual validation against real Telegram behavior is still required (especially timing and Business API).

### Coding Style Preferences
- Keep the code **clear and explicit**.
- Favor readability over cleverness.
- Document non-obvious behavior (especially around timers, generation tracking, and approval flows).
- Use type hints where they add clarity.

---

## Persistence

- `diana_authorized_users.json` (gitignored)
- `diana_state.json` (business connections recovery)
- `diana_training.db` (SQLite — examples + user_memory)
- `diana_escalaciones.txt` (audit log)

---

## Useful Commands

```bash
source venv/bin/activate
python diana.py

# Extract training data
python extractor.py list
python extractor.py export --chat <id> --format training --import-db

# Testing
PYTHONPATH=. pytest tests/
