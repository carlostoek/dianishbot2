## Verification Report

**Change**: gray-zone-guidance  
**Version**: N/A (delta specs only; no main openspec baseline for these domains)  
**Mode**: Strict TDD  
**Branch verified**: `feat/gray-zone-guidance-wu3` (WU1+WU2+WU3 stacked)  
**Date**: 2026-07-13  
**Artifact store**: openspec

### Completeness

| Metric | Value |
|--------|-------|
| Tasks total (Phase 1–3) | 30 |
| Tasks complete | 30 |
| Tasks incomplete | 0 |
| Manual post-WU3 checklist | listed (ops validation; not code tasks) |
| Apply-progress status | All phases complete; ready for verify; no material design deviations |

### Build & Tests Execution

**Build**: ➖ N/A (Python runtime project; no separate build step)

**Tests**: ✅ **588 passed** / ❌ 0 failed / ⚠️ 0 skipped  
Command: `PYTHONPATH=. pytest tests/`  
Runtime: ~2.32s  
Guidance-focused subset (11 files): **78 passed** in ~0.42s

```text
........................................................................ [ 12%]
........................................................................ [ 24%]
........................................................................ [ 36%]
........................................................................ [ 48%]
........................................................................ [ 61%]
........................................................................ [ 73%]
........................................................................ [ 85%]
........................................................................ [ 97%]
............                                                             [100%]
588 passed in 2.32s
```

Note: one benign `RuntimeWarning: coroutine 'auto_reply' was never awaited` from an unrelated VIP-path test that closes the scheduled coro without awaiting (pre-existing expected pattern).

**Coverage**: ➖ Not available (`pytest-cov` not installed)

Config defaults verified at import: `KNOWLEDGE_GAP_ENABLED=False`, `GUIDANCE_TIMEOUT_HOURS=12`, `GUIDANCE_POLICY_PRIORITY=100`.

---

### Spec Compliance Matrix

#### Capability: `gray-zone-guidance`

| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| Explicit knowledge_gap signal | Gap fields open consult path | `test_timer_guidance.py` > `test_gap_no_match_opens_consult_no_vip_io`, `test_gap_consult_creates_db_request`; `test_llm_gap_fields.py` parse/6-tuple | ✅ COMPLIANT |
| Explicit knowledge_gap signal | Low confidence without gap does not consult | Structural: timer gap branch requires `knowledge_gap` + non-empty `gap_question` (no confidence gate); covered by positive tests only firing when gap=true + `test_flag_off_ignores_gap` path | ✅ COMPLIANT |
| Escalation precedence over gap | Escalation topic suppresses gap | `test_timer_guidance.py` > `test_escalation_wins_over_gap` | ✅ COMPLIANT |
| Feature flag default off | Flag off ignores gap fields | `test_timer_guidance.py` > `test_flag_off_ignores_gap`; `config.py` default False | ✅ COMPLIANT |
| Anti-reask via policy match | Policy match regenerates without DM | `test_timer_guidance.py` > `test_gap_with_policy_match_one_regen_no_consult` (2 LLM calls, no pending) | ✅ COMPLIANT |
| Anti-reask via policy match | No match opens consult | `test_gap_no_match_opens_consult_no_vip_io`, `test_gap_consult_creates_db_request` | ✅ COMPLIANT |
| VIP freeze while pending | Open guidance blocks all VIP side effects | `test_gap_no_match_opens_consult_no_vip_io` (no save/approve/deliver/read/type); `test_guidance_freeze.py` reengage block | ✅ COMPLIANT |
| Diana consult UI with g: | Answer starts free-text capture | `test_guidance_callbacks.py` > `test_g_answer_arms_free_text`, `test_notify_diana_guidance_has_g_buttons` | ✅ COMPLIANT |
| Diana consult UI with g: | Use draft enters normal draft path | `test_g_use_draft_supervised_enters_approval`, `test_g_use_draft_autonomous_delivers` | ✅ COMPLIANT |
| Diana consult UI with g: | Skip closes without VIP send | `test_g_skip_closes_without_vip_send`, `test_g_skip_marks_request_skipped` | ✅ COMPLIANT |
| Free-text mutual exclusion | Answer clears other awaits | `test_g_answer_clears_note_and_correction` | ✅ COMPLIANT |
| Free-text mutual exclusion | Note/correct clears guidance await | `test_a_note_clears_awaiting_guidance`, `test_a_fix_clears_awaiting_guidance` | ✅ COMPLIANT |
| Answer distill then regen | Supervised answer → approval | `test_guidance_answer_regen.py` > `test_answer_fresh_distill_regen_supervised` | ✅ COMPLIANT |
| Answer distill then regen | Autonomous answer delivers | `test_answer_fresh_autonomous_delivers` | ✅ COMPLIANT |
| Answer distill then regen | Distill failure still proceeds | `test_answer_distill_fail_still_saves_policy`; `test_distill_guidance.py` degraded paths | ✅ COMPLIANT |
| Twelve-hour timeout ≡ use_draft | Timeout opens normal draft path | `test_guidance_timeout.py` > `test_timeout_opens_draft_path_supervised` (status `timeout`, save+approval, draft text); shared `enter_normal_draft_path` ≡ use_draft | ✅ COMPLIANT |
| Owner Business supersedes | Owner inbound supersedes | `test_guidance_freeze.py` > `test_owner_supersede_closes_guidance`; `business.py` wires `supersede_guidance_for_chat` | ✅ COMPLIANT |
| VIP new message stales guidance | New VIP message blocks old delivery | `test_answer_stale_gen_no_vip_send`, `test_timeout_stale_gen_no_send` | ✅ COMPLIANT |
| Persist + re-notify | Restart preserves open consults | `test_guidance_state.py` roundtrip; `test_recovery_renotifies_open_guidance`; `test_awaiting_guidance_not_in_recovery_snapshot` | ✅ COMPLIANT |
| Sandbox no pollution | Sandbox blocks production policy writes | Gates in timer (`should_persist` + synthetic) + `_persist_policy_from_answer` + `_close_pending`; `should_persist` unit-tested; **no dedicated guidance×sandbox path test** | ⚠️ PARTIAL |

