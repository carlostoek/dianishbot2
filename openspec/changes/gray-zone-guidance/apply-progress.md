# Apply Progress: gray-zone-guidance

**Mode**: Strict TDD  
**Delivery**: auto-chain · stacked-to-main  
**Current slice**: WU1 Foundation (PR1 → main)  
**Date**: 2026-07-13

## Completed Tasks

### Phase 1 — Foundation (WU1) — 9/9

- [x] 1.1 RED `tests/unit/test_knowledge_store.py`
- [x] 1.2 GREEN `services/knowledge.py`
- [x] 1.3 Wire `training.init_db` / conftest / `diana.py`
- [x] 1.4 RED `tests/unit/test_llm_gap_fields.py` (+ pure)
- [x] 1.5 GREEN config flags
- [x] 1.6 GREEN llm schema+parse+6-tuple
- [x] 1.7 Migrate 6-tuple call sites
- [x] 1.8 Prompt gap doctrine criteria
- [x] 1.9 Full suite green flag-off

### Phase 2 / 3 — not started (WU2 / WU3)

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 1.1 | `tests/unit/test_knowledge_store.py` | Unit | N/A (new) | ✅ Written (ImportError) | ✅ via 1.2 | ✅ 22 cases | ✅ Clean |
| 1.2 | same | Unit | N/A (new) | ✅ 1.1 first | ✅ 22 passed | ✅ schema/CRUD/match/block | ✅ Clean |
| 1.3 | wiring via suite | Unit | ✅ pre-existing | ➖ structural | ✅ suite green | ➖ Single | ➖ None needed |
| 1.4 | `tests/unit/test_llm_gap_fields.py` + pure | Unit | ✅ llm pure/retry | ✅ Written | ✅ via 1.6 | ✅ present/missing/fail | ✅ Clean |
| 1.5 | config constants | Unit | N/A | ➖ structural | ✅ used by knowledge+suite | ➖ Single | ➖ None needed |
| 1.6 | gap + retry + pure | Unit | ✅ 44 baseline | ✅ 1.4 first | ✅ 74 knowledge+llm | ✅ 6-tuple paths | ✅ Clean |
| 1.7 | call-site mocks | Unit | ✅ suite | ✅ failing unpacks | ✅ suite green | ✅ many call sites | ➖ mechanical |
| 1.8 | prompt text | Unit | N/A | ➖ structural (prompt) | ✅ suite green | ➖ Single | ➖ None needed |
| 1.9 | full suite | Unit | ✅ | ➖ verify | ✅ **542 passed** | ➖ | ➖ |

### Test Summary

- **Knowledge unit tests**: 22 passed
- **LLM gap + pure + retry + chat history**: 74 passed (subset)
- **Full suite (flag off)**: **542 passed**, 1 pre-existing warning
- **Layers used**: Unit only
- **Approval tests**: None — no pure-refactor-only tasks
- **Pure functions created**: `_parse_gap_fields`, `match_policies` scoring, `build_policy_block`, `_normalize_topic`

## Files Changed

| File | Action | What Was Done |
|------|--------|---------------|
| `services/knowledge.py` | Created | Schema, CRUD, match, build_policy_block |
| `tests/unit/test_knowledge_store.py` | Created | Schema/CRUD/match/block tests |
| `tests/unit/test_llm_gap_fields.py` | Created | Schema, parse, 6-tuple tests |
| `services/llm.py` | Modified | Schema gap fields, parse, 6-tuple, prompt criteria |
| `config.py` | Modified | `KNOWLEDGE_GAP_ENABLED=False`, timeout 12h, priority 100 |
| `services/training.py` | Modified | `knowledge.init_schema` from `init_db` |
| `diana.py` | Modified | Wire `knowledge.db` |
| `tests/conftest.py` | Modified | Tables + `knowledge_db` fixture |
| `handlers/timer.py` | Modified | 6-tuple unpack (gap unused while flag off) |
| `handlers/callbacks/approval.py` | Modified | 6-tuple unpack |
| `handlers/callbacks/escalation.py` | Modified | 6-tuple unpack |
| `tests/unit/test_llm_pure.py` | Modified | Optional gap parse case |
| `tests/unit/test_llm_retry.py` | Modified | 6-tuple unpack |
| `tests/unit/test_chat_history_persistence.py` | Modified | 6-tuple unpack |
| `tests/unit/test_callbacks_*.py` etc. | Modified | Mock return 6-tuples |
| `openspec/.../tasks.md` | Modified | 1.1–1.9 checked |

## Deviations from Design

None — implementation matches design. Policy inject on every `get_diana_response` call deferred to WU3 as planned. Consult path deferred to WU2.

## Issues Found

None.

## Remaining Tasks

- [ ] Phase 2 WU2 (2.1–2.11) — consult + VIP freeze
- [ ] Phase 3 WU3 (3.1–3.10) — inject + distill/regen + timeout + admin

## Workload / PR Boundary

- Mode: stacked PR slice (stacked-to-main)
- Current work unit: **WU1 Foundation**
- Boundary: schema + knowledge store + LLM gap fields + 6-tuple migration; flag off → zero VIP path change
- Out of scope this PR: pending_guidance, g: handlers, inject, distill, timeout UI
- Estimated review budget impact: medium (new module + call-site migration + tests); should stay reviewable as PR1

## Status

**9/9 WU1 tasks complete.** Ready for next batch: `sdd-apply` WU2.
