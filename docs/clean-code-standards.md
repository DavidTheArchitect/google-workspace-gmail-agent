# Engineering standards

These rules apply to production code, support scripts, tests, and developer configuration. They
favor explicit behavior and enforceable boundaries over arbitrary limits on function or file size.

## Design and dependencies

- Domain decisions are pure, typed, deterministic, and infrastructure-independent.
- Dependencies point toward policy. Browser, model, console, and persistence details do not enter
  the deterministic core.
- External data is validated at the boundary; finite states use enums or literals.
- Side effects enter through small protocols or injected callables. Time, identifiers, filesystem,
  model, and browser access are injected where behavior depends on them.
- Page objects express UI identity and state. Workflow executors coordinate one phase at a time.
- Prefer a direct implementation over a speculative abstraction. Extract shared code only when it
  represents one stable concept or removes meaningful duplication.
- Names distinguish requested, current, desired, expected, observed, and persisted state.

## Types and state

- Strict mypy is the baseline for `src/compliance_agent`.
- Boundary models are immutable unless mutation is required by the owning framework.
- `Any`, unchecked casts, and untyped dictionaries require a narrow interoperability boundary and
  a test that validates the runtime shape. They must not spread into domain decisions.
- Mutable process globals are prohibited. Server-owned state has an explicit lifecycle and owner.

## Control flow and failures

- Expected failures use explicit exception or result types and preserve their causes.
- A broad exception handler is allowed only at a process, request, or optional-capability boundary.
  It must fail closed, retain diagnostic context, and have behavior-focused coverage.
- Bare exception handlers, silent error suppression, broad mutation selectors, and positional UI
  selectors are prohibited.
- Guard clauses should make invalid and unsafe paths obvious. Complexity limits trigger design
  review; they are not targets to game with meaningless wrappers.

## Security and portability

- Local consoles bind to exact loopback. Codespaces accepts only the exact derived private HTTPS
  origin; arbitrary proxy hosts remain rejected.
- Authentication material, browser profiles, local state, `.env`, and audit packages are never
  tracked.
- Downloads used by setup are version-pinned, checksum-verified, and extracted without trusting
  archive paths.
- Supported setup and launch behavior remains equivalent on Windows x64, Linux x64/arm64, and
  Codespaces. Platform-specific wrappers delegate to the same Python entry points.

## Tests and quality gates

- Tests assert observable behavior, safety invariants, and failure paths rather than implementation
  trivia. Fakes are small and protocol-shaped.
- Architecture tests enforce dependency direction independently from code review.
- Ruff formatting/linting, strict mypy, branch-aware pytest coverage, Markdown structure checks,
  dependency auditing, container smoke tests, and secret checks are mandatory in CI.
- Pre-commit runs fast deterministic checks. The full test, typing, dependency, and container gates
  remain CI-authoritative and are available through the `Quality: All` VS Code task.
- Security-critical decision logic remains inside the coverage gate. Live browser mechanics may be
  excluded only when deterministic contracts and adapter behavior are covered separately.

## Tracked exceptions

- `compliance_agent.reflex_console.*` is temporarily excluded from strict mypy because Reflex
  generates dynamic state descriptors. New framework-independent logic must live in typed modules
  outside that override; the override must not expand.
- The coverage omissions in `pyproject.toml` are limited to attended browser mechanics, the Reflex
  framework surface, and the no-argument launcher. Their policy, validation, and adapter contracts
  remain covered in deterministic tests.
- Markdown line length (`MD013`) is disabled because tables, commands, and audit examples are more
  readable unwrapped. All other default Markdown rules are enforced.

An exception is not precedent. Any new or expanded exclusion must include its reason, owner,
removal condition, and a compensating test or validation gate.
