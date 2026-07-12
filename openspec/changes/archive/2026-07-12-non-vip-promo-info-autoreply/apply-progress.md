# Apply Progress: non-vip-promo-info-autoreply

**Mode**: Strict TDD  
**Delivery**: auto-chain / stacked-to-main  
**Batch**: WU1 + WU2 + **WU3 / PR3** (Phase 3 tasks 3.1–3.7)  
**Date**: 2026-07-12

## Completed Tasks

- [x] 1.1 RED `tests/unit/test_promo_info.py`
- [x] 1.2 GREEN `config.py` NON_VIP_PROMO_* constants
- [x] 1.3 GREEN `services/promo_info.py` (match, informed CRUD, message_pair, delay)
- [x] 1.4 Wire schema via `training.init_db` + conftest `test_db` / `promo_info_db`
- [x] 1.5 Verify: unit 23/23 green; full suite 475 passed
- [x] 2.1 RED `tests/unit/test_delivery_multi.py`
- [x] 2.2 GREEN `services/delivery.py` `deliver_sequential_messages`
- [x] 2.3 Verify: multi + chat_history green; full suite 483 passed
- [x] 3.1 RED extend `test_promo_info.py` schedule/run contracts
- [x] 3.2 GREEN `schedule_promo_reply` / `run_promo_reply`
- [x] 3.3 RED `tests/unit/test_promo_handler.py` handler intercept
- [x] 3.4 GREEN `handlers/business.py` unauthorized promo intercept
- [x] 3.5 Wire `diana.py` `promo_info.db = db`
- [x] 3.6 Regression: no `timer_schedule` write; recovery suite green
- [x] 3.7 Verify: full suite **497 passed**

## Remaining Tasks

- [ ] Phase 4 (optional): docs 4.1 — skipped (out of review budget; flag already in config)

## Files Changed (WU1)

| File | Action | What Was Done |
|------|--------|---------------|
| `tests/unit/test_promo_info.py` | Created | RED unit tests for trigger, informed store, pair, delay, wiring |
| `config.py` | Modified | NON_VIP_PROMO_* flag, trigger, copy, delays |
| `services/promo_info.py` | Created | init_schema, is_trigger, informed CRUD, message_pair, delay |
| `services/training.py` | Modified | call promo_info.init_schema from init_db |
| `tests/conftest.py` | Modified | promo_informed table in test_db + promo_info_db fixture |
| `openspec/.../tasks.md` | Modified | Phase 1 checked; chain strategy locked stacked-to-main |
| `openspec/.../apply-progress.md` | Created | this file |

## Files Changed (WU2)

| File | Action | What Was Done |
|------|--------|---------------|
| `tests/unit/test_delivery_multi.py` | Created | RED: single read, order, inter-gap, abort, send fail, persist flag |
| `services/delivery.py` | Modified | `deliver_sequential_messages`; `deliver_vip_response` unchanged |
| `openspec/.../tasks.md` | Modified | Phase 2 2.1–2.3 checked |
| `openspec/.../apply-progress.md` | Modified | merge WU2 progress |

## Files Changed (WU3)

