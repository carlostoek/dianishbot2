# non-vip-promo-autoreply Specification

## Purpose

When a non-VIP business chat sends the exact promo-info trigger, the system auto-replies with two fixed human-like messages after a random delay — without LLM, approval, Diana notify, training, memory, or reengagement. VIPs and non-trigger non-VIPs keep existing behavior.

## Requirements

### Requirement: Exact trigger match after strip only

The system MUST treat an inbound text as a promo trigger only when, after stripping leading/trailing whitespace, it equals exactly `Quiero más información 🔥`. The system MUST NOT case-fold, normalize emoji, or accept partial/substring matches.

#### Scenario: Exact trigger matches

- GIVEN a non-VIP inbound business message whose stripped text is `Quiero más información 🔥`
- WHEN the message is processed and the feature is enabled
- THEN the system schedules the promo autoreply path

#### Scenario: Near-miss does not match

- GIVEN a non-VIP inbound whose stripped text differs in case, emoji, or surrounding words
- WHEN the message is processed
- THEN the system MUST NOT schedule promo autoreply

### Requirement: Non-VIP audience only

The system MUST offer promo autoreply only to chats that are not on the VIP allowlist at trigger time. Authorized VIP users MUST keep the existing LLM auto-reply path even when they send the trigger text.

#### Scenario: Non-VIP trigger enters promo path

- GIVEN a chat that is not authorized as VIP
- AND the inbound stripped text is the exact trigger
- AND the feature is enabled
- WHEN the message is processed
- THEN promo autoreply is scheduled
- AND the VIP LLM path is not invoked

#### Scenario: VIP trigger keeps LLM path

- GIVEN an authorized VIP chat
- AND the inbound stripped text is the exact trigger
- WHEN the message is processed
- THEN the existing VIP LLM auto-reply path runs
- AND promo autoreply is not scheduled

### Requirement: Non-trigger non-VIP remains observe-only

For non-VIP chats, inbound messages that are not the exact trigger MUST remain observe-only (log/history as today) with no outbound promo, no timer, and no LLM.

#### Scenario: Non-trigger non-VIP silence

- GIVEN a non-VIP chat and any non-trigger inbound text
- WHEN the message is processed
- THEN no promo is scheduled
- AND no VIP auto-reply runs
- AND observe-only behavior is preserved

### Requirement: No LLM, approval, or Diana notify on promo path

The promo path MUST NOT call the LLM, MUST NOT enter the approval gate, and MUST NOT notify Diana (admin DM) when a promo is scheduled or sent.

#### Scenario: Promo send has no supervised side effects

- GIVEN a scheduled promo for a non-VIP chat
- WHEN delivery fires successfully
- THEN no LLM call is made
- AND no approval request is created
- AND Diana is not notified about the promo send

### Requirement: Random delay then two sequential messages

After a matching non-VIP trigger, the system MUST wait a random delay uniformly chosen between 2 and 5 minutes (inclusive bounds as configured), then deliver exactly two sequential human-like messages (Message 1 then Message 2) via multi-message delivery.

#### Scenario: Delay bounds and two-message send

- GIVEN a non-VIP exact trigger with feature enabled
- WHEN promo is scheduled and the wait completes without abort
- THEN the pre-delivery wait is between 2 and 5 minutes
- AND Message 1 is delivered before Message 2
- AND both messages use human-like delivery semantics

### Requirement: Message 1 first-time vs repeat variants

Message 1 MUST depend on durable "already informed" state for the `chat_id`:

- **First-time** (not yet informed) MUST be exactly:

```
Holaaa 💕
Te mando mis promos 🔥
```

- **Repeat** (already informed) MUST be exactly:

```
Holis 😁 
Claro, te mando de nuevo mis promos. Los nombres son los mismos pero es contenido nuevo y diferente.
```

#### Scenario: First-time Message 1

- GIVEN `chat_id` is not marked already informed
- WHEN promo delivery runs
- THEN Message 1 body equals the first-time text above

#### Scenario: Repeat Message 1

- GIVEN `chat_id` is already marked informed
- WHEN promo delivery runs again after a later trigger
- THEN Message 1 body equals the repeat text above
- AND Message 1 is not the first-time body

