# Tasks: Non-VIP Promo Info Autoreply

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 650–850 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR1 foundation → PR2 multi-send → PR3 wire+integrate |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| WU1 | Config + match + SQLite informed | PR1 | Base = tracker or main; no handler/delivery |
| WU2 | `deliver_sequential_messages` | PR2 | Parallel with WU1; base = PR1 branch or main |
| WU3 | Schedule/run + business intercept + integration | PR3 | Depends on WU1+WU2 |

**Parallel**: WU1 ∥ WU2. **Sequential**: WU3 after both. Strict TDD: RED → GREEN per unit.

Verify all: `PYTHONPATH=. pytest tests/`

---

## Phase 1: Foundation — config + informed store (WU1) → PR1

- [x] 1.1 **RED** `tests/unit/test_promo_info.py`: `is_trigger` strip/exact/near-miss; `init_schema`/`is_promo_informed`/`mark_promo_informed`; `message_pair` first vs repeat; `compute_promo_delay_sec` in [120,300]. DoD: fails (import/missing). Req: exact trigger, first/repeat msg1, durable informed, delay bounds.
- [x] 1.2 **GREEN** `config.py`: `NON_VIP_PROMO_*` (ENABLED default True, TRIGGER, DELAY_MIN/MAX, INTER_GAP_SEC, MSG1_FIRST, MSG1_REPEAT, MSG2 exact from spec).
- [x] 1.3 **GREEN** `services/promo_info.py`: `init_schema`, `is_trigger`, informed CRUD, `message_pair`, `compute_promo_delay_sec`; module `db` + `_require_db` like `chat_history`.
- [x] 1.4 Wire schema: `training.init_db()` → `promo_info.init_schema(conn)`; `tests/conftest.py` `test_db` creates `promo_informed`; optional `promo_info_db` fixture.
- [x] 1.5 Verify: `PYTHONPATH=. pytest tests/unit/test_promo_info.py -q` green; full suite green.

## Phase 2: Multi-message delivery (WU2) → PR2 ∥ WU1

- [x] 2.1 **RED** `tests/unit/test_delivery_multi.py`: one `mark_as_read`, two `send_message` order, inter-gap, `should_abort` before msg2 → False/no mark path, send fail → False. Req: multi-message-delivery.
- [x] 2.2 **GREEN** `services/delivery.py`: add `deliver_sequential_messages` (read once → type+send per text → short gap; `persist` flag; leave `deliver_vip_response` unchanged).
- [x] 2.3 Verify: `PYTHONPATH=. pytest tests/unit/test_delivery_multi.py tests/unit/test_chat_history_persistence.py -q` green.

## Phase 3: Orchestration + handler (WU3) → PR3

- [x] 3.1 **RED** extend `test_promo_info.py`: `schedule_promo_reply` ignores if `timers[chat_id]`; no `timer_schedule` write; `run_promo_reply` abort when authorized at fire (no deliver/mark); success → mark informed + clear timers; fail deliver → no mark.
- [x] 3.2 **GREEN** `promo_info.schedule_promo_reply` / `run_promo_reply`: sleep delay; auth re-check; `message_pair` + `deliver_sequential_messages(..., persist=False, should_abort=...)`; mark only full success; clear `timers` on exit. No LLM/approval/notify/training/memory/reengagement.
- [x] 3.3 **RED** handler tests (new or `test_business_logic.py`): non-VIP exact trigger schedules + sets `pending_msg`/`chat_bc`/`chat_meta`; non-trigger no schedule; flag off no schedule; VIP trigger still LLM path (no promo); mid-wait inbound no reschedule.
- [x] 3.4 **GREEN** `handlers/business.py` unauthorized branch: after observe (or flag-only path), if enabled + not edited + `is_trigger` → set pending/meta/bc → `schedule_promo_reply`; else return. Keep handler thin.
- [x] 3.5 Wire `diana.py`: `promo_info.db = db` after `init_db` (same pattern as `chat_history`).
- [x] 3.6 Regression: assert promo never writes `timer_schedule`; recovery unchanged (`handlers/recovery.py` / `test_runtime_recovery.py`).
- [x] 3.7 Verify: `PYTHONPATH=. pytest tests/ -q` full green.

## Phase 4: Docs polish (optional, same PR3 or follow-up)

- [ ] 4.1 If needed: note feature flag in `docs/CONFIGURATION.md` (one short section). Skip if out of review budget.
