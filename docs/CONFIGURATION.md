<!-- generated-by: gsd-doc-writer -->
# Configuration

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BOT_TOKEN` | **Yes** | — | Telegram bot token from [@BotFather](https://t.me/BotFather). Loaded via `python-dotenv` at startup. Startup fails if missing. |
| `DEEPSEEK_KEY` | **Yes** | — | DeepSeek API bearer token for LLM calls. Startup fails if missing. |

Create `.env` from the template:

```bash
cp .env.example .env
```

The template in `.env.example` documents both variables with placeholder values.

## Config file format

Beyond environment variables, runtime behavior is controlled by **hardcoded constants** in `diana.py` (lines 30–65). There is no external config file — edit the source to change these values.

### Core settings

| Constant | Value | Description |
|----------|-------|-------------|
| `DEEPSEEK_URL` | `https://api.deepseek.com/v1/chat/completions` | DeepSeek API endpoint |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | Model identifier sent in API requests |
| `AUTH_USERS_FILE` | `diana_authorized_users.json` | VIP allowlist persistence path |
| `AUTH_USERS_MAX` | `10` | Maximum authorized VIP users |
| `STATE_FILE` | `diana_state.json` | Business connection state file |
| `DB_FILE` | `diana_training.db` | SQLite training database path |
| `LOG_FILE` | `diana_business.log` | Application log file |
| `ESCALATE_FILE` | `diana_escalaciones.txt` | Escalation audit log |

### Timing and behavior

| Constant | Value | Description |
|----------|-------|-------------|
| `RESPONSE_DELAY_MIN` | `1` | Min delay (minutes) before auto-reply in autonomous mode |
| `RESPONSE_DELAY_MAX` | `8` | Max delay (minutes) — actual delay is random in range |
| `SILENCE_MINUTES` | `2` | Wait time in supervised (`APPROVAL_MODE`) mode |
| `MAX_HISTORY` | `10` | Messages sent to LLM as conversation context |
| `MAX_FEW_SHOTS` | `3` | Approved examples injected into system prompt |
| `CONFIDENCE_THRESHOLD` | `70` | Responses below this % trigger admin notification |
| `APPROVAL_MODE` | `True` | `True` = supervised (Diana approves drafts); `False` = autonomous |
| `OBSERVE_UNAUTHORIZED` | `True` | Log and learn from non-authorized chats without auto-replying |

### Telegram network timeouts

| Constant | Value (seconds) |
|----------|-----------------|
| `TG_CONNECT_TIMEOUT` | `15.0` |
| `TG_READ_TIMEOUT` | `30.0` |
| `TG_WRITE_TIMEOUT` | `30.0` |
| `TG_POOL_TIMEOUT` | `5.0` |
| `TG_POLL_TIMEOUT` | `30` |

### Admin and seed data

| Constant | Description |
|----------|-------------|
| `ADMIN_USER_ID` | Diana's Telegram user ID for admin DM commands |
| `DIANA_ADMIN_CHAT_ID` | Same as admin ID — receives approval drafts and training notifications |
| `VIP_USERS_SEED` | Set of user IDs seeded into allowlist on first run if JSON file is missing |

## Required vs optional settings

**Startup will fail** if either environment variable is absent:

```python
# diana.py main() — raises SystemExit
missing = [name for name, val in (
    ("BOT_TOKEN", BOT_TOKEN),
    ("DEEPSEEK_KEY", DEEPSEEK_KEY),
) if not val]
```

All other settings have defaults defined in `diana.py` or are created at runtime (SQLite DB, JSON state files).

## Runtime data files

These files are created automatically and listed in `.gitignore`:

| File | Created by | Purpose |
|------|------------|---------|
| `diana_authorized_users.json` | `auth_users._save()` | VIP allowlist |
| `diana_state.json` | `_save_connections_state()` | Active business connection IDs |
| `diana_training.db` | `init_db()` | Training examples table |
| `diana_business.log` | `logging` | Application logs |
| `diana_escalaciones.txt` | `log_escalation()` | Escalation audit trail |

## Per-environment overrides

The project does not use `.env.development` / `.env.production` split files. Configuration differences between environments are handled by:

1. Different `.env` files on each deployment host (not committed).
2. Editing hardcoded constants in `diana.py` for mode changes (`APPROVAL_MODE`, delays, thresholds).

<!-- VERIFY: Production deployment host paths and process manager (systemd, screen, etc.) are not defined in the repository. -->