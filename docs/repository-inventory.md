# Repository inventory

This document maps the tracked repository surface to its purpose. It is the starting point for
cleanup work: update this inventory when files are added, removed, renamed, or change ownership.
Generated local directories such as `.venv/`, `.node/`, `.web/`, caches, audit data, browser
profiles, and state are intentionally excluded and must remain untracked.

## Sources of truth and generated files

| Path | Status and purpose |
| --- | --- |
| `pyproject.toml` | Primary source for Python metadata, dependencies, entry points, and quality-tool configuration. |
| `uv.lock` | Generated, committed, canonical `uv` resolution used for exact Python installs. |
| `requirements.lock` | Generated export for requirements-style consumers; derived from `uv.lock`. |
| `reflex.lock/package.json` | Tracked npm manifest for the Reflex-managed frontend dependency subtree. |
| `reflex.lock/package-lock.json` | Generated, committed npm resolution for `reflex.lock/package.json`. |
| `.env.example` | Safe configuration template. Local `.env` files are never tracked. |
| `gmail_admin_agent/` | Compatibility import target required by Reflex; implementation stays under `src/compliance_agent/`. |

## Repository root and development environment

| Path | Purpose |
| --- | --- |
| `.devcontainer/Dockerfile` | Pinned Python 3.12 and `uv` base for Codespaces/devcontainers. |
| `.devcontainer/devcontainer.json` | Codespaces features, private port forwarding, setup command, and editor defaults. |
| `.dockerignore` | Restricts the production container build context. |
| `.env.example` | Documents safe plan-only defaults and optional model/browser settings. |
| `.github/workflows/container.yml` | Builds, smoke-tests, and publishes the hardened runtime image. |
| `.github/workflows/quality.yml` | Runs formatting, linting, typing, tests, dependency audit, and secret checks. |
| `.gitignore` | Excludes environments, generated frontend files, caches, and sensitive runtime data. |
| `.markdownlint-cli2.yaml` | Markdown structure policy; line length is intentionally exempted. |
| `.pre-commit-config.yaml` | Fast local formatting, linting, and repository-hygiene hooks. |
| `.vscode/tasks.json` | One-command setup, run, quality, and container tasks for VS Code/Codespaces. |
| `compose.yaml` | Optional plan-only container service with loopback publication and persistent volumes. |
| `Dockerfile` | Multi-stage, non-root, read-only-capable production image. |
| `README.md` | Primary product, safety, setup, and run guide. |
| `Setup-Gmail-Agent.cmd` | Guided Windows x64 setup wrapper. |
| `Setup-Gmail-Agent.sh` | Guided Linux x64/arm64 and Codespaces setup wrapper. |
| `Start-Gmail-Agent.cmd` | Windows x64 repair/check/start wrapper. |
| `Start-Gmail-Agent.sh` | Linux x64/arm64 repair/check/start wrapper. |
| `assets/styles.css` | Global styling used by the Reflex console. |
| `pyproject.toml` | Python package, dependency, entry-point, and tool configuration. |
| `requirements.lock` | Derived hash-pinned dependency export. |
| `rxconfig.py` | Reflex ports, generated-workdir location, and local/Codespaces public URLs. |
| `uv.lock` | Canonical exact Python dependency resolution. |

## Documentation

| Path | Purpose |
| --- | --- |
| `docs/advanced-blocking.md` | Product behavior for standard and Content compliance blocking. |
| `docs/api-feasibility.md` | Decision record for using the Google Admin UI rather than unsupported APIs. |
| `docs/architecture.md` | Deterministic-core, imperative-shell, and fail-closed workflow architecture. |
| `docs/audit-format.md` | Audit directory, event-chain, manifest, export, and retention contracts. |
| `docs/clean-code-standards.md` | Enforceable engineering and quality rules. |
| `docs/containers.md` | Container operation and security posture. |
| `docs/design-system.md` | Server-rendered console tokens, components, accessibility, and CSS rules. |
| `docs/design/reflex-operator-console.png` | Accepted visual reference for the Reflex console. |
| `docs/first-login.md` | Attended Google sign-in procedure and stale-lock recovery. |
| `docs/implementation-status.md` | Point-in-time implementation and acceptance status. |
| `docs/live-test-procedure.md` | Supervised real-tenant read/write acceptance procedure. |
| `docs/operator-console.md` | Operator workflow for plan-only, dry-run, live, ownership, and audits. |
| `docs/reflex-design-system.md` | Reflex-specific visual brief tied to the accepted reference image. |
| `docs/repository-inventory.md` | Canonical tracked-file ownership and purpose map. |
| `docs/repository-organization.md` | Staged cleanup roadmap and preservation constraints. |
| `docs/selector-repair.md` | Fail-closed selector evidence and repair runbook. |
| `docs/threat-model.md` | Assets, trust boundaries, controls, and explicit non-goals. |

