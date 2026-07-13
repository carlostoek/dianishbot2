# Apply Progress: gray-zone-guidance

**Mode**: Strict TDD  
**Delivery**: auto-chain · stacked-to-main  
**Current slice**: WU3 Inject + distill/regen + timeout + admin (PR3 → main on PR2)  
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

### Phase 2 — Consult + VIP freeze (WU2) — 11/11

- [x] 2.1 RED runtime/state tests
- [x] 2.2 GREEN `state.py` pending_guidance + awaiting_guidance_answer
- [x] 2.3 RED `test_guidance_callbacks.py`
- [x] 2.4 GREEN `handlers/callbacks/guidance.py` + register `g:` + router order
- [x] 2.5 RED timer guidance tests (freeze invariants)
- [x] 2.6 GREEN shared `enter_draft_pipeline` (timer) + wire use_draft
- [x] 2.7 GREEN timer gap branch after escalation before save
- [x] 2.8 GREEN reengagement `_has_pending_guidance`
- [x] 2.9 GREEN data_pause/sandbox clear pending; no real consult in sandbox
- [x] 2.10 GREEN owner supersede + recovery re-notify
- [x] 2.11 Freeze suite + full suite green

### Phase 3 — Inject + distill/regen + timeout + admin (WU3) — 10/10

- [x] 3.1 RED inject tests: after memory, before few-shots; empty/inactive omit
- [x] 3.2 GREEN `get_diana_response` always match+`build_policy_block`
- [x] 3.3 RED distill tests: happy→row; fail→degraded raw; no auto few-shot
- [x] 3.4 GREEN `knowledge.distill_guidance` + free-text saves policy, status answered
- [x] 3.5 RED/GREEN post-answer: fresh gen→regen→enter_draft_pipeline; stale→no VIP send
- [x] 3.6 RED/GREEN timer anti-reask: gap+match→one regen, no pending_guidance
- [x] 3.7 RED/GREEN timeout: age > GUIDANCE_TIMEOUT_HOURS → status timeout ≡ use_draft
- [x] 3.8 GREEN `/politicas [topic]`, `/borrar_politica <id>` soft deactivate
- [x] 3.9 GREEN `AGENTS.md` fourth flow, `g:`, freeze, flag, prompt order
- [x] 3.10 Full suite green — **588 passed**

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 1.1–1.9 | (WU1 prior) | Unit | ✅ | ✅ | ✅ | ✅ | ✅ |
| 2.1–2.11 | (WU2 prior) | Unit | ✅ | ✅ | ✅ | ✅ | ✅ |
| 3.1 | `tests/unit/test_policy_inject.py` | Unit | ✅ knowledge+llm baseline | ✅ Written (policy missing) | ✅ via 3.2 | ✅ order + empty + inactive | ✅ Clean |
| 3.2 | `services/llm.py` inject | Unit | ✅ | ✅ 3.1 first | ✅ 3 inject tests pass | ✅ 3 cases | ✅ try/except skip |
| 3.3 | `tests/unit/test_distill_guidance.py` | Unit | N/A (new API) | ✅ Written (no distill) | ✅ via 3.4 | ✅ happy/fail/invalid/no-fewshot | ✅ Clean |
| 3.4 | `knowledge.distill_guidance` + answer path | Unit | ✅ | ✅ 3.3 first | ✅ distill+answer green | ✅ multi paths | ✅ degraded helper |
| 3.5 | `tests/unit/test_guidance_answer_regen.py` | Unit | ✅ callbacks | ✅ Written | ✅ Passed | ✅ supervised/auto/stale/degraded | ✅ Clean |
| 3.6 | `tests/unit/test_timer_guidance.py` | Unit | ✅ timer | ✅ Updated anti-reask | ✅ 2 LLM calls + regen text | ✅ match vs no-match | ✅ Clean |
| 3.7 | `tests/unit/test_guidance_timeout.py` | Unit | N/A (new) | ✅ Written | ✅ Passed | ✅ timeout/fresh/stale | ✅ Clean |
| 3.8 | `tests/unit/test_admin_politicas.py` | Unit | ✅ admin | ✅ Written | ✅ Passed | ✅ list/filter/deactivate | ➖ |
| 3.9 | `AGENTS.md` | Docs | N/A | ➖ docs | ✅ | ➖ | ➖ |
| 3.10 | full suite | Unit | ✅ | ➖ verify | ✅ **588 passed** | ➖ | ➖ |

