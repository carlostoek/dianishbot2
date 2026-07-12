# Design: Non-VIP Promo Info Autoreply

## Technical Approach

**Approach A** (locked): intercept the unauthorized branch in `handlers/business.py`, own match/schedule/send in new `services/promo_info.py`, extend `services/delivery.py` for sequential human-like multi-send. **Do not** call LLM `auto_reply`, approval, training, memory, reengagement, or Diana notify.

Mental model: reengagement **semantics** (fixed Spanish templates, no LLM) + VIP delivery **UX** (read → pause → typing → send).

## Architecture Decisions

| Decision | Options | Choice | Rationale |
|----------|---------|--------|-----------|
| Entry point | A intercept non-VIP / B reuse `auto_reply` / C sleep in deliver | **A** | Keeps VIP LLM path clean; recovery cannot rehydrate promo as LLM |
| Timer persistence | `timer_schedule` + `kind` / omit schedule | **Omit `timer_schedule`** | `recover_runtime_on_startup` always spawns `auto_reply`; omit = zero recovery risk. Restart drops in-flight promo; user re-triggers |
| Staleness | `reply_gen` / task-in-`timers` only | **`timers[chat_id]` only** | Do not bump `reply_gen` (VIP-owned). Presence of task = waiting; ignore further inbound for reschedule |
| Mid-wait inbound | cancel / reschedule / ignore | **Ignore** | Observe-log only; timer keeps original `fire_at` |
| VIP mid-wait | still send / abort at fire | **Abort if `is_authorized` at fire** | Also: if they message as VIP, VIP path cancels `timers[chat_id]` |
| Informed store | JSON file / SQLite | **SQLite table** in `diana_training.db` | Matches training/chat_history pattern; durable first vs repeat |
| Multi-send | double `deliver_vip_response` / new helper | **`deliver_sequential_messages`** | One read-receipt; typing+send per part; short inter-gap |
| History persist | persist outbound / RAM only | **`persist=False`** | Non-VIP observe is RAM-only; full non-VIP history OOS |
| Feature flag | couple to `OBSERVE_UNAUTHORIZED` / independent | **`NON_VIP_PROMO_AUTOREPLY_ENABLED` default True** | Works even if observe is off (still needs text + bc) |

## Data Flow

```
business_message (non-VIP)
  → optional observe log (OBSERVE_UNAUTHORIZED)
  → if not NON_VIP_PROMO_AUTOREPLY_ENABLED or edited: return
  → promo_info.is_trigger(text)?  # strip only, exact
       no  → return (observe-only)
       yes → pending_msg[chat_id]=msg_id; chat_bc; chat_meta
             if chat_id in timers: log ignore; return
             schedule promo_info.run_promo_reply(...); timers[chat_id]=task
                  │  (NO timer_schedule, NO reply_gen bump, NO _save_runtime_state for timer)
                  ▼
             sleep random 2–5 min
                  ▼
             is_authorized(vip/chat)? → abort, clear timers
                  ▼
             texts = [msg1_first|msg1_repeat, msg2] via is_promo_informed(chat_id)
                  ▼
             deliver_sequential_messages(read once → type+send → gap → type+send)
                  ▼
             on full success: mark_promo_informed(chat_id); clear timers
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `config.py` | Modify | Flag, trigger, msg1 first/repeat, msg2, delay min/max, inter-gap |
| `services/promo_info.py` | Create | Match, delay, schedule, fire, informed SQLite API |
| `services/delivery.py` | Modify | Add `deliver_sequential_messages`; leave `deliver_vip_response` VIP-stable |
| `handlers/business.py` | Modify | Thin call in unauthorized branch after observe logging |
| `services/training.py` | Modify | Call `promo_info.init_schema(conn)` from `init_db()` |
| `tests/conftest.py` | Modify | Create `promo_informed` in `test_db` |
| `tests/unit/test_promo_info.py` | Create | Match, informed, schedule/ignore, abort-on-VIP |
| `tests/unit/test_delivery_multi.py` | Create | Single read, two sends, abort mid-sequence |
| `handlers/recovery.py` | None | Unchanged — promo never enters `timer_schedule` |
| `handlers/timer.py` | None | VIP `auto_reply` untouched |

## Interfaces / Contracts

### Config keys (`config.py`)

```python
NON_VIP_PROMO_AUTOREPLY_ENABLED = True
NON_VIP_PROMO_TRIGGER = "Quiero más información 🔥"  # exact after str.strip()
NON_VIP_PROMO_DELAY_MIN = 2   # minutes
NON_VIP_PROMO_DELAY_MAX = 5
NON_VIP_PROMO_INTER_GAP_SEC = (1.5, 3.0)  # random uniform between msg1 and msg2
NON_VIP_PROMO_MSG1_FIRST = "Holaaa 💕\nTe mando mis promos 🔥"
NON_VIP_PROMO_MSG1_REPEAT = (
    "Holis 😁 \n"
    "Claro, te mando de nuevo mis promos. Los nombres son los mismos pero es contenido nuevo y diferente."
)
NON_VIP_PROMO_MSG2 = """..."""  # full promo block from exploration (leetspeak preserved)
```

### `services/promo_info.py`

```python
def init_schema(conn: sqlite3.Connection) -> None: ...
def is_trigger(text: str) -> bool:
    """True iff text.strip() == NON_VIP_PROMO_TRIGGER (no case fold)."""
