# Exploration: non-vip-promo-info-autoreply

## Intent

When a **non-VIP** (not on the authorized VIP allowlist) sends the trigger message `Quiero más información 🔥`, the bot should automatically send two fixed sequential promo messages **without LLM**, after a **random 2–5 minute** delay, using the existing human-like delivery path.

## Current State

**Non-VIP today = observe-only (no outbound).** In `handlers/business.py`, after resolving VIP auth:

```
authorized? ──no──► if OBSERVE_UNAUTHORIZED:
                      log + append_message(user, persist=False)
                      store chat_bc / chat_meta
                      return   ← no timer, no LLM, no delivery
                    else: log ignored → return
```

`config.OBSERVE_UNAUTHORIZED = True` (“listen without auto-reply”). Non-VIP never sets `pending_msg`, never bumps `reply_gen`, never schedules `auto_reply`.

**VIP path (contrast):**

1. Auth via `auth_users.is_authorized` → `services/auth_service.is_authorized` (JSON allowlist)
2. Escalation keywords → early exit
3. Cancel prior timer → bump `reply_gen` → `compute_reply_delay` → `auto_reply` task
4. LLM → approval (if supervised) or `deliver_vip_response`

**Delays (`handlers/timer.compute_reply_delay`):**

- Supervised: `SILENCE_MINUTES * 60` = **2 min fixed**
- Autonomous / auto-send VIP: random **`RESPONSE_DELAY_MIN..MAX` = 3–10 min**
- **No 2–5 min knob exists**

**Delivery (`services/delivery.deliver_vip_response`):** single `text` only:
`read receipt → pause → typing (len-based) → send_message(business_connection_id)`. No multi-message API. VIP delivery uses `reply_gen` staleness checks. **No `parse_mode`** on business sends (admin menus only use Markdown).

**Closest fixed-template pattern:** `services/reengagement.py` — fixed Spanish templates, **no LLM / no approval**, but **bare `bot.send_message`** (intentionally bypasses human-like delivery). VIP-only scanner, not inbound-triggered.

**Approval / sandbox:** `APPROVAL_MODE` only gates VIP `auto_reply`. Sandbox is VIP-oriented (`sandbox.is_active(chat_id)`). Non-VIP observe history is in-memory (`persist=False`).

**Recovery hazard:** `handlers/recovery.recover_runtime_on_startup` re-spawns **all** `timer_schedule` entries as `auto_reply` (LLM path). Reusing VIP timers for promo without a `kind` field would wrongly call LLM after restart.

**Telegram length:** promo message 2 is well under the 4096-char limit (~1.5–2k).

## Current flow

```
business_message
  → owner (Diana)?  → cancel timer; optional observed-example save; return
  → authorized VIP?
       YES → escalate? → else schedule auto_reply(delay) → LLM → approve? → deliver_vip_response
       NO  → OBSERVE_UNAUTHORIZED? → log/history only → RETURN  ← INSERT PROMO HERE
```

## Affected Areas

| File | Why |
|------|-----|
| `handlers/business.py` | Non-VIP early-return branch is the intercept point; must set `pending_msg` for read receipts |
| `services/delivery.py` | Single-text only; need sequential 2-message human-like send |
| `config.py` | Trigger text, fixed bodies, 2–5 min delay constants, feature flag |
| `handlers/timer.py` or new service | Delay/scheduling; **do not** blindly reuse LLM `auto_reply` |
| `handlers/recovery.py` + `state.py` | If timers persist, recovery needs `kind` (promo vs vip) |
| `services/auth_service.py` | Auth gate only (likely no code change) |
| Tests | Observe-only contracts must stay; add trigger/delay/multi-send tests |

## Approaches

| Approach | Pros | Cons | Effort |
|----------|------|------|--------|
| **A. Early intercept + thin promo service** (recommended) — match trigger in unauthorized branch; `services/promo_info.py` owns match/schedule/send; extend delivery for multi-message | Clean module boundaries (handlers I/O, services logic); no LLM/approval; reengagement-like fixed templates + VIP delivery UX; isolated tests | New path; recovery/kind if timers persist; multi-msg delivery extension | **Medium** |
| **B. Reuse `auto_reply` with fixed-response mode** | Reuses delay/gen/timer_schedule | Pollutes VIP LLM path; recovery must branch; approval/confidence dead code for promo | Medium–High |
| **C. Delay inside delivery only** (sleep 2–5 min in deliver) | Simple | Blocks poorly; no cancel on new msgs; no recovery; anti-pattern vs existing timer model | Low (wrong) |
| **D. Bare send like reengagement** | Tiny change | **Violates** “use human-like delivery” requirement | Low (reject) |

