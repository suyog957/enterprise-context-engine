# ADR-004: Business policies live in OPA, not in prompts

**Status:** Accepted

## Context
Whether a requisition may become a purchase order depends on state, supplier status and risk, business-unit scope, approval limits, category restrictions and manager approval. An LLM can be persuaded, can hallucinate and cannot be audited as a rule engine.

## Decision
Express procurement and authorization rules in versioned Rego evaluated by Open Policy Agent. OPA returns typed decisions (`allowed`, `approval_required`, reason codes, explanations, policy rule references such as `SUP-004`, policy version). The same bundle evaluates every catalog action for discovery and is called again immediately before execution. If OPA cannot be reached, writes fail closed and the agent reports that no action can be confirmed.

## Consequences
- The agent learns the valid action space before planning and never discovers constraints by calling an API that fails.
- Policy changes are reviewed, versioned and tested like code (10 Rego unit tests; 75 golden decisions evaluated against live OPA with zero false allows).
- Explanations cite rules and documents, not model reasoning.
- The evaluation oracle found a real policy bug (a blocked request that also exceeded the approval limit was shown as approval-required). That kind of defect is visible and fixable in Rego in a way it would not be inside a prompt.
- Cost: an extra service and a policy language to learn; a local Python mirror exists only for the offline smoke evaluation.
