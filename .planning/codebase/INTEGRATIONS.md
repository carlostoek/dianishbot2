# External Integrations

**Analysis Date:** 2026-06-25

## APIs & External Services

**LLM (response generation):**
- DeepSeek Chat Completions API — generates Diana persona responses with JSON output
  - Endpoint: `https://api.deepseek.com/v1/chat/completions` (`diana.py`)
  - Model: `deepseek-v4-flash`
  - Auth: `DEEPSEEK_KEY` env var (Bearer token)
  - Client: `aiohttp` direct POST in `get_diana_response()`
  - Options: `response_format: json_object`, `max_tokens: 300`, `temperature: 0.85`

**Messaging (primary integration):**
- Telegram Bot API — bot runtime, business messages, callbacks, typing indicators
  - SDK: `python-telegram-bot` 22.8 (`Application`, `TypeHandler`, `Update`)
  - Auth: `BOT_TOKEN` env var
  - Business features: `business_connection`, `business_message`, `edited_business_message`
  - Direct HTTP: `readBusinessMessage` via `https://api.telegram.org/bot{BOT_TOKEN}/readBusinessMessage` in `mark_as_read()` (Bot API 9.0)

## Data Storage

**Databases:**
- SQLite (`diana_training.db`)
  - Table: `examples` — chat context, bot response, confidence, topic, rating, correction, status
  - Access: stdlib `sqlite3` in `diana.py` (`init_db`, `save_example`, `get_few_shots`, etc.)
  - Connection: file path constant `DB_FILE`, `check_same_thread=False`

**File Storage:**
- Local filesystem only
  - `diana_authorized_users.json` — authorized VIP users (`auth_users.py`)
  - `diana_state.json` — business connection ID → owner user ID map (`diana.py`)
  - `diana_escalaciones.txt` — append-only escalation log (`log_escalation()`)
  - `diana_business.log` — structured app log
  - `diana_session.session` — present but not referenced in Python source (likely legacy Telethon/session artifact)

**Caching:**
- In-memory only — `history`, `timers`, `connections`, `pending_approval`, etc. (`diana.py`)

## Authentication & Identity

**Auth Provider:**
- Custom allowlist — no OAuth or external identity provider
  - Implementation: `auth_users.py` with JSON file persistence
  - Admin identified by `ADMIN_USER_ID` constant and/or business connection owner
  - VIP users added via forwarded messages to admin DM (`/usuarios`, forward-to-add flow)
  - Max 10 authorized users (`AUTH_USERS_MAX`)

**Telegram identity resolution:**
- `_resolve_sender_id()`, `_resolve_vip_id()` in `diana.py`
- Business connection owner stored in `connections` dict and `diana_state.json`

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry, Datadog, etc.)

**Logs:**
- Python `logging` to `diana_business.log` and stdout (`diana.py`)
- Format: `%(asctime)s | %(message)s`, level INFO
- Separate module logger `diana.auth_users` in `auth_users.py`
- Escalations written to `diana_escalaciones.txt` with user context

## CI/CD & Deployment

**Hosting:**
- Not configured in repo (manual/long-running process assumed)

**CI Pipeline:**
- None detected (no GitHub Actions, Makefile, or deploy scripts)

## Environment Configuration

**Required env vars:**
- `BOT_TOKEN` — Telegram bot token
- `DEEPSEEK_KEY` — DeepSeek API key

**Secrets location:**
- `.env` file (gitignored; template in `.env.example`)
- Startup fails with `SystemExit` if either var is missing (`main()` in `diana.py`)

**Hardcoded identifiers (not env):**
- `ADMIN_USER_ID`, `VIP_USERS_SEED`, `DIANA_ADMIN_CHAT_ID` in `diana.py`

## Webhooks & Callbacks

**Incoming:**
- Telegram long-polling updates via `app.run_polling()` — no HTTP webhook endpoint
- Allowed update types: `business_connection`, `business_message`, `edited_business_message`, `message`, `callback_query`

**Outgoing:**
- DeepSeek chat completions (on timer expiry / coverage activation)
- Telegram `send_message`, `send_chat_action`, `readBusinessMessage`
- Admin notifications to `DIANA_ADMIN_CHAT_ID` for approval and low-confidence feedback

## Human-in-the-loop training loop

**Approval callbacks (prefix `a:`):**
- `a:approve:{example_id}` — send draft to VIP
- `a:fix:{example_id}` — request correction from Diana

**Feedback callbacks (prefix `t:`):**
- `t:good`, `t:bad`, `t:fix` — rate sent responses for few-shot learning

**Auth user callbacks (prefix `au:`):**
- `au:del:{user_id}` — remove authorized user (`auth_users.py`)

---

*Integration audit: 2026-06-25*