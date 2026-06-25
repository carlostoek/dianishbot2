<!-- generated-by: gsd-doc-writer -->
# Getting Started

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | >= 3.14 | Project uses CPython 3.14.4 locally |
| pip | Latest | For installing dependencies into a venv |
| Telegram account | — | Diana's account with **Chat Automation** enabled |
| Telegram bot | — | Created via [@BotFather](https://t.me/BotFather) |
| DeepSeek API key | — | For LLM responses |

**Python packages** (install via pip):

- `python-telegram-bot` >= 21.0 (tested with 22.8)
- `python-dotenv`
- `aiohttp`

There is no `requirements.txt` or `pyproject.toml` in the repository — install packages manually or generate a lockfile locally.

## Installation steps

1. **Clone the repository**

```bash
git clone <repository-url>
cd diana
```

2. **Create and activate a virtual environment**

```bash
python3 -m venv venv
source venv/bin/activate
```

3. **Install dependencies**

```bash
pip install "python-telegram-bot>=21.0" python-dotenv aiohttp
```

4. **Configure environment variables**

```bash
cp .env.example .env
```

Edit `.env` and set:

```
BOT_TOKEN=your_telegram_bot_token
DEEPSEEK_KEY=your_deepseek_api_key
```

5. **Connect Chat Automation**

On Diana's Telegram account:

- Go to **Settings → Chat Automation**
- Connect the bot created in BotFather
- Ensure business messages are routed to the bot

The bot persists the business connection ID to `diana_state.json` on first activation.

## First run

```bash
source venv/bin/activate
python diana.py
```

Expected startup log output includes:

- `DB de entrenamiento lista: diana_training.db`
- `Diana Business Bot v2.0 iniciando...`
- VIP count, observation mode, supervised/autonomous mode, and delay settings

The bot runs long-polling and listens for: `business_connection`, `business_message`, `edited_business_message`, `message`, and `callback_query` updates.

**Seed VIP users:** On first run, IDs in `VIP_USERS_SEED` (defined in `diana.py`) are written to `diana_authorized_users.json` if the file does not exist.

**Add more VIPs:** As admin, DM the bot `/usuarios` and forward a user's message to add them to the allowlist.

## Common setup issues

### Missing environment variables

```
Faltan variables de entorno: BOT_TOKEN, DEEPSEEK_KEY. Copia .env.example a .env y configúralas.
```

**Fix:** Create `.env` from `.env.example` and fill in both values.

### Business messages not arriving

**Symptoms:** Bot starts but never logs `ENTRADA` messages.

**Fix:** Verify Chat Automation is enabled on Diana's account and the bot connection is active. Check `diana_business.log` for `Conexión activa` on startup. Restart the bot after enabling the connection.

### `ReadTimeout` or Telegram network errors

**Symptoms:** Intermittent disconnects or timeout errors in logs.

**Fix:** The bot configures extended timeouts (`TG_CONNECT_TIMEOUT=15`, `TG_READ_TIMEOUT=30`) in `diana.py`. Ensure stable network connectivity. The bot uses `bootstrap_retries=-1` for automatic reconnection.

### User cannot be added via forward

**Symptoms:** Bot replies that it cannot obtain the user ID.

**Fix:** The forwarded user may have **forward privacy** enabled. Ask them to disable it or message the bot directly first so their ID is known.

## Next steps

- [DEVELOPMENT.md](DEVELOPMENT.md) — local development workflow and code conventions
- [TESTING.md](TESTING.md) — current test status and how to add tests
- [CONFIGURATION.md](CONFIGURATION.md) — all environment variables and runtime constants
- [ARCHITECTURE.md](ARCHITECTURE.md) — system design and data flow