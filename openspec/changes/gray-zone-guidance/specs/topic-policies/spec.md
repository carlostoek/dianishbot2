# topic-policies Specification

## Purpose

Durable, high-weight topic doctrine distilled from gray-zone guidance (and future sources). Policies are matched by free-form topic plus keywords and injected as mandatory instructions so the LLM does not re-ask the same gap.

## Requirements

### Requirement: Store policy with full doctrine fields

The system MUST persist each active policy with at least: `topic`, `keywords` (list), `policy_summary`, `example_response`, `priority`, audit fields `source_question` and `source_answer_raw`, timestamps, and soft-active flag. Distilled policies from guidance MUST default to high priority (product default 100 unless configured otherwise).

#### Scenario: Distill creates complete policy row

- GIVEN a successful distill of a Diana guidance answer
- WHEN the policy is stored
- THEN topic, keywords, summary, example, high priority, and raw audit fields are all present
- AND the policy is active by default

#### Scenario: Degraded distill still stores audit trail

- GIVEN distill failure falls back to raw answer as summary
- WHEN the policy is stored
- THEN `source_answer_raw` (and source question when available) are retained
- AND the policy remains matchable and injectable

### Requirement: Match by free-form topic and keywords

Policy matching MUST score active policies using free-form LLM topic equality (normalized) plus keyword hits against the provided texts (gap question, last user message, and other match inputs). Matching MUST NOT rely solely on pre-LLM `TOPIC_MAP` labels. Exact topic match MUST score strongly; each distinct keyword hit MUST add score. Policies meeting the score floor (topic match and/or at least one keyword hit, per product floor) MUST be eligible; results MUST be ordered by priority then recency, with a capped top-N for prompt size.

#### Scenario: Keyword hit matches without identical topic string

- GIVEN an active policy with topic `limites_contenido` and keyword `videollamada`
- AND the current turn topic string differs but user/gap text contains `videollamada`
- WHEN policies are matched
- THEN that policy is included in the match set

#### Scenario: Exact topic alone can match

- GIVEN an active policy whose normalized topic equals the turn topic
- WHEN policies are matched with no keyword overlap
- THEN the policy is still eligible via topic score

#### Scenario: Inactive policies never match

- GIVEN a soft-deactivated policy that would otherwise score
- WHEN policies are matched
- THEN it is excluded

### Requirement: Inject as mandatory instruction block

On VIP LLM generation, matched policies MUST be injected as a dedicated mandatory-instruction block (not as few-shot examples). Injection order MUST be after memory context and before few-shots. The block MUST label content as binding rules/instructions that override generic model judgment and MUST NOT present policies merely as optional style samples.

#### Scenario: Assembly order places policies after memory

- GIVEN memory context and matched policies and few-shots are available
- WHEN the VIP prompt is assembled
- THEN the policy instruction block appears after memory and before few-shots

#### Scenario: Empty match injects nothing

- GIVEN no active policies match the turn
- WHEN the VIP prompt is assembled
- THEN no policy instruction block is added

### Requirement: Soft deactivate and list for admin

The system MUST support listing active (and optionally inactive) policies for admin inspection and soft-deactivation (`is_active=0`) without hard-deleting audit history. Soft-deactivated policies MUST stop matching and injecting immediately.

#### Scenario: Soft deactivate stops injection

- GIVEN an active policy that currently matches a turn
- WHEN an admin soft-deactivates it
- THEN subsequent matches exclude it
- AND subsequent prompts omit it from the policy block

#### Scenario: List surfaces stored doctrine

- GIVEN one or more stored policies
- WHEN an admin lists policies
- THEN topic, summary, priority, and active state are visible enough to manage doctrine

### Requirement: First slice does not auto-create few-shots from distill

Distilling a guidance answer MUST create or update topic policy only. The system MUST NOT automatically insert a few-shot training example solely from the distill output in this slice.

#### Scenario: Answer distill is policy-only

- GIVEN Diana answers a guidance consult and distill succeeds
- WHEN side effects of the answer path run
- THEN a topic policy is stored or updated
- AND no automatic few-shot example is created from the distill payload alone
