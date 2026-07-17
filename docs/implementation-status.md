# Implementation status

Review date: 2026-07-16

## Complete

- Typed schema-v2 planning for standard blocked senders and Reject-only Gmail Content compliance.
- Standard create, read, atomic update, enable/disable, and remove for owned rules, primary address
  lists, approved-sender bypass lists, arbitrary organizational-unit paths, and custom notices.
- Content compliance create, read, update, enable/disable, and remove across inbound, outbound,
  internal-sending, and internal-receiving directions.
- Simple, advanced, metadata, and edition-dependent predefined expressions; one-to-ten expression
  limits; RE2 validation; all supported content locations and match operators; address-list
  conditions; and envelope sender/recipient filters.
- Dynamic local persona and rejection-notice generation with fresh entropy on every attempt,
  recent-profile duplicate suppression, explicit bounded failure, protected policy category/ID
  fields, and category-only disclosure. A deterministic sender-safety quality gate resamples drafts
  that leak markup, escape artifacts, non-printable characters, or fabricated contact details, or
  that omit the sender-visible category; the category is application-owned and not editable from
  the rejection-notice editor.
- Four-participant Microsoft Agent Framework `GroupChatBuilder` orchestration with deterministic
  round-robin selection, two passes per specialist by default, strict framework-author attribution,
  typed verdicts, complete-participant/order validation, and no model mutation authority.
- Reflex operator console with UI-selectable plan-only, dry-run, and live modes; standard and
  advanced editors; focused removal and enabled-state confirmations; managed ownership controls;
  exact approval evidence; run history; settings; and verified audit history.
- Built-in attended headed-Chrome driver for both Google Admin surfaces. It verifies the expected
  administrator and Workspace domain, uses a local vision model for bounded semantic navigation,
  supplies policy values only from typed application tokens, and refuses ambiguous controls.
- Browser-backed dry-run preview, one-time live approval, approval expiry/cancellation, mandatory
  pre-write re-read, drift rejection, exact permit binding, independent after-state verification,
  and ownership persistence only after a complete match.
- Hash-chained local audit events and terminal artifact manifests. Credentials, cookies, browser
  profiles, screenshots, approval phrases, and unrelated tenant data are excluded.
- Durable `agent-review.json` evidence bound to the plan hash, model tag, deterministic turn order,
  and typed verdicts; interrupted run folders are surfaced as indeterminate instead of hidden.
- Stale-preview invalidation after every policy edit and terminal audit finalization for cancelled,
  rejected, failed-unchanged, uncertain, drifted, verified, and no-change outcomes.
- Async draft revision guards, in-progress editor locking, clean sidebar create flow, exact focused
  impact summaries, conditional expression controls, compatible metadata operators, and responsive
  ownership/audit actions.
- Ownership-page *Current Google state* panel: attended Playwright reads of the live blocked-sender
  and Content compliance configuration in dry-run or live mode, projecting managed rules with Edit,
  Enable/Disable, and Remove entry points plus read-only unmanaged rule names, recorded as
  no-change audit packages.
- Specialist findings rendered under each agent-rail message, and a persisted light/dark theme
  toggle covering every console surface.
- OneDrive-safe Reflex generation through `REFLEX_WEB_WORKDIR`, exact dependency locks, Ruff,
  strict mypy, coverage enforcement, and production-build verification.

## Requires the operator's tenant at run time

- A licensed Google Workspace tenant whose edition exposes the requested Gmail fields.
- An administrator account with the required Gmail settings privileges.
- Attended sign-in in the visible persistent Chrome profile.
- The configured local Ollama models; the browser model must accept image input.
- A fresh dry-run or live preview against Google's current Admin UI.
- For live changes, the exact one-time phrase and acknowledgement shown after that preview.

These are operational preconditions, not missing application adapters. Google changes Admin console
markup independently, so the agent deliberately discovers the current visible controls and aborts
instead of guessing when the page is ambiguous. A supervised disposable-resource acceptance run is
still strongly recommended for each tenant and current Admin UI before production use.

## Acceptance still to perform in a real tenant

- Supervised standard rule create/read/update/disable/enable/remove/read.
- Supervised Content compliance regex/header rule create/read/update/disable/enable/remove/read.
- Admin audit-log review of those disposable changes.
- Separately authorized mail-flow propagation and bounce-message delivery tests.

No live tenant mutation was performed by the automated repository test suite.
