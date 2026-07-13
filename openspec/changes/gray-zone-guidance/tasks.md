# Tasks: Gray-Zone Guidance → Topic Policies

Strict TDD RED→GREEN. `PYTHONPATH=. pytest tests/`. Specs: gray-zone-guidance · topic-policies.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 900–1300 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR1 foundation → PR2 consult+freeze → PR3 inject+regen+timeout |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Work Units (sequential, stacked-to-main)

| Unit | Goal | PR |
|------|------|-----|
| WU1 | Schema, match, LLM gap fields, flag, 6-tuple migration | PR1→main |
| WU2 | pending_guidance, g: UI, freeze, shared save→approve\|deliver | PR2→main on PR1 |
| WU3 | Inject, anti-reask, distill/regen, timeout, admin | PR3→main on PR2 |

---

## Phase 1 — Foundation (WU1) → PR1

Specs: gap signal · flag-off zero change · store/match API. DoD: tables; pure match; 6-tuple callers; flag off suite green; no consult UI.

- [x] 1.1 **RED** `tests/unit/test_knowledge_store.py`: schema; CRUD; match (topic+100, kw+10, inactive, floor, top-5); `build_policy_block` binding
- [x] 1.2 **GREEN** `services/knowledge.py` (promo_info `db`): schema, CRUD, match, block
- [x] 1.3 Wire `training.init_db`→`knowledge.init_schema`; `conftest.py` tables; `diana.py` `knowledge.db`
- [x] 1.4 **RED** `tests/unit/test_llm_gap_fields.py` (+`test_llm_pure`): optional gap fields; missing→false/`""`
- [x] 1.5 **GREEN** `config.py`: `KNOWLEDGE_GAP_ENABLED=False`, `GUIDANCE_TIMEOUT_HOURS=12`, `GUIDANCE_POLICY_PRIORITY=100`
- [x] 1.6 **GREEN** `services/llm.py`: schema+parse; return 6-tuple `(resp, conf, topic, knowledge_gap, gap_question, failure)`
- [x] 1.7 **Migrate 6-tuple call sites**: `timer.py`, `callbacks/approval.py`, `callbacks/escalation.py`, tests (`test_llm_retry`, `test_chat_history_persistence`, `test_runtime_recovery`, callback/timer mocks)
- [x] 1.8 Prompt: gap only for doctrine (never FAQ/tone/escalado)
- [x] 1.9 Verify knowledge+llm units + full suite flag-off green

---

## Phase 2 — Consult + VIP freeze (WU2) → PR2

Specs: freeze · g: UI · free-text exclusion · persist/re-notify · owner supersede · sandbox · escalation wins.
**Freeze invariant:** no deliver / mark_as_read / simulate_typing / reengage / save_example of gap draft.

- [x] 2.1 **RED** runtime/state tests: `pending_guidance` persist; `awaiting_guidance_answer` not persisted
- [x] 2.2 **GREEN** `state.py`: both dicts; snapshot/load; `_active_chat_ids`
- [x] 2.3 **RED** `tests/unit/test_guidance_callbacks.py`: g:answer/use_draft/skip; free-text; mutual exclusion; expired/unauth
- [x] 2.4 **GREEN** `handlers/callbacks/guidance.py` + `g` in `__init__.py`; router: admin_note→**guidance**→note→correction
- [x] 2.5 **RED** timer guidance tests: gap+no match→consult+notify; never save/approve/deliver/read/type; flag off; escalation>gap
- [x] 2.6 **GREEN** Extract shared **save→approve|deliver** helper (`timer.py` or `callbacks/shared.py`); wire normal timer exit + `g:use_draft`
- [x] 2.7 **GREEN** `timer.py` gap branch after escalation, before `save_example`; freeze + finish timer
- [x] 2.8 **GREEN** `reengagement.py`: `_has_pending_guidance` block
- [x] 2.9 **GREEN** `data_pause.py`/`sandbox.py`: clear pending; no real policy/consult writes
- [x] 2.10 **GREEN** `business.py` owner inbound→supersede; `recovery.py` restore+re-notify
- [x] 2.11 **Freeze invariant suite** + full suite green

---

## Phase 3 — Inject + distill/regen + timeout + admin (WU3) → PR3

Specs: anti-reask · distill→regen · timeout≡use_draft · inject order · list/deactivate · policy-only · stale gen.

- [x] 3.1 **RED** inject tests: after memory, before few-shots; empty/inactive omit
- [x] 3.2 **GREEN** `get_diana_response`: always match+`build_policy_block`
- [x] 3.3 **RED** distill tests: happy→row; fail→degraded raw; no auto few-shot
- [x] 3.4 **GREEN** `knowledge.distill_guidance` + answer path saves policy, status `answered`
- [x] 3.5 **RED/GREEN** post-answer: fresh gen→regen→shared helper (approval|deliver); stale→no VIP send, notify
- [x] 3.6 **RED/GREEN** timer anti-reask: gap+match→one regen, no `pending_guidance`
- [x] 3.7 **RED/GREEN** timeout: age>`GUIDANCE_TIMEOUT_HOURS`→`timeout`≡`g:use_draft`; freeze until fire
- [x] 3.8 **GREEN** `/politicas [topic]`, `/borrar_politica <id>` soft deactivate
- [x] 3.9 **GREEN** `AGENTS.md` fourth flow, `g:`, freeze, flag, prompt order
- [x] 3.10 Full suite green

### Manual (post-WU3)

Flag off unchanged · flag on consult only (no VIP I/O) · answer→policy+approval · no re-ask · timeout draft · restart re-notify · reengage blocked

### Out of scope

Vector search · auto few-shot distill · VIP wait/read/typing · escalation UX redesign