#### Capability: `topic-policies`

| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| Store policy with full doctrine fields | Distill creates complete policy row | `test_distill_guidance.py` > `test_distill_happy_creates_policy_fields`; `test_knowledge_store.py` create/defaults | ✅ COMPLIANT |
| Store policy with full doctrine fields | Degraded distill still stores audit trail | `test_distill_failure_degrades_to_raw_summary`, `test_distill_invalid_json_degrades` | ✅ COMPLIANT |
| Match by free-form topic and keywords | Keyword hit without identical topic | `test_knowledge_store.py` > `test_match_keyword_hit_without_topic` | ✅ COMPLIANT |
| Match by free-form topic and keywords | Exact topic alone can match | `test_match_exact_topic_scores_eligible`, `test_match_topic_is_normalized_case` | ✅ COMPLIANT |
| Match by free-form topic and keywords | Inactive policies never match | `test_match_inactive_excluded` | ✅ COMPLIANT |
| Inject as mandatory instruction block | Assembly order after memory / before few-shots | `test_policy_inject.py` > `test_policy_block_after_memory_before_few_shots`; `test_build_policy_block_labels_as_mandatory_instructions` | ✅ COMPLIANT |
| Inject as mandatory instruction block | Empty match injects nothing | `test_empty_match_omits_policy_block` | ✅ COMPLIANT |
| Soft deactivate and list | Soft deactivate stops injection | `test_admin_politicas.py` > `test_borrar_politica_soft_deactivates`; `test_inactive_policy_not_injected` | ✅ COMPLIANT |
| Soft deactivate and list | List surfaces stored doctrine | `test_politicas_lists_active`, `test_politicas_topic_filter` | ✅ COMPLIANT |
| First slice policy-only | Answer distill is policy-only | `test_distill_does_not_create_few_shot` | ✅ COMPLIANT |

**Compliance summary**: **29/30** scenarios fully compliant; **1/30** partial (sandbox×guidance path)

---

### Correctness (Static Evidence)