### Test Summary

- **WU3 new/updated unit tests**: policy_inject (3) + distill (4) + answer_regen (4) + timeout (3) + admin (3) + timer anti-reask update + free-text callback update ≈ 17+
- **Full suite**: **588 passed**, 1 pre-existing RuntimeWarning (unawaited auto_reply in unrelated test)
- **Layers used**: Unit only
- **Approval tests**: None — no pure-refactor-only tasks
- **Pure / shared helpers**: `distill_guidance`, `_degraded_distill`, `build_policy_block` inject, `process_guidance_timeouts`, `list_policies_filtered`

## Files Changed (WU3)

| File | Action | What Was Done |
|------|--------|---------------|
| `services/llm.py` | Modified | Policy match+inject after memory, before few-shots |
| `services/knowledge.py` | Modified | `distill_guidance`, DISTILL_SCHEMA, degraded path, `list_policies_filtered`, `raw_call` indirection |
| `handlers/callbacks/guidance.py` | Modified | Free-text distill→policy→regen; timeout scanner; policy_id on resolve |
| `handlers/timer.py` | Modified | Anti-reask one-shot regen on policy match |
| `handlers/router.py` | Modified | Start guidance timeout scheduler |
| `handlers/admin_auth.py` | Modified | `/politicas`, `/borrar_politica` + ayuda |
| `AGENTS.md` | Modified | Fourth flow, g:, freeze, flag, prompt order, admin cmds |
| `tests/unit/test_policy_inject.py` | Created | Inject order tests |
| `tests/unit/test_distill_guidance.py` | Created | Distill happy/fail/no-fewshot |
| `tests/unit/test_guidance_answer_regen.py` | Created | Post-answer regen + stale |
| `tests/unit/test_guidance_timeout.py` | Created | Timeout ≡ use_draft |
| `tests/unit/test_admin_politicas.py` | Created | Admin list/deactivate |
| `tests/unit/test_timer_guidance.py` | Modified | Anti-reask asserts one regen |
| `tests/unit/test_guidance_callbacks.py` | Modified | Free-text expects distill+regen |
| `openspec/.../tasks.md` | Modified | 3.1–3.10 checked |

## Deviations from Design

None material — implementation matches design locks:
- Distill is separate small-schema LLM call; fail → degraded raw summary
- Free-text upgraded from use_draft to distill → policy → regen → enter_draft_pipeline
- Inject order: base→temporal→memory→policies→few_shots→escalation_fp→format
- Timeout uses stored draft via enter_draft_pipeline (same as use_draft)
- First slice: no auto few-shot from distill
- VIP freeze remains until resolution/timeout
- Timeout scanner is a dedicated interval task (not only piggybacked on reengage) so it runs even when reengagement is disabled

## Issues Found

None blocking. Pre-existing RuntimeWarning about unawaited `auto_reply` still present (unrelated).

## Remaining Tasks

None — all phases complete. Ready for `sdd-verify`.

## Workload / PR Boundary

- Mode: stacked PR slice (stacked-to-main)
- Current work unit: **WU3 Inject + distill/regen + timeout + admin**
- Branch: `feat/gray-zone-guidance-wu3` (stacked on WU2 tip `feat/gray-zone-guidance-wu2`)
- Boundary: policy inject, distill LLM, free-text regen, anti-reask regen, 12h timeout worker, `/politicas`, AGENTS.md
- Out of scope: vector search, auto few-shot distill, escalation UX redesign
- Estimated review budget impact: medium–high but autonomous WU3 slice

## Status

**WU1 9/9 + WU2 11/11 + WU3 10/10 complete.** Ready for `sdd-verify`.