## Compatibility and operator scripts

| Path | Purpose |
| --- | --- |
| `gmail_admin_agent/__init__.py` | Package marker for the Reflex-compatible import target. |
| `gmail_admin_agent/gmail_admin_agent.py` | Re-exports the real Reflex app from `compliance_agent.reflex_console`. |
| `scripts/__init__.py` | Package marker for support scripts. |
| `scripts/export_redacted_audit.py` | Compatibility wrapper for the main redacted-audit CLI command. |
| `scripts/install_node.py` | Downloads, verifies, and safely extracts Node 22 for Windows/Linux. |
| `scripts/login.py` | Attended headed-browser sign-in helper with no credential capture. |
| `scripts/observe_ui.py` | Read-only Admin UI observation and sanitized evidence helper. |

## Application package

| Path | Purpose |
| --- | --- |
| `src/compliance_agent/__init__.py` | Package entry and public version export. |
| `src/compliance_agent/cli.py` | Main CLI for diagnostics, console, plans, and audit operations. |
| `src/compliance_agent/composition.py` | Production composition root for workflows and adapters. |
| `src/compliance_agent/exceptions.py` | Explicit expected-failure types. |
| `src/compliance_agent/launcher.py` | Cross-platform no-argument Reflex console launcher. |
| `src/compliance_agent/settings.py` | Strict environment-backed settings and live-mode validation. |
| `src/compliance_agent/startup.py` | Startup diagnostics, port selection, Ollama probe, and public-origin derivation. |
| `src/compliance_agent/version.py` | Single application version constant. |

### Application services

| Path | Purpose |
| --- | --- |
| `src/compliance_agent/application/__init__.py` | Application-service package marker. |
| `src/compliance_agent/application/approval_service.py` | Short-lived exact-evidence approval envelopes. |
| `src/compliance_agent/application/attended_audit.py` | Audit assembly for attended policy runs. |
| `src/compliance_agent/application/attended_policy_service.py` | End-to-end attended preview, approval, execution, and verification. |
| `src/compliance_agent/application/audit_catalog.py` | Read-only audit history projections. |
| `src/compliance_agent/application/audit_inspection_service.py` | Detailed finalized-audit inspection. |
| `src/compliance_agent/application/audit_service.py` | Deterministic terminal audit finalization. |
| `src/compliance_agent/application/change_presentation.py` | Operator-facing change summaries. |
| `src/compliance_agent/application/change_service.py` | Desired-state and diff orchestration. |
| `src/compliance_agent/application/compliance_audit_service.py` | Protected audit boundary for Content compliance runs. |
| `src/compliance_agent/application/compliance_browser_service.py` | Approved Content compliance browser-action boundary. |
| `src/compliance_agent/application/compliance_ownership_service.py` | Content compliance ownership lifecycle. |
| `src/compliance_agent/application/compliance_preview_service.py` | Advanced-blocker preview and approval evidence. |
| `src/compliance_agent/application/dry_run_audit_service.py` | Read-only preview audit finalization. |
| `src/compliance_agent/application/dry_run_service.py` | Browser-backed read and impact orchestration without mutation. |
| `src/compliance_agent/application/failure_mapping.py` | Maps boundary failures into typed application results. |
| `src/compliance_agent/application/fixture_inspection_service.py` | Validates sanitized UI contract evidence. |
| `src/compliance_agent/application/impact_service.py` | Deterministic impact assessment. |
| `src/compliance_agent/application/mutation_service.py` | Typed mutation protocol boundary. |
| `src/compliance_agent/application/ownership_console_service.py` | Console projections for ownership health/recovery. |
| `src/compliance_agent/application/ownership_health_service.py` | Reconciles local ownership with observed state. |
| `src/compliance_agent/application/ownership_recovery_service.py` | Recovers ownership evidence from verified audits. |
| `src/compliance_agent/application/ownership_service.py` | Verified ownership-registry updates. |
| `src/compliance_agent/application/planning_service.py` | Natural-language and direct-command typed plans. |
| `src/compliance_agent/application/preflight_service.py` | Joins browser observations to deterministic preflight policy. |
| `src/compliance_agent/application/propagation_service.py` | Tracks later enforcement separately from UI persistence. |
| `src/compliance_agent/application/reporting_service.py` | Builds authoritative terminal reports. |
| `src/compliance_agent/application/retention_service.py` | Plans and applies fail-closed audit retention. |
| `src/compliance_agent/application/state_read_service.py` | Typed blocked-sender state-read boundary. |
| `src/compliance_agent/application/ui_contract_service.py` | Stores and activates evidence-gated UI contracts. |
| `src/compliance_agent/application/verification_service.py` | Fresh-read post-change verification. |
| `src/compliance_agent/application/workflow_audit_service.py` | Incremental protected workflow audit recorder. |