| Requirement | Status | Notes |
|------------|--------|-------|
| Gap signal optional fields + defaults | ✅ Implemented | `services/llm.py` schema + `_parse_gap_fields`; 6-tuple return |
| Flag default False | ✅ Implemented | `config.py` `KNOWLEDGE_GAP_ENABLED = False` |
| Timeout 12h | ✅ Implemented | `GUIDANCE_TIMEOUT_HOURS = 12`; scanner interval 300s independent of reengage |
| Escalation > gap | ✅ Implemented | `timer.py` escalation block returns before gap branch |
| Gap after escalation, before save | ✅ Implemented | `handlers/timer.py` L195–256 then `enter_draft_pipeline` |
| Anti-reask one regen | ✅ Implemented | match → one `get_diana_response` regen → fall through; no re-check gap |
| VIP freeze on consult open | ✅ Implemented | open consult → notify → `_finish_timer` return (no save/deliver) |
| Reengage blocked | ✅ Implemented | `reengagement._has_pending_guidance` |
| Shared save→approve\|deliver | ✅ Implemented | `enter_draft_pipeline` / `enter_normal_draft_path` |
| g: callbacks | ✅ Implemented | `handlers/callbacks/guidance.py` answer/use_draft/skip |
| Free-text → distill → regen | ✅ Implemented | `handle_diana_guidance_answer` |
| Timeout ≡ use_draft | ✅ Implemented | both call `enter_normal_draft_path`; status differs (`timeout` vs `skipped`) |
| pending_guidance persisted | ✅ Implemented | `state.py` snapshot/load; await runtime-only |
| Recovery re-notify | ✅ Implemented | `handlers/recovery.py` |
| Owner supersede | ✅ Implemented | `business.py` → `supersede_guidance_for_chat` |
| Policy inject order | ✅ Implemented | `llm.py` memory → policy_block → few_shots |
| Match scoring | ✅ Implemented | topic +100, keyword +10, floor, top-5, priority/recency |
| Admin list/deactivate | ✅ Implemented | `/politicas`, `/borrar_politica` in `admin_auth.py` |
| AGENTS.md fourth flow | ✅ Implemented | document updated |
| Sandbox gates | ✅ Implemented | timer + `_persist_policy_from_answer` + resolve skip |

### Coherence (Design)

| Decision | Followed? | Notes |
|----------|-----------|-------|
| New g: / pending_guidance flow | ✅ Yes | Distinct from approval/escalation/notes |
| Detection via knowledge_gap + gap_question | ✅ Yes | Not low confidence |
| Escalation > gap > approve/deliver | ✅ Yes | Timer order |
| Tables in training DB | ✅ Yes | `knowledge.init_schema` via `training.init_db` |
| Logic in `services/knowledge.py` | ✅ Yes | Handlers I/O + orchestration only |
| Inject mandatory block after memory | ✅ Yes | |
| First slice policy-only | ✅ Yes | No auto few-shot from distill |
| No save_example on gap open | ✅ Yes | |
| pending_guidance persist; await not | ✅ Yes | |
| Flag default False | ✅ Yes | |
| Timeout 12h ≡ use_draft | ✅ Yes | |
| VIP freeze zero I/O | ✅ Yes | |
| Reengage block on open guidance | ✅ Yes | |
| Post-answer supervised/auto | ✅ Yes | |
| Owner supersede | ✅ Yes | |
| Sandbox no real policy/consult pollution | ✅ Yes (code) | Dedicated path test partial |
| Timeout scanner independent of reengage | ✅ Yes | Design deviation note: dedicated interval task (good) |

### Locked Product Decisions Check

| Decision (docs/feedback-design.md + design.md) | Verified |
|-----------------------------------------------|----------|
| VIP freeze: no deliver / mark_as_read / typing / reengage / save gap draft | ✅ tests + code |
| Timeout 12h ≡ g:use_draft (normal draft path) | ✅ |
| `KNOWLEDGE_GAP_ENABLED` default False | ✅ |
| Escalation wins over gap | ✅ |
| First slice policy-only (no auto few-shot) | ✅ |
| Callback prefix `g:` | ✅ |
| Module `services/knowledge.py` | ✅ |
| Prompt order base→temporal→memory→policies→few_shots→escalation_fp→format | ✅ |
| Hook after escalation before save_example | ✅ |
| pending_guidance persisted; awaiting runtime-only | ✅ |

### Success Criteria

| Criterion | Met? |
|-----------|------|
| Gray-zone consult freezes VIP and consults Diana only | ✅ |
| Distill → policy → regen → supervised approval or autonomous deliver | ✅ |
| Anti-reask on policy match (one regen, no DM) | ✅ |
| Flag off = zero behavior change | ✅ |
| Timeout uses stored draft via normal path | ✅ |
| Automated tests cover contracts | ✅ 78 focused + 588 suite |

---

### TDD Compliance

| Check | Result | Details |
|-------|--------|---------|
| TDD Evidence reported | ✅ | Full table in `apply-progress.md` (WU1–WU3) |
| All tasks have tests | ✅ | 30/30 implementation tasks linked; 3.9 docs N/A |
| RED confirmed (tests exist) | ✅ | 11 dedicated unit files present |
| GREEN confirmed (tests pass) | ✅ | 78 guidance-related + 588 full suite pass on re-run |
| Triangulation adequate | ✅ | Match matrix, freeze, callbacks, supervised/auto/stale, timeout age, distill happy/fail |
| Safety Net for modified files | ✅ | Apply-progress records baseline suite greens per WU |