### Requirement: Message 2 fixed promo block

Message 2 MUST always be the fixed promo block below (leetspeak preserved as provided), regardless of first-time vs repeat:

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

#### Scenario: Message 2 always fixed

- GIVEN either first-time or already-informed state
- WHEN promo delivery runs
- THEN Message 2 equals the fixed promo block above character-for-character

### Requirement: Durable already-informed tracking per chat_id

The system MUST persist an "already informed" flag (or equivalent timestamp) in SQLite keyed by `chat_id`. The system MUST mark the chat informed only after both promo messages have been sent successfully. Failed or aborted delivery MUST NOT mark the chat informed.

#### Scenario: Mark after successful send

- GIVEN a first-time promo delivery that sends Message 1 and Message 2 successfully
- WHEN delivery completes
- THEN SQLite records the chat as already informed

#### Scenario: No mark on abort or failure

- GIVEN promo delivery aborts (e.g. now VIP) or fails before both messages succeed
- WHEN the path exits
- THEN the chat MUST NOT be marked informed solely due to that attempt

### Requirement: Ignore inbound during wait

While a promo wait is pending for a chat, further inbound messages from that chat MUST be ignored for scheduling purposes: the system MUST NOT cancel, reschedule, or stack additional promo timers for that pending wait. The original timer MUST fire as scheduled (subject to send-time auth re-check).

#### Scenario: Second message during wait does not reschedule

- GIVEN a non-VIP chat with an active promo wait
- WHEN another inbound message arrives before the wait ends
- THEN the existing wait is neither cancelled nor rescheduled
- AND no second promo wait is stacked for that chat

### Requirement: Re-check auth at send time

At the moment delivery would start after the wait, the system MUST re-check VIP authorization. If the chat is now VIP, the system MUST abort promo delivery without sending Message 1 or Message 2.

#### Scenario: Abort when user became VIP during wait

- GIVEN a promo wait was scheduled while the chat was non-VIP
- AND the chat becomes authorized VIP before the wait ends
- WHEN the wait completes
- THEN no promo messages are sent
- AND the VIP path is not auto-triggered solely by this abort

### Requirement: Feature flag NON_VIP_PROMO_AUTOREPLY_ENABLED

Promo autoreply MUST be gated by `NON_VIP_PROMO_AUTOREPLY_ENABLED` (default `true`). When false, non-VIP triggers MUST NOT schedule promo (observe-only / silence as applicable). The flag MUST operate independently of `OBSERVE_UNAUTHORIZED`.

#### Scenario: Flag off disables promo

- GIVEN `NON_VIP_PROMO_AUTOREPLY_ENABLED` is false
- AND a non-VIP exact trigger arrives
- WHEN the message is processed
- THEN promo is not scheduled

#### Scenario: Flag on independent of observe setting

- GIVEN `NON_VIP_PROMO_AUTOREPLY_ENABLED` is true
- AND a non-VIP exact trigger arrives
- WHEN the message is processed
- THEN promo is scheduled regardless of `OBSERVE_UNAUTHORIZED` value

### Requirement: pending_msg and side-effect exclusions

On a matching non-VIP trigger that schedules promo, the system MUST set `pending_msg` so read receipts can run. The promo path MUST NOT save training examples, run memory extraction, or touch idle reengagement for that flow.

#### Scenario: pending_msg set without training side effects

- GIVEN a non-VIP exact trigger schedules promo
- WHEN scheduling completes
- THEN `pending_msg` is set for the chat
- AND training save, memory extraction, and reengagement touch are skipped for the promo path

### Requirement: Recovery must not route promo as VIP auto_reply

Process restart or runtime recovery MUST NOT rehydrate a pending promo wait as VIP `auto_reply` (LLM path). Promo waits MUST either be omitted from VIP `timer_schedule` recovery or be distinctly typed so recovery never invokes the LLM path for them.

#### Scenario: Restart does not LLM-recover promo

- GIVEN a promo wait was in flight before process restart
- WHEN recovery runs on startup
- THEN the system does not start VIP `auto_reply` / LLM for that promo wait
