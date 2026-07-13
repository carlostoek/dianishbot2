# gray-zone-guidance Specification

## Purpose

When the LLM signals a policy gray zone (`knowledge_gap`), the system consults Diana for doctrine, freezes all VIP I/O, distills the answer into a reusable topic policy, regenerates the draft, and re-enters the normal approval/deliver path. This is a fourth flow distinct from approval, escalation, and per-user notes.

## Requirements

### Requirement: Explicit knowledge_gap signal

The system MUST detect gray zones only via optional LLM fields `knowledge_gap` (boolean) and `gap_question` (string). Low `confidence` MUST NOT open a guidance consult. Missing gap fields MUST normalize to `false` / empty.

#### Scenario: Gap fields open consult path

- GIVEN `KNOWLEDGE_GAP_ENABLED` is true
- AND a successful VIP LLM response with `knowledge_gap=true` and non-empty `gap_question`
- AND topic is not an escalation topic (or is a known false positive)
- AND no active policy match
- WHEN the post-LLM timer branch runs
- THEN the system opens a guidance consult for Diana
- AND does not treat low confidence alone as a gap

#### Scenario: Low confidence without gap does not consult

- GIVEN a VIP LLM response with low confidence and `knowledge_gap` false or missing
- WHEN the post-LLM timer branch runs
- THEN the system MUST NOT open guidance
- AND continues the existing approval/deliver path

### Requirement: Escalation precedence over gap

When both an escalation-topic condition and a knowledge gap are present, escalation MUST win. The system MUST ignore the gap and MUST NOT open guidance.

#### Scenario: Escalation topic suppresses gap

- GIVEN LLM topic is an escalation topic not marked known false-positive
- AND `knowledge_gap=true` with a gap question
- WHEN the post-LLM timer branch runs
- THEN the existing escalation path runs
- AND no `pending_guidance` is created

### Requirement: Feature flag default off is zero behavior change

`KNOWLEDGE_GAP_ENABLED` MUST default to false. When false, the system MUST ignore gap fields and MUST behave identically to the pre-change VIP path (no consult, no freeze, no policy-driven regen from gap).

#### Scenario: Flag off ignores gap fields

- GIVEN `KNOWLEDGE_GAP_ENABLED` is false
- AND LLM returns `knowledge_gap=true` with a gap question
- WHEN the post-LLM timer branch runs
- THEN no guidance consult opens
- AND save_example / approval / deliver proceed as today

### Requirement: Anti-reask via policy match before consult

Before opening a Diana consult, the system MUST attempt to match active topic policies against the free-form topic, gap question, and recent user text. On a non-empty match, the system MUST regenerate the draft once with matched policies injected and MUST NOT notify Diana for that turn. The system MUST limit auto-regen to one attempt per timer fire.

#### Scenario: Policy match regenerates without DM

- GIVEN `KNOWLEDGE_GAP_ENABLED` is true and `knowledge_gap=true`
- AND at least one active policy matches the turn
- WHEN the gap branch runs
- THEN the system regenerates once with policies in context
- AND does not create `pending_guidance` or DM Diana for the gap
- AND then enters the normal save/approve/deliver path with the regenerated draft

#### Scenario: No match opens consult

- GIVEN gap is true and no active policy matches
- WHEN the gap branch runs
- THEN a guidance request is created with status `pending`
- AND Diana is notified with the consult UI
- AND the timer finishes without save_example and without VIP send

### Requirement: VIP freeze while guidance pending

While `pending_guidance` is open for a chat, the system MUST NOT perform any VIP-channel I/O: no text send (including wait/filler), no read receipt, no typing indicator, no reengagement message, no approval delivery, no auto-send of the gap draft, and no `save_example` of the gap draft.

#### Scenario: Open guidance blocks all VIP side effects

- GIVEN an open `pending_guidance` for a VIP chat
- WHEN any background path would send, mark read, type, reengage, deliver, or save the gap draft
- THEN those VIP-facing actions MUST NOT occur
- AND only Diana DM / internal state updates MAY occur

### Requirement: Diana consult UI with g: callbacks

The system MUST notify Diana via admin DM with a consult distinct from approval/escalation copy, including VIP identity, topic, gap question, recent context, and tentative draft. Buttons MUST use prefix `g:` with actions: `g:answer:{id}`, `g:use_draft:{id}`, `g:skip:{id}`.

#### Scenario: Answer starts free-text capture

- GIVEN an open guidance request
- WHEN Diana taps `g:answer:{id}`
- THEN the system arms free-text capture for that guidance id
- AND prompts Diana for an answer

#### Scenario: Use draft enters normal draft path

- GIVEN an open guidance request with stored draft
- WHEN Diana taps `g:use_draft:{id}`
- THEN the request is closed without distill (status `skipped` or equivalent non-answered close)
- AND the system re-enters the normal pipeline with the stored draft (supervised: pending approval + notify; autonomous: deliver)
- AND VIP freeze ends for that consult

