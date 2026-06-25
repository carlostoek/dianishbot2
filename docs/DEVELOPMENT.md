<!-- generated-by: gsd-doc-writer -->
# Development

## Local setup

Development setup mirrors production — there is no separate dev server or build step.

1. Clone the repo and create a venv (see [GETTING-STARTED.md](GETTING-STARTED.md)).
2. Copy `.env.example` to `.env` and use **test** bot token and DeepSeek key.
3. Run the bot directly:

```bash
source venv/bin/activate
python diana.py
```

4. Use a separate Telegram test account for VIP simulation, or add your test user ID to `VIP_USERS_SEED` in `diana.py` before first run.

**Tip:** Set `APPROVAL_MODE = True` during development so every draft goes to the admin DM for inspection before reaching test chats.

## Build commands

This is an interpreted Python project with no build step. Common commands:

| Command | Description |
|---------|-------------|
| `python diana.py` | Start the bot (long-polling) |
| `source venv/bin/activate` | Activate the virtual environment |
| `pip install "python-telegram-bot>=21.0" python-dotenv aiohttp` | Install runtime dependencies |

There is no `Makefile`, `package.json`, or CI pipeline in the repository.

## Code style

No linting or formatting tools are configured. The project does not include:

- `.eslintrc*`, `eslint.config.*`
- `.prettierrc*`, `prettier.config.*`
- `biome.json`
- `.editorconfig`
- `ruff.toml`, `pyproject.toml`

**Conventions observed in source:**

- **Files:** snake_case modules (`diana.py`, `auth_users.py`)
- **Functions:** snake_case; private helpers prefixed with `_` (`_handle_business_message`)
- **Constants:** `UPPER_SNAKE_CASE` (`BOT_TOKEN`, `APPROVAL_MODE`)
- **Async handlers:** `async def` with `handle_` prefix for Telegram handlers
- **Comments and logs:** Mix of Spanish and English
- **Callback data:** Prefixed strings (`a:`, `t:`, `au:`) for routing

## Module boundaries

| Module | Responsibility |
|--------|----------------|
| `diana.py` | All bot orchestration — do not split without a clear reason |
| `auth_users.py` | VIP allowlist only — configured via `auth_users.configure()` at startup |

When adding features, prefer extending `auth_users.py` for user-management concerns and keeping LLM/delivery logic in `diana.py`.

## Key development workflows

### Tuning the persona

Edit `DIANA_SYSTEM_PROMPT` in `diana.py` (lines 196–379). Changes take effect on restart — no hot reload.

### Adjusting response timing

Edit constants at the top of `diana.py`:

- `RESPONSE_DELAY_MIN` / `RESPONSE_DELAY_MAX` — autonomous mode delay range (minutes)
- `SILENCE_MINUTES` — supervised mode wait before generating draft
- `CONFIDENCE_THRESHOLD` — when to notify Diana about low-confidence responses

### Adding escalation keywords

Edit `ESCALATE_KEYWORDS` list in `diana.py`. Matches trigger `log_escalation()` and skip auto-reply.

### Inspecting training data

```bash
sqlite3 diana_training.db "SELECT id, username, topic, confidence, rating, status FROM examples ORDER BY id DESC LIMIT 10;"
```

## Branch conventions

No convention documented. The default branch is `main`.

## PR process

No `.github/PULL_REQUEST_TEMPLATE.md` or `CONTRIBUTING.md` exists. Standard practice:

1. Create a feature branch from `main`.
2. Test manually against a Telegram test bot and test VIP account.
3. Open a pull request with a description of behavior changes.
4. Avoid committing `.env`, `venv/`, logs, or runtime data files (all listed in `.gitignore`).