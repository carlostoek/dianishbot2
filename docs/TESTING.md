<!-- generated-by: gsd-doc-writer -->
# Testing

## Test framework and setup

**Status:** No automated test suite is present in this repository.

| Check | Result |
|-------|--------|
| `tests/` or `test/` directory | Not found |
| `test_*.py` or `*_test.py` files | Not found |
| `pytest.ini`, `pyproject.toml` test config | Not found |
| CI workflow with test steps | Not found |

The project is validated manually by running `python diana.py` against a Telegram test bot and observing behavior in VIP chats and admin DMs.

If you add tests, **pytest** with **pytest-asyncio** is the recommended stack for this async Telegram bot codebase.

## Running tests

No test commands exist today. Once a test suite is added, expected commands would be:

```bash
# Not yet available — placeholder for future setup
pip install pytest pytest-asyncio
pytest
```

```bash
# Run a single test file (future)
pytest tests/test_auth_users.py
```

```bash
# Watch mode (future — requires pytest-watch)
ptw
```

## Writing new tests

When introducing tests, follow these conventions:

**File naming:** `tests/test_<module>.py` — e.g., `tests/test_auth_users.py`, `tests/test_diana_escalation.py`

**Suggested first targets** (pure functions, no Telegram network):

| Function | Module | Why testable |
|----------|--------|--------------|
| `guess_topic()` | `diana.py` | Keyword classification, no I/O |
| `needs_escalation()` | `diana.py` | Escalation keyword detection |
| `build_few_shot_block()` | `diana.py` | Few-shot formatting from dict input |
| `is_authorized()` | `auth_users.py` | Allowlist logic with temp JSON file |
| `add_user()` / `remove_user()` | `auth_users.py` | CRUD with file persistence |

**Async handlers** (`process_update`, `_handle_business_message`, `auto_reply`) require mocking `python-telegram-bot` `Update` and `ContextTypes` objects. Use `pytest-asyncio` with `@pytest.mark.asyncio`.

**Example skeleton:**

```python
# tests/test_auth_users.py
import json
import auth_users
from pathlib import Path

def test_is_authorized(tmp_path):
    users_file = tmp_path / "users.json"
    users_file.write_text(json.dumps({"users": {"123": {"id": 123}}}))
    auth_users.configure(users_file=str(users_file), max_users=10)
    assert auth_users.is_authorized(123) is True
    assert auth_users.is_authorized(999) is False
```

## Coverage requirements

No coverage threshold configured. No `.nycrc`, `c8` config, or `coverage` settings exist.

## CI integration

No CI/CD pipeline detected. There are no `.github/workflows/` files in the repository.

Manual verification checklist before merging changes:

1. Bot starts without missing-env errors.
2. `/usuarios` lists and modifies the allowlist.
3. Authorized VIP messages trigger timer → LLM → delivery (or approval flow).
4. Escalation keywords skip auto-reply and log to `diana_escalaciones.txt`.
5. Callback buttons (approve, fix, rate, delete user) respond correctly.