### Audit

| Path | Purpose |
| --- | --- |
| `src/compliance_agent/audit/__init__.py` | Audit package marker. |
| `src/compliance_agent/audit/export.py` | Redacted deterministic audit exports and ZIPs. |
| `src/compliance_agent/audit/html.py` | Neutralizes active HTML in diagnostic evidence. |
| `src/compliance_agent/audit/manifest.py` | Artifact digests and manifest integrity verification. |
| `src/compliance_agent/audit/redaction.py` | Conservative text redaction. |
| `src/compliance_agent/audit/report.py` | Deterministic JSON and Markdown reports. |
| `src/compliance_agent/audit/writer.py` | Protected hash-chained run-directory writer. |

### Browser integration

| Path | Purpose |
| --- | --- |
| `src/compliance_agent/browser/__init__.py` | Browser package marker. |
| `src/compliance_agent/browser/accessible_names.py` | Canonical accessible-name patterns. |
| `src/compliance_agent/browser/admin_agent_driver.py` | Attended Admin reader/writer coordinator. |
| `src/compliance_agent/browser/diagnostics.py` | Sanitizes explicit read-only UI evidence. |
| `src/compliance_agent/browser/locator_contracts.py` | Unique, scoped, fail-closed semantic locators. |
| `src/compliance_agent/browser/navigation_agent.py` | Constrained model navigator over opaque semantic candidates. |
| `src/compliance_agent/browser/pages/__init__.py` | Page-object package marker. |
| `src/compliance_agent/browser/pages/address_lists.py` | Address-list page gate pending accepted live evidence. |
| `src/compliance_agent/browser/pages/content_compliance.py` | Safe Content compliance page automation. |
| `src/compliance_agent/browser/pages/gmail_spam_settings.py` | Gmail spam-settings identity and state gate. |
| `src/compliance_agent/browser/pages/login.py` | Authentication-state detection without credential interaction. |
| `src/compliance_agent/browser/routes.py` | Allowed Google Accounts/Admin host constants. |
| `src/compliance_agent/browser/session.py` | Protected persistent headed-Chrome lifecycle. |
| `src/compliance_agent/browser/states.py` | Closed browser page-state enum. |
| `src/compliance_agent/browser/ui_extraction.py` | Schema-constrained visible-state extraction. |

### Server-rendered console

| Path | Purpose |
| --- | --- |
| `src/compliance_agent/console/__init__.py` | FastAPI console package entry. |
| `src/compliance_agent/console/app.py` | Console composition, middleware, routes, and error handlers. |
| `src/compliance_agent/console/capabilities.py` | Fail-closed optional capability discovery. |
| `src/compliance_agent/console/configuration.py` | Allow-listed local `.env` updates. |
| `src/compliance_agent/console/coordinator.py` | Planning, preview, approval, and execution coordinator. |
| `src/compliance_agent/console/journal.py` | Non-authoritative local run projection store. |
| `src/compliance_agent/console/notices.py` | Allow-listed redirect notices. |
| `src/compliance_agent/console/planner.py` | Structured planner adapter. |
| `src/compliance_agent/console/readiness.py` | Safe local readiness projections. |
| `src/compliance_agent/console/recovery.py` | Narrow planning-failure recovery hints. |
| `src/compliance_agent/console/routes.py` | Setup, run, audit, ownership, export, and SSE routes. |
| `src/compliance_agent/console/run_status.py` | Fixed actionable run-status messages. |
| `src/compliance_agent/console/security.py` | Single-operator session, host, Origin, and CSRF protection. |
| `src/compliance_agent/console/setup_flow.py` | Guided setup-step projection. |
| `src/compliance_agent/console/timeline.py` | Pure run-timeline presentation. |

