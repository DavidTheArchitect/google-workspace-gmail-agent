# Engineering standards

- Domain decisions are pure, typed, deterministic, and infrastructure-independent.
- External data is validated by Pydantic at the boundary; finite states use enums or literals.
- Page objects express UI state only; executors coordinate one service at a time.
- Expected failures have explicit exception or result types and preserve their causes.
- Time, identifiers, filesystem access, model access, and browser access are injected.
- Names distinguish requested, current, desired, expected, observed, and persisted state.
- No broad mutation selector, positional selector, mutable global, unexplained `Any`, or bare
  exception handler is permitted.
- Ruff formatting/linting, strict mypy, branch-aware pytest coverage, architecture checks, and
  secret checks are mandatory.
- Complexity limits prompt review; they are not targets to game with meaningless wrappers.