#### Scenario: Skip closes without VIP send

- GIVEN an open guidance request
- WHEN Diana taps `g:skip:{id}`
- THEN the request is closed without VIP send
- AND no draft is delivered or submitted for approval from that consult
- AND VIP freeze ends for that consult

### Requirement: Free-text capture mutual exclusion

Guidance free-text capture MUST be mutually exclusive with note and correction awaits. Arming `g:answer` MUST clear note/correction awaits. Arming note/correction or completing approval/training actions that clear awaits MUST clear guidance answer await (with prompt restore if required by existing patterns).

#### Scenario: Answer clears other awaits

- GIVEN Diana has an active note or correction await
- WHEN she taps `g:answer` for a guidance id
- THEN note/correction awaits are cleared
- AND only guidance answer await remains armed

#### Scenario: Note/correct clears guidance await

- GIVEN guidance answer await is armed
- WHEN Diana arms note or correction (or an action that clears those awaits)
- THEN guidance answer await is cleared

### Requirement: Answer path distill then regen into normal path

After Diana submits a free-text answer for an open guidance, the system MUST distill doctrine into a topic policy, link it to the guidance request (status `answered`), clear pending guidance, and regenerate the VIP draft with policies injected. If `reply_gen` still matches the consult generation: supervised VIP MUST enter pending approval + notify; autonomous (`auto_send`) VIP MUST deliver (with existing low-confidence notify rules if applicable). If generation is stale, the system MUST NOT deliver the old draft and MUST notify Diana that the consult closed because the VIP wrote again.

#### Scenario: Supervised answer completes to approval

- GIVEN open guidance for a supervised VIP and matching generation
- WHEN Diana sends a free-text answer
- THEN a policy is stored (or degraded raw-summary policy on distill failure)
- AND a regenerated draft enters pending approval with Diana notify
- AND VIP freeze is released

#### Scenario: Autonomous answer delivers

- GIVEN open guidance for an autonomous VIP and matching generation
- WHEN Diana sends a free-text answer
- THEN after distill and regen the system delivers via the normal delivery path
- AND does not open pending approval for that VIP

#### Scenario: Distill failure still proceeds

- GIVEN open guidance and distill fails
- WHEN the answer path continues
- THEN a usable policy is still stored from the raw answer (degraded)
- AND regen + normal path still run

### Requirement: Twelve-hour timeout equals use_draft

Open guidance older than 12 hours MUST time out with status `timeout` and MUST follow the same outcome as `g:use_draft`: re-enter the existing draft/send pipeline with the stored tentative draft. VIP freeze MUST hold until that transition. Diana MUST be notified that timeout opened the normal draft/send path.

#### Scenario: Timeout opens normal draft path

- GIVEN pending guidance older than 12 hours still open
- WHEN the timeout check runs
- THEN status becomes `timeout`
- AND supervised VIP gets pending approval + notify for the stored draft
- OR autonomous VIP gets normal delivery of the stored draft
- AND no special VIP wait message is sent

### Requirement: Owner Business message supersedes guidance

If Diana (owner) writes in the Business chat while guidance is open for that chat, the system MUST mark the guidance `superseded`, drop pending guidance, and MUST NOT later deliver that consult’s draft.

#### Scenario: Owner inbound supersedes

- GIVEN open pending guidance for a chat
- WHEN the owner sends a Business message in that chat
- THEN guidance status is `superseded`
- AND pending guidance is cleared
- AND no further VIP send from that consult occurs

### Requirement: VIP new message stales open guidance

When the VIP sends a new message that advances generation tracking, an open guidance for the prior generation MUST become stale: the system MUST NOT deliver or approve the old consult draft for that generation.

#### Scenario: New VIP message blocks old delivery

- GIVEN open guidance bound to generation N
- WHEN the VIP sends a new message advancing generation past N
- THEN any later answer/timeout/use_draft for generation N MUST NOT deliver the old draft to the VIP
- AND Diana is informed when resolution hits a stale generation

### Requirement: Persist pending_guidance and re-notify after restart

The system MUST persist `pending_guidance` in runtime state across restarts. Free-text answer await state MUST NOT be required to survive restart. After recovery load, the system MUST re-notify Diana for open guidances (or refresh an existing notify message when identity is known) so she can still act.

#### Scenario: Restart preserves open consults

- GIVEN open pending guidance entries at shutdown
- WHEN the bot recovers runtime state
- THEN those entries are restored
- AND Diana receives re-notification for each still-open guidance

### Requirement: Sandbox does not pollute real policies

In sandbox mode, the system MUST NOT write durable real topic policies from guidance and MUST NOT open a real Diana consult that trains production doctrine (synthetic/offline gates equivalent to sibling training flows are allowed).

#### Scenario: Sandbox blocks production policy writes

- GIVEN sandbox mode is active for a session
- WHEN a gap would otherwise distill and store a policy
- THEN no production topic policy is created
- AND production guidance training side effects are gated