#### Console static assets

| Path | Purpose |
| --- | --- |
| `src/compliance_agent/console/static/bootstrap.js` | Exchanges the URL-fragment launch token without retaining it. |
| `src/compliance_agent/console/static/console.js` | HTMX/SSE, busy-state, copy, hash, and approval enhancements. |
| `src/compliance_agent/console/static/favicon.svg` | Console favicon. |
| `src/compliance_agent/console/static/htmx.min.js` | Vendored HTMX runtime. |
| `src/compliance_agent/console/static/relative-time.js` | Enhances absolute timestamps with relative labels. |
| `src/compliance_agent/console/static/sse.js` | Vendored HTMX SSE extension. |
| `src/compliance_agent/console/static/styles.css` | Tokenized responsive server-rendered console styles. |
| `src/compliance_agent/console/static/tables.js` | Search, sort, filter, and pagination enhancement. |
| `src/compliance_agent/console/static/theme.js` | Early light/dark/auto theme application. |
| `src/compliance_agent/console/static/toasts.js` | Accessible transient notification manager. |

#### Console templates

| Path | Purpose |
| --- | --- |
| `src/compliance_agent/console/templates/activity.html` | Session and finalized-evidence activity page. |
| `src/compliance_agent/console/templates/audit_detail.html` | Audit integrity and event detail page. |
| `src/compliance_agent/console/templates/audits.html` | Finalized-audit index. |
| `src/compliance_agent/console/templates/base.html` | Shared shell, navigation, themes, and assets. |
| `src/compliance_agent/console/templates/bootstrap.html` | One-time console connection page. |
| `src/compliance_agent/console/templates/contracts.html` | Browser contract/evidence status page. |
| `src/compliance_agent/console/templates/dashboard.html` | Readiness and primary-task dashboard. |
| `src/compliance_agent/console/templates/error.html` | Styled security-preserving error page. |
| `src/compliance_agent/console/templates/new_run.html` | Mode-aware plan/preview/apply form. |
| `src/compliance_agent/console/templates/ownership.html` | Ownership health, recovery, and Google-state reads. |
| `src/compliance_agent/console/templates/propagation.html` | Later enforcement evidence tracking. |
| `src/compliance_agent/console/templates/readiness.html` | Local diagnostic details. |
| `src/compliance_agent/console/templates/run_detail.html` | Plan, preview, approval, progress, and result detail. |
| `src/compliance_agent/console/templates/setup.html` | Settings and ordered capability setup. |
| `src/compliance_agent/console/templates/partials/_audit_rows.html` | Reusable audit table rows. |
| `src/compliance_agent/console/templates/partials/_change_summary.html` | Human-readable change-summary macros. |
| `src/compliance_agent/console/templates/partials/_direct_add_form.html` | Deterministic direct-add form. |
| `src/compliance_agent/console/templates/partials/_google_identities_form.html` | Expected Google identity settings form. |
| `src/compliance_agent/console/templates/partials/_icons.html` | Shared icon sprite. |
| `src/compliance_agent/console/templates/partials/_interrupted_guidance.html` | Interrupted-run guidance. |
| `src/compliance_agent/console/templates/partials/_macros.html` | Shared timestamp and empty-state macros. |
| `src/compliance_agent/console/templates/partials/_plan_summary.html` | Plan-summary macros. |
| `src/compliance_agent/console/templates/partials/_run_status.html` | Progress, recovery, approval, and terminal state. |
| `src/compliance_agent/console/templates/partials/_session_runs.html` | Session-run table/polling fragment. |

### Domain core

