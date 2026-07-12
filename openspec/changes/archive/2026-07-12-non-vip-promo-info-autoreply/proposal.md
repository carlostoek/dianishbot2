# Proposal: Non-VIP Promo Info Autoreply

## Intent

Non-VIP users who send `Quiero más información 🔥` get observe-only silence today, wasting conversion on people who already asked for pricing.

**Why now:** CTA exists; VIP LLM path must stay untouched; fixed-template human-like delivery avoids LLM/approval cost.

**Success:** non-VIP exact trigger → random 2–5 min wait → two sequential human-like messages (first vs repeat intro + fixed promo block); non-trigger non-VIP stay observe-only; VIPs unchanged.

## Scope

### In Scope
- Exact trigger after strip only (no case fold / partial)
- Non-VIP only; VIP keeps LLM path even on trigger text
- Auto two-message reply: no LLM, no approval, no Diana notify
- Random 2–5 min pre-delivery delay; short typing gap between messages
- First vs repeat Message 1 via durable SQLite flag/timestamp per `chat_id`
- During wait: ignore inbound; no cancel/reschedule; fire on timer
- Re-check auth at send; abort if now VIP
- Flag `NON_VIP_PROMO_AUTOREPLY_ENABLED` (default true), independent of observe
- Set `pending_msg`; skip training/memory/reengagement
- Recovery-safe timers (omit VIP `timer_schedule` or `kind: promo`)
- Tests: match, audience, delay, multi-send, abort-on-VIP, observe-only non-trigger

### Out of Scope
- LLM/approval; fuzzy triggers; extra cooldown; sandbox admin UX
- Full non-VIP history persistence; VIP delay/reengagement/allowlist changes

## Capabilities

### New Capabilities
- `non-vip-promo-autoreply`: match, schedule, first/repeat copy, multi-send, SQLite informed tracking, auth re-check, flag
- `multi-message-delivery`: one read receipt, then typing+send per message with short gap

### Modified Capabilities
- None (no existing `openspec/specs/` baselines)

## Approach

**Approach A:** intercept unauthorized branch in `handlers/business.py` + thin `services/promo_info.py` + extend `services/delivery.py`. Do **not** reuse LLM `auto_reply`.

1. Config: trigger, msg1 variants, msg2 promo block (leetspeak as provided), 2–5 min delay, flag
2. Match after observe logging (strip-only exact)
3. Dedicated async task (not VIP recovery path)
4. Deliver: read once → msg1 → short gap → msg2; mark informed after success
5. Never rehydrate promo timers as VIP `auto_reply`

Model: reengagement semantics (fixed text) + VIP delivery UX (read/typing).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `handlers/business.py` | Modified | Non-VIP trigger intercept; `pending_msg` |
| `services/promo_info.py` | New | Match, schedule, first/repeat, orchestration |
| `services/delivery.py` | Modified | Multi-message human-like sequence |
| `config.py` | Modified | Trigger, copy, delays, flag |
| timer / recovery / `state.py` | Modified | Isolate promo from VIP recovery |
| SQLite layer | Modified | Durable informed flag per `chat_id` |
| `tests/` | Modified | New contracts; keep observe-only non-trigger |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Promo timer recovered as LLM path | Med | Omit schedule or `kind` + recovery branch |
| Double read-receipt on dual deliver | Med | Single multi-message helper |
| Logic inlined in handler | Med | Thin call into `promo_info` |

## Rollback Plan

1. Set `NON_VIP_PROMO_AUTOREPLY_ENABLED=False`.
2. Revert deploy. Optional: drop informed-tracking rows (VIP unaffected).

## Dependencies

- `is_authorized` + business-connection send path; existing SQLite patterns; no new services

## Success Criteria

- [ ] Non-VIP exact trigger → 2–5 min → first/repeat msg1 + fixed msg2, human-like
- [ ] Non-trigger non-VIP observe-only; VIP trigger still LLM
- [ ] No LLM/approval/notify/training/memory/reengagement on promo path
- [ ] Abort if VIP during wait; restart never routes promo via VIP `auto_reply`
- [ ] Flag off restores silence; automated tests cover contracts

## Locked Decisions & Technical Defaults

| Item | Decision |
|------|----------|
| Trigger / audience | Exact strip match; non-VIP only |
| Wait policy | 2–5 min; ignore inbound; abort if VIP at send |
| Copy | First/repeat msg1; fixed msg2 from exploration |
| Notify / arch | No Diana notify; Approach A |
| Defaults | Flag default true; typing-seconds inter-gap; `pending_msg` set; skip training/memory/reengagement; promo timers not VIP-rehydrated |
