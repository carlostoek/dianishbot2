# Design: Gray-Zone Guidance → Topic Policies

Product rationale: [`docs/feedback-design.md`](../../../docs/feedback-design.md). This file is the implementer contract — do not diverge from locks below.

## Technical Approach

Fourth flow after LLM success + escalation-topic handling, **before** `save_example` / approval / deliver. When `KNOWLEDGE_GAP_ENABLED` and model sets `knowledge_gap` + `gap_question`:

1. `match_policies` → hit: **one** regen with policy inject → normal path.
2. Miss: open consult (`guidance_requests` + `pending_guidance`), DM Diana only, **VIP freeze**, finish timer without save/send.
3. Answer → distill → `topic_policies` → regen → supervised approval **or** autonomous deliver (mirror `_generate_from_escalation`).
4. Timeout 12h ≡ `g:use_draft` (stored draft → normal pipeline).

Logic in `services/knowledge.py`; handlers = Telegram I/O only.

## Architecture Decisions

| Decision | Choice | Rejected | Rationale |
|----------|--------|----------|-----------|
| Flow | New `g:` / `pending_guidance` | Overload note/approval | Doctrine ≠ draft edit ≠ note |
| Detection | `knowledge_gap` + `gap_question` | Low confidence | Confidence = generic wording |
| Precedence | Escalation > gap > approve/deliver | Gap first | Crisis must not become Q&A |
| Storage | Tables in training DB | New DB / notes | Same as escalations / promo |
| Service | `services/knowledge.py` | Logic in handlers | Module boundaries |
| Inject | Mandatory instruction block after memory, before few_shots | Few-shot slots | Constitutional > style |
| Prompt order | base→temporal→memory→**policies**→few_shots→escalation_fp→format | Other orders | Person < doctrine < style |
| First slice | Policy only | Auto few-shot distill | Avoid noisy examples |
| Gap open | No `save_example` | Save pending draft | Weak drafts must not train |
| Runtime | Persist `pending_guidance`; await runtime-only | Persist await | Escalations vs notes pattern |
| Callbacks | `g:action:id` (3-part) | Free-form | Align with `a:`/`t:`/`e:` |
| Flag | `KNOWLEDGE_GAP_ENABLED=False` | Default on | Measure before noise |
| Timeout | 12h ≡ `g:use_draft` | Filler / cancel | Product lock |
| VIP freeze | Zero VIP I/O while pending | Wait text / read / typing | Product lock §0.1 |
| Reengage | Block on open guidance | Approval-only block | Freeze completeness |
| Post-answer | Supervised→approval; auto_send→deliver | Always approval | Real timer branch |
| Owner write | Supersede guidance | Keep open | Human owns thread |
| Sandbox | No real policy/consult writes | Train sandbox | No prod pollution |

## VIP Freeze Invariant

While `pending_guidance` exists for chat C: never call `deliver_vip_response`, `mark_as_read`, or `simulate_typing`; no reengage; no `save_example` of gap draft. Diana DM only. Read/typing only later inside normal delivery after resolve/timeout.

## Data Flow

```
auto_reply
  get_diana_response → (text, conf, topic, knowledge_gap, gap_question, failure)
  failure? → fail path STOP
  escalado_* & !FP? → escalate STOP
  flag & gap & question?
    match_policies → HIT: one regen → save → approve|deliver
                  → MISS: guidance_requests + pending_guidance
                           notify Diana (g:) → finish timer [NO save/VIP I/O]
  else → save → approve|deliver

g:answer + free text → distill → policy → regen → approve|deliver (or stale STOP)
g:use_draft | timeout 12h → save draft → approve|deliver
g:skip | owner Business | VIP gen bump → close without VIP send
```

## File Surface

| File | Action | Role |
|------|--------|------|
| `services/knowledge.py` | Create | Schema, CRUD, match, distill, policy block |
| `handlers/callbacks/guidance.py` | Create | Notify, `g:`, free-text, post-answer regen |
| `config.py` | Modify | Flag False, timeout 12h, priority 100 |
| `services/llm.py` | Modify | Schema/parse gap fields; 6-tuple; policy inject |
| `handlers/timer.py` | Modify | Hook after escalation, before `save_example` |
| `state.py` | Modify | `pending_guidance` persist; await not; snapshot/load |
| `handlers/callbacks/__init__.py` | Modify | Route `g:` |
| `handlers/router.py` | Modify | Await order: admin_note → **guidance** → note → correction |
| `services/training.py` | Modify | `knowledge.init_schema` from `init_db` |
| `services/reengagement.py` | Modify | `_has_pending_guidance` block |
| `services/data_pause.py` / `sandbox.py` | Modify | Clear/synthetic gates |
| `handlers/recovery.py` | Modify | Re-notify open guidances |
| `handlers/business.py` | Modify | Owner inbound → supersede |
| Admin cmds + tests + `AGENTS.md` | Modify | `/politicas`, unit coverage, docs |

## Interfaces

**Config:** `KNOWLEDGE_GAP_ENABLED=False`, `GUIDANCE_TIMEOUT_HOURS=12`, `GUIDANCE_POLICY_PRIORITY=100`.

**LLM:** Extend `DIANA_RESPONSE_SCHEMA` with optional `knowledge_gap` (bool→False), `gap_question` (str→`""`); required stays response/confidence/topic; `additionalProperties: False`. Return:

```python
(response, confidence, topic, knowledge_gap, gap_question, failure)
# call sites: timer, approval regen, escalation gen, tests
```

Gap only for new/contradictory/commitment doctrine; never FAQ, pure tone, or escalado. Dual signal → escalation wins.

**`knowledge.py` (promo_info-style `db` wire):** `init_schema`, `match_policies`, `build_policy_block`, CRUD for policies/requests, `distill_guidance`, list/deactivate. Match: topic +100, keyword +10 each, keep ≥100 or ≥1 keyword, top 5. Distill fail → raw truncated policy, still proceed. Tables: `topic_policies`, `guidance_requests` (statuses: pending|answered|skipped|timeout|superseded) — SQL in feedback-design §6.

**Runtime:**

```python
pending_guidance: dict[int, dict]  # persisted (diana_runtime.json)
awaiting_guidance_answer: dict[int, int]  # admin→gid, NOT persisted
```

**Callbacks:** `g:answer` (await free text), `g:use_draft` (normal draft path), `g:skip` (no send). Mutual exclusion with note/correction.

**Shared helper (recommended):** extract save→approve|deliver used by timer, use_draft, timeout, post-distill (same as `_generate_from_escalation` / `auto_reply`).

## Testing

| Layer | Focus |
|-------|--------|
| Unit | Schema, match, block, gap parse defaults |
| Unit | Timer freeze (no save/deliver/read/type) |
| Unit | Reengage block, timeout≡use_draft, inject order |
| Unit | Callbacks, free-text, stale gen, mutual exclusion |
| Suite | Flag off → existing path unchanged |

Strict TDD. `PYTHONPATH=. pytest tests/`.

## Rollout

Flag default False. Idempotent schema via `init_db`. Rollback: flag off; optional `is_active=0`; clear runtime pending.

## Work Units (stacked-to-main, auto-chain)

| WU | Scope | Done when |
|----|-------|-----------|
| WU1 Foundation | Schema, knowledge, LLM fields+flag, init/conftest | Flag-off path identical |
| WU2 Consult+freeze | pending, `g:` UI, timer branch, reengage/recovery/owner/sandbox | Freeze + re-notify hold |
| WU3 Inject+regen+timeout | Policy inject, distill→regen, 12h, admin list | Happy-path covered |

## Open Questions

None blocking.
