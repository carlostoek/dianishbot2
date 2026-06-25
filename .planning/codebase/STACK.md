# Technology Stack

**Analysis Date:** 2026-06-25

## Languages

**Primary:**
- Python 3.14.4 — entire application (`diana.py`, `auth_users.py`)

**Secondary:**
- Not detected

## Runtime

**Environment:**
- CPython 3.14.4 (local venv at `venv/`)

**Package Manager:**
- pip (venv-based)
- Lockfile: Not detected (no `requirements.txt` or `pyproject.toml`)

## Frameworks

**Core:**
- python-telegram-bot 22.8 — Telegram Bot API client with async support; uses Business Connection / Chat Automation features (`diana.py`)

**Testing:**
- Not detected

**Build/Dev:**
- python-dotenv 1.2.2 — loads `.env` at startup (`diana.py`)
- aiohttp 3.14.1 — async HTTP for DeepSeek API and direct Telegram Bot API calls (`diana.py`)

## Key Dependencies

**Critical:**
- `python-telegram-bot` 22.8 — long-polling bot, business messages, inline keyboards, callbacks
- `aiohttp` 3.14.1 — LLM requests and `readBusinessMessage` HTTP calls
- `python-dotenv` 1.2.2 — `BOT_TOKEN` and `DEEPSEEK_KEY` from environment

**Infrastructure:**
- `sqlite3` (stdlib) — training examples database (`diana_training.db`)
- `asyncio` (stdlib) — timers, typing simulation, concurrent delivery
- `json`, `logging`, `pathlib`, `random`, `datetime` (stdlib) — state, persistence, logging

## Configuration

**Environment:**
- `.env` file (from `.env.example` template)
- Required vars: `BOT_TOKEN`, `DEEPSEEK_KEY`
- Loaded via `load_dotenv()` at module import in `diana.py`

**Build:**
- No build step — interpreted Python, run directly
- Config constants hardcoded in `diana.py` (timeouts, delays, thresholds, admin IDs, model name)

**Runtime data files (not env):**
- `diana_authorized_users.json` — VIP allowlist
- `diana_state.json` — persisted business connection IDs
- `diana_training.db` — SQLite training examples
- `diana_business.log` — application log
- `diana_escalaciones.txt` — escalation audit trail

## Platform Requirements

**Development:**
- Python 3.14+ (currently 3.14.4 in venv)
- Telegram Bot with Business / Chat Automation enabled
- DeepSeek API key
- Network access to `api.telegram.org` and `api.deepseek.com`

**Production:**
- Long-running process (polling, not webhook)
- Persistent filesystem for JSON state, SQLite DB, and logs
- No container or deployment config detected in repo

---

*Stack analysis: 2026-06-25*