| Path | Purpose |
| --- | --- |
| `src/compliance_agent/domain/__init__.py` | Deterministic domain package marker. |
| `src/compliance_agent/domain/compliance_desired_state.py` | Content compliance desired-state calculation. |
| `src/compliance_agent/domain/desired_state.py` | Standard blocked-sender desired-state calculation. |
| `src/compliance_agent/domain/diff.py` | Current-versus-desired state comparison. |
| `src/compliance_agent/domain/hashing.py` | Canonical approval/drift hashing. |
| `src/compliance_agent/domain/normalization.py` | Conservative domain/email/address normalization. |
| `src/compliance_agent/domain/ownership.py` | Dual-evidence managed-resource ownership policy. |
| `src/compliance_agent/domain/preconditions.py` | Root-OU, hash, and drift preconditions. |
| `src/compliance_agent/domain/preflight.py` | Pure administrator/session/OU preflight policy. |
| `src/compliance_agent/domain/reconciliation.py` | Uncertain-write read-back and retry decisions. |
| `src/compliance_agent/domain/regex_validation.py` | Google RE2-compatible expression validation. |
| `src/compliance_agent/domain/reporting.py` | Deterministic terminal-status selection. |
| `src/compliance_agent/domain/verification.py` | Independent expected-versus-observed verification. |

### Infrastructure

| Path | Purpose |
| --- | --- |
| `src/compliance_agent/infrastructure/__init__.py` | Concrete adapter package marker. |
| `src/compliance_agent/infrastructure/clock.py` | Injectable UTC clock. |
| `src/compliance_agent/infrastructure/filesystem.py` | Atomic ownership-registry persistence. |
| `src/compliance_agent/infrastructure/identifiers.py` | Injectable UUID generation. |
| `src/compliance_agent/infrastructure/permissions.py` | Sensitive path permission tightening. |
| `src/compliance_agent/infrastructure/process_lock.py` | Cross-platform exclusive run lock. |
| `src/compliance_agent/infrastructure/protected_json.py` | Atomic protected JSON persistence. |
| `src/compliance_agent/infrastructure/runtime_metadata.py` | Version, repository, and contract metadata. |

### Local-model adapters

| Path | Purpose |
| --- | --- |
| `src/compliance_agent/llm/__init__.py` | Local-model adapter package marker. |
| `src/compliance_agent/llm/examples.py` | Structured-planner few-shot examples. |
| `src/compliance_agent/llm/group_chat.py` | Local Microsoft Agent Framework specialist review. |
| `src/compliance_agent/llm/persona.py` | Persona generation and sender-safety gates. |
| `src/compliance_agent/llm/planner.py` | Planner/reviewer/persona factories. |
| `src/compliance_agent/llm/policy_draft.py` | Typed review-only policy composer. |
| `src/compliance_agent/llm/prompts.py` | Versioned typed-planning system prompt. |
| `src/compliance_agent/llm/readiness.py` | Local Ollama model readiness checks. |
| `src/compliance_agent/llm/structured.py` | Schema-constrained calls and bounded correction. |

### Reflex console

| Path | Purpose |
| --- | --- |
| `src/compliance_agent/reflex_console/__init__.py` | Reflex console package marker. |
| `src/compliance_agent/reflex_console/app.py` | Reflex UI entry bound to server-owned state. |
| `src/compliance_agent/reflex_console/state.py` | Policy composition, review, approval, audit, and settings state machine. |

### Schemas

| Path | Purpose |
| --- | --- |
| `src/compliance_agent/schemas/__init__.py` | Typed-boundary package marker. |
| `src/compliance_agent/schemas/base.py` | Shared immutable Pydantic configuration and scalar constraints. |
| `src/compliance_agent/schemas/changes.py` | Desired-state, change-set, and diff models. |
| `src/compliance_agent/schemas/compliance.py` | Content compliance resource/state models. |
| `src/compliance_agent/schemas/compliance_operations.py` | Advanced preview, impact, and approval models. |
| `src/compliance_agent/schemas/events.py` | Hash-chained audit events. |
| `src/compliance_agent/schemas/hitl.py` | Human confirmation, login pause, and clarification payloads. |
| `src/compliance_agent/schemas/operations.py` | Run mode, phase, ownership, propagation, and contract models. |
| `src/compliance_agent/schemas/plan.py` | Typed LLM-to-application action boundary. |
| `src/compliance_agent/schemas/policy_draft.py` | Review-only draft recommendation boundary. |
| `src/compliance_agent/schemas/preflight.py` | Browser/admin preflight observations. |
| `src/compliance_agent/schemas/resources.py` | Standard blocked-sender resource models. |
| `src/compliance_agent/schemas/results.py` | Mutation, reconciliation, verification, and run results. |
| `src/compliance_agent/schemas/state.py` | Normalized blocked-sender state. |
| `src/compliance_agent/schemas/status.py` | Closed terminal status set. |

