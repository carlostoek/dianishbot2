# Codebase Structure

**Analysis Date:** 2026-06-25

## Directory Layout

```
diana/                          # Project root (repo: dianishbot)
├── diana.py                    # Main bot — routing, LLM, training, delivery (~1152 lines)
├── auth_users.py               # Authorized VIP user management (~287 lines)
├── .env.example                # Env template (BOT_TOKEN, DEEPSEEK_KEY)
├── .gitignore                  # Ignores .env, venv, logs, db, state files
├── README.md                   # Minimal placeholder (repo name only)
├── venv/                       # Python 3.14 virtual environment (not committed)
├── __pycache__/                # Compiled Python bytecode
│
├── diana_authorized_users.json # Runtime: VIP allowlist (seeded on first run)
├── diana_state.json            # Runtime: persisted business_connection IDs
├── diana_training.db           # Runtime: SQLite training examples
├── diana_business.log          # Runtime: application log
├── diana_escalaciones.txt      # Runtime: escalation audit log
├── diana_cobertura.log         # Runtime: secondary log (coverage-related)
└── diana_session.session       # Runtime: session file (not used by current Python code)
```

## Directory Purposes

**Project root:**
- Purpose: All application code and runtime data colocated in a flat layout
- Contains: Two Python modules, env template, generated state/logs/DB
- Key files: `diana.py`, `auth_users.py`

**venv/:**
- Purpose: Isolated Python dependencies
- Contains: `python-telegram-bot`, `aiohttp`, `python-dotenv`, transitive deps
- Generated: Yes
- Committed: No (implied by standard practice; not explicitly in `.gitignore` but typically excluded)

## Key File Locations

**Entry Points:**
- `diana.py` (`main()`): Application bootstrap and polling loop
- `diana.py` (`process_update()`): Single handler for all Telegram update types

**Configuration:**
- `.env.example`: Documents required environment variables
- `.env`: Live secrets (gitignored, must be created locally)
- `diana.py` lines 30-65: Hardcoded runtime constants (delays, thresholds, file paths, admin IDs)
- `DIANA_SYSTEM_PROMPT` in `diana.py` lines 196-379: Persona and behavior rules

**Core Logic:**
- `diana.py`: Business message handling, LLM integration, timers, delivery, training callbacks
- `auth_users.py`: User allowlist load/save, `/usuarios`, forward-to-add, delete callbacks

**Persistence:**
- `diana_training.db`: SQLite `examples` table — created by `init_db()` in `diana.py`
- `diana_authorized_users.json`: Managed by `auth_users._save()` / `_load()`
- `diana_state.json`: Business connections via `_save_connections_state()` in `diana.py`

**Testing:**
- Not detected — no `tests/` directory, no `test_*.py` files, no pytest config

## Naming Conventions

**Files:**
- Snake_case Python modules: `diana.py`, `auth_users.py`
- Runtime data prefixed with `diana_`: `diana_state.json`, `diana_training.db`, `diana_business.log`

**Functions:**
- snake_case: `get_diana_response`, `_handle_business_message`, `save_example`
- Private helpers prefixed with `_`: `_load_connections_state`, `_resolve_vip_id`
- Async handlers prefixed with `handle_` or verb: `handle_callback`, `auto_reply`

**Variables:**
- UPPER_SNAKE_CASE for module constants: `BOT_TOKEN`, `APPROVAL_MODE`, `MAX_FEW_SHOTS`
- Spanish log/comments mixed with English identifiers

**Callback data prefixes:**
- `a:` — approval mode (approve/fix)
- `t:` — training feedback (good/bad/fix)
- `au:` — auth user delete (`auth_users.py`)

## Where to Add New Code

**New Telegram handler or update type:**
- Primary code: `process_update()` in `diana.py` — add routing branch
- Register allowed update in `app.run_polling(allowed_updates=[...])` at bottom of `diana.py`

**New authorized-user admin feature:**
- Implementation: `auth_users.py` — extend `handle_admin_message()` or `handle_callback()`
- Wire in: `process_update()` already delegates to `auth_users` handlers

**New LLM behavior or prompt changes:**
- System prompt: `DIANA_SYSTEM_PROMPT` constant in `diana.py`
- Request logic: `get_diana_response()` in `diana.py`
- Few-shot logic: `get_few_shots()`, `build_few_shot_block()`, `TOPIC_MAP` in `diana.py`

**New persistence / training fields:**
- Schema: `init_db()` CREATE TABLE in `diana.py`
- Writes: `save_example()`, `update_rating()` and related helpers in `diana.py`

**New external integration:**
- HTTP clients: follow `aiohttp` pattern in `get_diana_response()` or `mark_as_read()`
- Secrets: add to `.env.example` and load via `os.getenv()` near top of `diana.py`

**Utilities shared across modules:**
- Currently no `utils/` package — either add `utils/` or keep helpers in the owning module
- Cross-module config: pass via `auth_users.configure(**kwargs)` pattern

## Special Directories

**venv/:**
- Purpose: Local Python environment with pinned installed packages
- Generated: Yes (`python -m venv venv`)
- Committed: No

**__pycache__/:**
- Purpose: Bytecode cache for `diana.py` and `auth_users.py`
- Generated: Yes
- Committed: No

**.planning/codebase/:**
- Purpose: GSD codebase intelligence documents (this scan output)
- Generated: By `/gsd-map-codebase`
- Committed: Yes (via GSD workflow)

## Module dependency graph

```
diana.py
  └── imports auth_users

auth_users.py
  └── imports telegram (PTB types only; no import from diana.py)
```

No `requirements.txt` — dependencies must be inferred from venv or documented manually when adding packages.

---

*Structure analysis: 2026-06-25*