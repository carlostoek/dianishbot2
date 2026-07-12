# multi-message-delivery Specification

## Purpose

Deliver an ordered sequence of fixed outbound texts on a business connection with human-like behavior: a single read-receipt phase, then typing + send per message, with a short gap between messages. Used by non-VIP promo autoreply (and available for other multi-message fixed sends).

## Requirements

### Requirement: Single read receipt then sequential sends

When delivering N ordered messages (N ≥ 1) on a business chat, the system MUST perform human-like read-receipt behavior at most once at the start of the sequence, then for each message in order: show typing (duration appropriate to message length) and send that message. The system MUST NOT re-run a full independent single-message delivery (including a second read-receipt cycle) for each message in the sequence.

#### Scenario: Two-message sequence has one read phase

- GIVEN a two-message delivery request on a business connection
- WHEN multi-message delivery runs
- THEN read-receipt humanization runs once before the first send
- AND Message 1 is sent before Message 2
- AND a second full read-receipt cycle is not performed between Message 1 and Message 2

#### Scenario: Order preserved

- GIVEN messages [A, B] requested in that order
- WHEN delivery completes successfully
- THEN A is sent before B
- AND no reordering occurs

### Requirement: Short inter-message gap

Between consecutive messages in a multi-message delivery, the system MUST insert a short human-like pause (on the order of typing-duration seconds, not minutes) before starting the next message's typing/send. The inter-message gap MUST NOT replace or extend the pre-delivery 2–5 minute promo wait (that wait is owned by the caller).

#### Scenario: Gap between message 1 and 2

- GIVEN a two-message multi-message delivery after any pre-delivery wait has already completed
- WHEN Message 1 has been sent
- THEN a short pause occurs
- AND then typing + send for Message 2 runs

### Requirement: Partial failure does not claim full success

If any message in the sequence fails to send, the delivery MUST surface failure to the caller. The caller (promo autoreply) MUST treat the overall promo send as unsuccessful for "already informed" marking when not all messages were sent.

#### Scenario: Second message send failure

- GIVEN Message 1 sent successfully and Message 2 send fails
- WHEN multi-message delivery returns
- THEN the result indicates incomplete/failed delivery
- AND success is not reported for the full sequence

### Requirement: Business connection send path

Multi-message delivery MUST send via the existing business-connection outbound path (same channel family as VIP human-like delivery), not a bare admin-only or non-business shortcut that skips human-like typing behavior.

#### Scenario: Uses human-like business send

- GIVEN a multi-message delivery for a business chat with a valid business connection
- WHEN each message is sent
- THEN sends use the business-connection delivery path with typing indication