| File | Action | What Was Done |
|------|--------|---------------|
| `tests/unit/test_promo_info.py` | Modified | RED schedule/run: ignore active timer, no timer_schedule, abort VIP, success mark, fail no mark, cancel |
| `services/promo_info.py` | Modified | `schedule_promo_reply` / `run_promo_reply` orchestration |
| `tests/unit/test_promo_handler.py` | Created | Handler intercept: trigger/non-trigger/flag/VIP/mid-wait/observe-off |
| `handlers/business.py` | Modified | Unauthorized branch: thin promo intercept after observe |
| `diana.py` | Modified | `promo_info.db = db` after init_db |
| `tests/unit/test_diana_startup_llm.py` | Modified | Assert promo_info/chat_history/training db wiring |
| `openspec/.../tasks.md` | Modified | Phase 3 3.1–3.7 checked |
| `openspec/.../apply-progress.md` | Modified | merge WU3 progress |

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 1.1 | `tests/unit/test_promo_info.py` | Unit | ✅ baseline chat_history/reengagement green | ✅ Written (ImportError) | n/a (RED only) | ✅ multi near-miss + happy | n/a |
| 1.2 | same (config asserts) | Unit | N/A (constants) | covered by 1.1 | ✅ config constants | ➖ Single exact copy | ➖ None needed |
| 1.3 | same | Unit | N/A (new module) | covered by 1.1 | ✅ 23 passed | ✅ first/repeat, CRUD, delay uniform | ✅ chat_history-style module |
| 1.4 | `test_training_init_db_wires_promo_schema` + conftest | Unit | ✅ training init path | covered | ✅ table created | ✅ fixture + init_db path | ➖ None needed |
| 1.5 | full suite | Unit | ✅ 475 passed | n/a | ✅ green | n/a | n/a |
| 2.1 | `tests/unit/test_delivery_multi.py` | Unit | ✅ chat_history_persistence 20/20 | ✅ Written (ImportError) | n/a (RED only) | ✅ 8 cases (order/gap/abort/fail/persist) | n/a |
| 2.2 | same | Unit | N/A (new function; VIP path untouched) | covered by 2.1 | ✅ 8/8 multi + 20 chat_history | ✅ abort, partial fail, persist True/False, N=1 | ➖ Minimal clean API |
| 2.3 | multi + chat_history + full | Unit | ✅ 483 passed | n/a | ✅ green | n/a | n/a |
| 3.1 | `tests/unit/test_promo_info.py` | Unit | ✅ 63 baseline (promo+multi+vip+recovery+history) | ✅ Written (AttributeError) | n/a (RED only) | ✅ 6 cases (ignore/schedule/abort/success/fail/cancel) | n/a |
| 3.2 | same | Unit | covered by 3.1 | covered | ✅ 29/29 promo unit | ✅ VIP abort + deliver fail + cancel | ✅ `_clear_promo_timer` helper |
| 3.3 | `tests/unit/test_promo_handler.py` | Unit | N/A (new file) | ✅ Written (missing promo_info import) | n/a (RED only) | ✅ trigger/non-trigger/flag/VIP/mid-wait/observe-off | n/a |
| 3.4 | same + business.py | Unit | ✅ | covered by 3.3 | ✅ handler 8/8 | ✅ observe off + mid-wait | ✅ thin unauthorized branch |
| 3.5 | `test_diana_startup_llm.py` | Unit | ✅ startup tests | ✅ assert wiring | ✅ green | ✅ training+chat_history+promo | ➖ None needed |
| 3.6 | promo_handler + runtime_recovery | Unit | ✅ recovery suite | ✅ no timer_schedule asserts | ✅ green | ✅ schedule path + recovery unchanged | ➖ recovery.py untouched |
| 3.7 | full suite | Unit | ✅ 497 passed | n/a | ✅ green | n/a | n/a |

### Test Summary

- **Total tests written (WU3)**: 6 schedule/run + 8 handler + wiring assertion = ~14 new
- **Total tests passing**: **497** (full suite; was 483 after WU2)
- **Layers used**: Unit
- **Approval tests**: None — recovery/VIP path not modified
- **Pure functions created**: none new (orchestration is async IO)

## Workload / PR Boundary

- Mode: chained PR slice (stacked-to-main)
- Current work unit: **WU3 / PR3** — schedule/run + business intercept + diana wire
- Boundary: starts after WU1+WU2 on `feat/non-vip-promo-multi-delivery`; ends with full promo path live
- Depends on: PR1 (#4) + PR2 (#5)
- Out of scope: optional docs 4.1
- Rollback: flag `NON_VIP_PROMO_AUTOREPLY_ENABLED=False` or revert PR3
- Estimated review budget: ~350–400 lines for WU3-only diff after PR1/PR2 merge

## Deviations from Design

None — data flow matches design.md: unauthorized → observe optional → trigger → pending/meta/bc → schedule (no timer_schedule) → sleep → auth re-check → deliver_sequential (persist=False) → mark on full success.

## Issues Found

None. PR1 (#4) and PR2 (#5) still open at apply time; merge order **#4 → #5 → #6**. Optional task 4.1 docs skipped.

## Status

15/15 numbered Phase 1–3 tasks complete (Phase 4 optional skipped). **Ready for sdd-verify**.