### Workflow

| Path | Purpose |
| --- | --- |
| `src/compliance_agent/workflow/__init__.py` | Fixed workflow package marker. |
| `src/compliance_agent/workflow/build.py` | Typed workflow graph construction. |
| `src/compliance_agent/workflow/contracts.py` | Workflow adapter/auditor/ownership protocols. |
| `src/compliance_agent/workflow/executors.py` | Thin executors for fixed workflow phases. |
| `src/compliance_agent/workflow/messages.py` | Typed graph-edge and interruption payloads. |

## Tests

| Path | Purpose |
| --- | --- |
| `tests/__init__.py` | Test package marker. |
| `tests/conftest.py` | Shared domain/state/ownership builders. |
| `tests/architecture/__init__.py` | Architecture-test package marker. |
| `tests/architecture/test_dependency_boundaries.py` | AST-enforced package dependency constraints. |
| `tests/browser/__init__.py` | Browser-test package marker. |
| `tests/browser/test_diagnostics.py` | Diagnostic redaction/sanitization tests. |
| `tests/browser/test_locator_contracts.py` | Fail-closed semantic locator tests. |
| `tests/browser/test_pages.py` | Page identity and live-UI gate tests. |
| `tests/browser/test_session.py` | Browser session lifecycle and cleanup tests. |
| `tests/unit/__init__.py` | Unit-test package marker. |
| `tests/unit/test_agent_browser_execution.py` | Specialist attribution and browser-action boundary tests. |
| `tests/unit/test_application_and_workflow.py` | Application services and typed graph tests. |
| `tests/unit/test_attended_policy_service.py` | Full attended policy lifecycle tests. |
| `tests/unit/test_audit.py` | Audit chain, manifest, export, and integrity tests. |
| `tests/unit/test_compliance_v2.py` | Content compliance schema/workflow invariants. |
| `tests/unit/test_console_enhancements.py` | Console mode, recovery, approval, and impact tests. |
| `tests/unit/test_console_plan_improvements.py` | Console persistence and plan-flow regressions. |
| `tests/unit/test_console_recovery.py` | Planning recovery-hint tests. |
| `tests/unit/test_console_run_status.py` | Operator run-status message tests. |
| `tests/unit/test_console_ui.py` | Console UI, security, SSE, audit, and retention tests. |
| `tests/unit/test_container_contract.py` | Static hardened-container contracts. |
| `tests/unit/test_diff_hash_preconditions.py` | Diff, hash, and stale-confirmation tests. |
| `tests/unit/test_google_state_read.py` | Attended current-Google-state read tests. |
| `tests/unit/test_launch_scripts.py` | Windows/Linux setup, Node, and launcher contracts. |
| `tests/unit/test_normalization.py` | Address/domain normalization tests. |
| `tests/unit/test_observe_ui_script.py` | Read-only observation script tests. |
| `tests/unit/test_ownership_and_desired_state.py` | Ownership and desired-state tests. |
| `tests/unit/test_persona_randomness.py` | Persona entropy and output-guard tests. |
| `tests/unit/test_plan_schema.py` | Typed task-plan security-boundary tests. |
| `tests/unit/test_policy_draft_composer.py` | Typed local policy-composition tests. |
| `tests/unit/test_preflight_policy.py` | Deterministic preflight policy tests. |
| `tests/unit/test_reflex_persona_state.py` | Reflex persona-state concurrency/honesty tests. |
| `tests/unit/test_remaining_integration.py` | Cross-service integration coverage. |
| `tests/unit/test_settings_cli_infrastructure.py` | Settings, CLI, startup, lock, and adapter tests. |
| `tests/unit/test_structured_planner.py` | Structured output and corrective-retry tests. |
| `tests/unit/test_verification_reconciliation_reporting.py` | Verification, reconciliation, retry, and reporting tests. |
| `tests/workflow/__init__.py` | Workflow-test package marker. |
| `tests/workflow/test_full_workflow.py` | Full fixed-graph tests with controlled fakes. |
| `tests/workflow/test_preflight_schema.py` | Ready/blocked preflight schema invariants. |