**TDD Compliance**: 6/6 checks passed

---

### Test Layer Distribution

| Layer | Tests | Files | Tools |
|-------|-------|-------|-------|
| Unit | ~78 new/related (full suite 588) | 11 primary guidance/policy files | pytest + pytest-asyncio |
| Integration | 0 dedicated Telegram e2e | — | not installed |
| E2E | 0 | — | not installed |
| **Total** | **588 suite / 78 guidance-focused** | | |

---

### Changed File Coverage

Coverage analysis skipped — no coverage tool detected (`pytest-cov` not installed).

---

### Assertion Quality

Scanned guidance-related unit tests:

| File | Line | Assertion | Issue | Severity |
|------|------|-----------|-------|----------|
| `tests/unit/test_timer_guidance.py` | 123 | `assert mock_approval.await_count + 0 >= 0` | Near-tautology (always true); real coverage is `mock_save` / no pending | WARNING |

Mock-heavy files (mocks ≳ 2× asserts — expected for handler orchestration with Telegram I/O mocked):

- `test_timer_guidance.py` (~58 mock constructs / 14 asserts)
- `test_guidance_answer_regen.py` (~38 / 14)

Behavioral asserts elsewhere verify real outcomes: pending presence, status fields, save/deliver call counts, draft text, policy fields, inject order markers.

**Assertion quality**: 0 CRITICAL, 1 WARNING

---

### Quality Metrics

**Linter**: ➖ Not run (no project linter invoked in capabilities for this verify)  
**Type Checker**: ➖ Not available as enforced gate  
**Coverage**: ➖ Not available

---

### PR Chain Status

| Commit | Title | Branch |
|--------|-------|--------|
| `4662f26` | feat(knowledge): WU1 gray-zone foundation | `feat/gray-zone-guidance-wu1` (ancestor) |
| `91432a2` | feat(guidance): WU2 consult UI + VIP freeze | `feat/gray-zone-guidance-wu2` (ancestor) |
| `21e7017` | feat(guidance): WU3 policy inject, distill/regen, timeout, admin | `feat/gray-zone-guidance-wu3` (HEAD) |

Delivery strategy: **auto-chain / stacked-to-main**.  
At verify time: **no open GitHub PRs** listed for these heads — open PR chain (or merge stacked branch) remains a delivery step outside the code/spec gate.

---

### Issues Found

**CRITICAL**: None

**WARNING**:
1. **Sandbox×guidance path only partially tested** — production gates exist (`should_persist` in timer consult open, distill persist, and resolve), and `should_persist` is unit-tested, but there is no test that activates sandbox and asserts no `create_policy` / no real consult. Spec scenario is ⚠️ PARTIAL.
2. **Weak assert** in `test_flag_off_ignores_gap` (`await_count + 0 >= 0`) — harmless but adds no signal; real asserts on that test are sufficient.
3. **Timeout autonomous path** not dedicated — supervised timeout + autonomous `use_draft` share `enter_normal_draft_path`; risk is low but triangulation is incomplete for timeout×auto_send.
4. **PR chain not opened** at verify time — delivery incomplete for merge, not a product defect.

**SUGGESTION**:
1. Add one sandbox unit: activate sandbox → gap branch → assert no `pending_guidance` DB pollution / no policy write on answer.
2. Replace tautological flag-off assert with `mock_approval.assert_awaited()` (or deliver) for stronger signal.
3. Manual post-WU3 checklist still recommended before flipping `KNOWLEDGE_GAP_ENABLED`: flag-off smoke, consult freeze, answer→policy+approval, anti-reask, timeout draft, restart re-notify, reengage blocked.
4. All coverage is unit-level with mocks; acceptable for this bot stack. Manual sandbox validation against real Business API still recommended.

---

### Verdict

**PASS WITH WARNINGS**

Implementation matches specs, design, and locked product decisions. VIP freeze, flag default False, timeout ≡ use_draft, escalation precedence, anti-reask, inject order, and policy-only distill are implemented and covered by passing tests. Full suite **588 green**. Only non-blocking gaps: sandbox×guidance integration test partial, minor assertion quality, PR delivery not yet opened.

**Archive readiness**: ✅ Yes for code/spec gate (accept sandbox partial as follow-up or add tiny test before/after archive).  
**Next recommended**: `sdd-archive` (or open/merge stacked PR chain first if archive expects main).
