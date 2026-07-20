# Repository organization roadmap

The repository already has a useful layered `src/` layout and broad behavior coverage. Cleanup
should reduce ambiguity without moving safety-critical code merely to make the tree look smaller.
Use `repository-inventory.md` as the factual map and this document as the change sequence.

## Organization principles

1. Keep production implementation under `src/compliance_agent/`.
2. Keep deterministic decisions in `domain/` and typed boundary data in `schemas/`.
3. Keep side effects behind application protocols and concrete adapters.
4. Keep compatibility shims small, documented, and covered by contract tests.
5. Keep generated artifacts identified by a single source of truth.
6. Do not combine file moves with behavioral changes.
7. Update documentation links, architecture tests, and inventory in the same change as a move.

## Preserve

These structures carry safety or distribution contracts and should not be casually reorganized:

- `src/compliance_agent/domain/`, `schemas/`, `application/`, `workflow/`, and their architecture
  tests.
- `docs/architecture.md`, `threat-model.md`, `live-test-procedure.md`, `selector-repair.md`, and
  `first-login.md`.
- `Dockerfile`, `compose.yaml`, and container contract tests.
- `uv.lock`, `requirements.lock`, and `reflex.lock/` while their documented consumers remain.
- `gmail_admin_agent/` while Reflex requires that import target.
- Windows and Linux setup/start wrappers while desktop distribution remains supported.

## Phase 1: low-risk consistency

- Keep model defaults aligned across `.env.example`, `compose.yaml`, settings, and README.
- Keep Windows, Linux, and Codespaces commands together in README.
- Treat `pyproject.toml` as dependency intent, `uv.lock` as the canonical resolution, and
  `requirements.lock` as a generated export.
- Label compatibility wrappers in module docstrings and this inventory.
- Run inventory-link and tracked-file checks when files are added or removed.

## Phase 2: enforce architecture and quality

- Expand architecture tests to prevent domain, schema, browser, and LLM dependency regressions.
- Keep local pre-commit checks fast, deterministic, and close to the CI format/lint/security gates.
- Keep full strict mypy, branch coverage, dependency auditing, and container smoke tests in CI.
- Replace broad exception or `Any` allowances with narrow typed boundaries when touching their
  owning code; do not perform a repository-wide mechanical rewrite.
- Record temporary mypy or coverage exclusions in `clean-code-standards.md` with a reason and
  removal condition.

## Phase 3: documentation consolidation

- Compare `design-system.md` and `reflex-design-system.md` section by section.
- Make one document canonical for shared visual tokens and accessibility rules.
- Keep a short Reflex-specific appendix only for framework mechanics and the accepted reference
  image.
- Keep operator procedures separate from architecture and design specifications.

## Phase 4: naming and compatibility decisions

- Decide whether `gmail_admin_agent` remains a permanent Reflex adapter or whether the configured
  app name can migrate to `compliance_agent` without breaking Reflex-generated state.
- Decide whether desktop wrappers remain at the root for discoverability. If moved under
  `scripts/windows/` or `scripts/linux/`, leave root forwarding wrappers for at least one release.
- Remove `scripts/export_redacted_audit.py` only after confirming no external automation uses it.

## Deferred work

- Running attended Google Admin login and live mutation inside Codespaces is not a basic repository
  organization task. It needs a separate browser, secret, session-protection, and operator-presence
  design. Codespaces currently targets development, tests, plan-only workflows, and the console.
- Large package-cycle refactors between `application` and `workflow` require dedicated behavioral
  tests and should not be bundled into file cleanup.

## Completion checks

For every cleanup change:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict src
uv run pytest
uv run pip-audit
docker compose config --quiet
```

Also run `git diff --check`, update `repository-inventory.md`, and verify that no profile, audit,
state, `.env`, or authentication material is tracked.