def is_promo_informed(chat_id: int) -> bool: ...
def mark_promo_informed(chat_id: int, *, username: str = "") -> None: ...
def compute_promo_delay_sec() -> float:
    """uniform(MIN*60, MAX*60)."""
def message_pair(chat_id: int) -> tuple[str, str]:
    """(msg1 first|repeat, msg2) from informed flag."""
async def schedule_promo_reply(bot, *, chat_id, username, bc_id, vip_id) -> bool:
    """Create task if no active timers[chat_id]; store in timers. Return True if scheduled."""
async def run_promo_reply(bot, *, chat_id, username, bc_id, vip_id, delay_sec) -> None:
    """Sleep → auth re-check → deliver → mark informed. Clears timers on exit."""
```

### SQLite: `promo_informed`

```sql
CREATE TABLE IF NOT EXISTS promo_informed (
    chat_id     INTEGER PRIMARY KEY,
    username    TEXT,
    informed_at TEXT NOT NULL
);
```

Wire: `training.init_db()` → `promo_info.init_schema(conn)`; module uses shared `training.db` / local `db` wired like `chat_history` (or import `services.training.db` via `_require_db`). Prefer module-level `db` set in `diana.py` after `init_db` for consistency with `chat_history`.

### `services/delivery.py`

```python
async def deliver_sequential_messages(
    bot, *, chat_id: int, bc_id: str, username: str,
    texts: list[str],
    should_abort: Callable[[], bool] | None = None,
    persist: bool = True,
    inter_gap_sec: tuple[float, float] = (1.5, 3.0),
) -> bool:
    """Read pending_msg once → for each text: abort check, typing, send, append_message.
    Short random gap between messages (not after last). Return False on abort or send fail.
    Partial send: do not mark informed (caller); stop on first failure."""
```

`run_promo_reply` passes `should_abort=lambda: auth is_authorized(vip_id, chat_id)`, `persist=False`.

### Handler intercept (`business.py` unauthorized branch)

After existing observe append/`chat_bc`/`chat_meta` (or equivalent when observe off but flag on):

1. If edited or flag off → return (current behavior).
2. If `promo_info.is_trigger(text)`:
   - Ensure `chat_bc`, `chat_meta`, `pending_msg[chat_id] = msg.message_id`.
   - `await`/`schedule_promo_reply(...)` (fire-and-forget task inside service).
3. Else return (observe-only).

Handler stays I/O-thin: no delay math, no copy selection, no DB.

## Sequencing & Error Handling

| Stage | Failure | Behavior |
|-------|---------|----------|
| Sleep cancelled | `CancelledError` | Exit; VIP path or shutdown owns cancel |
| Auth VIP at fire | authorized | Log abort; no send; no mark informed |
| Read receipt fail | HTTP error | Log; continue (same as VIP deliver) |
| Send msg1 fail | exception | Log; stop; **do not** mark informed |
| Send msg2 fail | exception | Log; stop; **do not** mark informed (may re-get first intro on retry) |
| Mark informed fail | SQLite error | Log error; messages already sent (acceptable once) |

No Diana DM on success or failure.

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Unit | `is_trigger` strip/exact/negative | parametrize |
| Unit | `is_promo_informed` / `mark_promo_informed` | tmp sqlite via conftest |
| Unit | `message_pair` first vs repeat | informed flag |
| Unit | `compute_promo_delay_sec` bounds | monkeypatch random or range assert |
| Unit | `schedule_promo_reply` ignore if `timers` set | AsyncMock bot |
| Unit | `run_promo_reply` abort when authorized mid-wait | patch `is_authorized` |
| Unit | `deliver_sequential_messages` | one `mark_as_read`, two `send_message`, gap; abort before msg2 |
| Integration | non-VIP trigger schedules; non-trigger no schedule; VIP trigger still `auto_reply` | patch business handler like `test_vip_race_delivery` |
| Regression | recovery never calls `auto_reply` for promo | assert promo does not write `timer_schedule` |

Strict TDD: RED tests first for match, multi-send, abort-on-VIP, observe-only non-trigger.

## Migration / Rollout

- Schema: `CREATE TABLE IF NOT EXISTS` — no migration script.
- Flag default on; disable with `NON_VIP_PROMO_AUTOREPLY_ENABLED=False`.
- Rollback: flag off or revert deploy; optional `DELETE FROM promo_informed` (VIP unaffected).

## Open Questions

- [x] `NON_VIP_PROMO_MSG1_REPEAT` locked by product:
  ```
  Holis 😁 
  Claro, te mando de nuevo mis promos. Los nombres son los mismos pero es contenido nuevo y diferente.
  ```
- [x] When observe off + flag on: still require non-empty text path without observe history? **Yes** — schedule on trigger only; skip observe append if `OBSERVE_UNAUTHORIZED` is False.
- [x] Partial msg1-only success then crash: next trigger still first intro until both succeed — **accepted**.
