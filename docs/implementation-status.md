# Implementation status

Review date: 2026-07-10

## Complete

- Phase 0: public-API feasibility decision and threat model.
- Phase 1: exact dependency pins and locks, settings validation, Ruff, strict mypy, branch coverage,
  architecture checks, pre-commit, CI, secret scanning, and dependency audit.
- Phase 2: resource/action schemas, normalization, ownership, desired state, deterministic diff,
  canonical hashes, confirmation preconditions, reconciliation, verification, and status policy.
- Phase 3: schema-constrained Ollama planning, compatibility extraction, corrective retries,
  few-shot prompts, planner metadata, and deterministic direct-command plans.
- Phase 7 deterministic orchestration: typed Agent Framework graph, clarification and login HITL,
  mandatory confirmation, fresh pre-write read, drift reconfirmation, mutation routing, uncertain
  result reconciliation, one safe retry, verification, and audit finalization.
- Audit core: protected per-run writer, hash-chained events, deterministic reports, artifact
  digests, manifest verification, redaction, and redacted export.

## Implemented but awaiting live validation

- Dedicated headed persistent Chrome session.
- Exclusive process lock and documented stale-lock recovery.
- Explicit page states and safe semantic locator contracts.
- Deterministic administrator, Workspace, privilege, Gmail-context, blocked-senders-context, and
  root-OU preflight policy.
- Attended login and sanitized observation scripts.

## Gated on supervised Admin console evidence

- Live administrator/Workspace/privilege/OU observer locators.
- Blocked-sender rule and address-list parsers.
- Current rule-to-list relationship extraction.
- Address-list and rule mutation locators.
- Save-response observation and known-entry-point navigation details.
- Sanitized HTML/ARIA fixtures for the current Admin console.
- Supervised disposable-resource CRUD acceptance.

These items are not ordinary unfinished scaffolding. Implementing them from assumptions would
violate the project's fail-closed selector policy. Run `scripts/login.py`, then
`scripts/observe_ui.py --output-directory <protected-path>` with an authorized administrator to
produce the evidence required for the next implementation gate.

## Pending after the live gate

- Browser fixture parser and locator tests based on captured sanitized evidence.
- Supervised create/read/update/read/delete/read testing.
- Admin audit-log inspection and complete live audit-package review.
- Separately authorized mail-flow propagation testing.
