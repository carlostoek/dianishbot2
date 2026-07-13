# Proposal: Gray-Zone Guidance → Topic Policies

## Intent

When the LLM hits a **policy gray zone** (limits, exceptions, one-off commercial asks), Diana has no flow for reusable doctrine. Approval = draft quality gate; escalation = human takeover; notes = per-user memory. None yield high-weight global topic policy.

**Why now:** Gray zones force weak drafts or full escalation with no learning; the same ambiguity re-asks forever.

**Success:** `knowledge_gap` opens consult → VIP freeze → Diana answers → distill → high-priority policy inject → regen → normal approval/deliver; later turns reuse policy without re-asking.

## Scope

### In Scope
- LLM fields `knowledge_gap` + `gap_question` (not low confidence)
- Match `topic_policies` first (anti-reask); else consult
- VIP freeze while `pending_guidance` (no VIP I/O)
- DM UI `g:` (answer / use_draft / skip) + free-text capture
- Distill → high-priority `topic_policies` + `guidance_requests`
- Inject policies as mandatory instructions; regen → normal path
- Timeout 12h ≡ `g:use_draft`; flag `KNOWLEDGE_GAP_ENABLED` default False
- Module `services/knowledge.py`; handlers I/O only
- Persist `pending_guidance`; block reengagement; recovery re-notify
- Per-VIP `auto_send`: after distill+regen deliver; else approval

### Out of Scope
- Vector search; auto few-shot from distill; full policy editor
- Escalation/approval UX redesign; example weight columns
- Sandbox real-policy training; any VIP wait signal (msg/read/typing)

## Capabilities

### New Capabilities
- `gray-zone-guidance`: detect gap, anti-reask, consult, VIP freeze, distill, timeout, `g:` UI, regen into normal path
- `topic-policies`: store, keyword/topic match, high-weight prompt injection

### Modified Capabilities
- None (`non-vip-promo-autoreply`, `multi-message-delivery` unrelated)

## Approach

Fourth flow after escalation-topic check, before `save_example` / approval / deliver:

1. Extend schema with optional gap fields; strict prompt criteria
2. `match_policies` → hit: one regen with policies; miss: open consult
3. Zero VIP I/O until answer / use_draft / skip / timeout / supersede / gen stale
4. Distill free text → policy; inject after memory, before few-shots
5. Re-enter supervised/autonomous pipeline (mirror escalation regen)

Canonical design: `docs/feedback-design.md` (§0.1 freeze locked).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `services/knowledge.py` | New | Schema, match, distill, policy block |
| `handlers/callbacks/guidance.py` | New | `g:` callbacks + free-text answer |
| `services/llm.py` | Modified | Schema, return shape, policy inject |
| `handlers/timer.py` | Modified | Gap hook; freeze; no save/send |
| `state.py` / recovery / reengagement | Modified | `pending_guidance`; block reengage |
| `config.py`, `training.py`, router, business, sandbox | Modified | Flags, schema init, await order, supersede |
| `tests/unit/` | New/Modified | Freeze, match, timeout, inject |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| LLM over-fires `knowledge_gap` | Med | Flag default off; strict criteria |
| VIP freeze leak (read/typing) | Med | Invariant tests; never deliver/read/type |
| Topic-only miss on policies | Med | Keyword scoring mandatory |
| Distill failure | Low | Degraded raw-answer policy; still proceed |

## Rollback Plan

1. `KNOWLEDGE_GAP_ENABLED=False` (behavior = today).
2. Revert deploy; optional `is_active=0` on policies.
3. Clear runtime `pending_guidance` if needed.

## Dependencies

- Timer/approval/escalation paths; training SQLite; reengagement hooks
- Design: `docs/feedback-design.md`

## Success Criteria

- [ ] Gap + no match → Diana DM only; VIP freeze holds
- [ ] Policy match → no re-ask; inject + one regen → normal path
- [ ] Answer → distill → regen → approval or auto_send deliver
- [ ] 12h timeout ≡ `g:use_draft`; escalation wins over gap
- [ ] Flag off unchanged; tests cover freeze/match/timeout/inject

## Locked Product Assumptions

| Item | Lock |
|------|------|
| VIP freeze | Zero VIP I/O while `pending_guidance` |
| Timeout | 12h → normal draft path (`g:use_draft`) |
| Flag | `KNOWLEDGE_GAP_ENABLED=False` |
| Precedence | Escalation topic > knowledge_gap > approve/deliver |
| First slice | Policy-only (no auto few-shot from distill) |
| Callbacks | `g:answer` / `g:use_draft` / `g:skip` |
| Delivery | auto-chain, stacked-to-main (WU1→WU2→WU3) |