## Recommendation

**Approach A:**

1. **Config** (`config.py`): trigger string, two message bodies, `NON_VIP_PROMO_DELAY_MIN/MAX = 2, 5`, optional enable flag.
2. **Match** in non-authorized branch of `_handle_business_message` (after observe logging): exact-match policy TBD (product).
3. **Schedule** dedicated async task (not LLM `auto_reply`): random 2–5 min sleep; cancel/reschedule policy TBD.
4. **Deliver** via extended delivery: one read-receipt, then typing+send per message with short inter-message gap; skip approval/LLM/training/memory/reengagement.
5. **Set `pending_msg`** on non-VIP trigger so read receipts work.
6. **Recovery:** either omit promo from `timer_schedule`, or add `kind: "promo"` and teach recovery not to call LLM `auto_reply`.

Closest mental model: **reengagement semantics** (fixed text, no LLM, no approval) + **VIP delivery UX** (read/typing/delay).

## Risks

- **Recovery bug:** promo in `timer_schedule` without `kind` → LLM `auto_reply` on restart.
- **Spam / cost:** every trigger resend without cooldown can flood.
- **Module boundary:** logic inlined in `business.py` violates AGENTS.md.
- **Multi-message:** naive double-call of `deliver_vip_response` re-does read receipt; needs sequence helper.
- **`OBSERVE_UNAUTHORIZED=False`:** today fully ignores non-VIP — promo may need independent enable.
- **History:** non-VIP currently `persist=False`; outbound may vanish on restart unless decided otherwise.
- **VIP unchanged:** authorized users keep LLM path even if they send the trigger (assumed).

## Open product / gray-zone questions

1. **Trigger scope:** exact string only, or normalize case/whitespace/emoji variants?
2. **Audience:** only on trigger, or *every* non-VIP message gets the promo?
3. **Repeat policy:** every match, once ever, or cooldown?
4. **During 2–5 min wait:** cancel, reschedule, or ignore further messages?
5. **Non-trigger non-VIP:** remain observe-only?
6. **Notify Diana** when promo is sent?
7. **`OBSERVE_UNAUTHORIZED=False`:** still send promo?
8. **Inter-message gap** between msg1 and msg2?
9. **Persist** promo replies to SQLite history?
10. **Sandbox / admin test** path for non-VIP promo?
11. **User becomes VIP mid-delay:** still send?

## Ready for Proposal

**Yes — after product Q&A on gray zones.** Engineering path is clear; product matching/repeat/notify rules are the main unknowns.

## Proposed fixed copy (user-provided)

### Message 1

```
Holaaa 💕
Te mando mis promos 🔥
```

### Message 2

```
*Precios en pesos mexicanos 

♥ Encanto Inicial 💫 - Explora mi lado más coqu3to con 1 video y 10 fotos, una dulce introducción para conocernos mejor. 
📸 Precio $150 (10 usd)
1 video donde me toco, juego con mis labios y 🍒
10 fotos semid3snuda o con lencería

🔴 Sensualidad Revelada 🔥 -  Déjate seducir con 2 videos y 10 fotos, donde desvelo mi lado más atrevido. 
🎥 Precio: $200 (14 usd)
2 videos donde me toc@, me abro bien ric@ me +turbo y se ve mi cara más 10 fotos

❤️‍🔥 Pasión Desbordante 💋 - Vive la intensidad con 3 videos y 15 fotos, una experiencia íntima llena de emociones. 
🎬 Precio: $250 (17 usd)
Tres videos, uno con lencería muy s3nsual otro vestida y jugando muy s3xy y el último jugando con un dild0 🍒 me toco 🍑 más 15 fotos 

❤️ Intimidad Explosiva 🔞 - Sumérgete en mí con 5 videos y 15 fotos, contenido totalmente atrevido y explícit0 
🎞️ Precio: $300 (20 usd)
Set de 5 videos totalmente explícit0s tocándome hasta terminar 💦, jugando con dildo, desvistiéndome hasta quedar d3snud@, usando juguetitos y uno exclusivo c0gi3ndo montando y moviendome rico 😈 más 15 fotos de obsequio

💎 EL DIVÁN VIP 💎 
Recibe antes que nadie lo más nuevo y ric0 de mi cont3nid0 suscribiéndote a mi canal privado y exclusivo y déjate consentir por la señorita más K1nky 🔥
Subscripción mensual de $350 (23 usd)
```
