# Implementation status

Review date: 2026-07-11

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
- Audit core: protected per-run writer, run-consistent hash-chained events, deterministic reports,
  streaming artifact digests, terminal manifest creation and verification, structure-preserving
  redaction, and protected redacted export.
- Repository-wide hardening review: order-sensitive plan hashing, non-overlapping sensitive paths,
  strict result-evidence invariants, deterministic exhausted-retry status, failure-safe browser and
  process-lock cleanup, inert diagnostic HTML, and browser safety code included in coverage.
- Integration foundations: incremental request/plan/preflight/state/change/confirmation/mutation/
  reconciliation/verification audit artifacts and events; post-mutation audit degradation;
  verified ownership-registry updates; expected adapter failure mapping; exclusive-lock composition
  with runtime manifest metadata; and explicit plan-first audit retention.
- Operator experience: loopback-only FastAPI/HTMX console, one-time in-memory session bootstrap,
  Host/Origin/CSRF/CSP controls, guided readiness, natural-language and deterministic planning,
  typed run projections, impact assessment, expiring server-owned approvals, run control, contract
  gate inspection, ownership registry view, audit explorer, retention confirmation, and propagation
  follow-up.
- Operational hardening: explicit run modes with legacy migration, writer-free dry-run composition,
  dry-run audit manifests, reviewed contract-pack digests, inert fixture inspection, exact-evidence
  ownership recovery service, and deterministic manifested redacted ZIP export.

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
- Injection of the accepted read adapters into the console dry-run coordinator.
- Injection of the accepted live runner into the approval/execution control room.

These items are not ordinary unfinished scaffolding. Implementing them from assumptions would
violate the project's fail-closed selector policy. Run `scripts/login.py`, then
`scripts/observe_ui.py --output-directory <protected-path>` with an authorized administrator to
produce the evidence required for the next implementation gate.

## Pending after the live gate

- Browser fixture parser and locator tests based on captured sanitized evidence.
- Supervised create/read/update/read/delete/read testing.
- Admin audit-log inspection and complete live audit-package review.
- Separately authorized mail-flow propagation testing.
