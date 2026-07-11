# Gmail Compliance Agent

This project is a local, human-approved automation system for Google Workspace Gmail blocked
senders. Natural language is an optional planning interface; typed domain code owns validation,
authorization, execution preconditions, verification, and reporting.

The current implementation completes the public-API feasibility gate, project baseline, typed
planning boundary, deterministic domain core, structured Ollama planner, direct-command planning,
the full typed Agent Framework workflow, hash-chained audit primitives, and fail-closed
browser/session foundations. The workflow includes clarification, manual login, exact-hash
confirmation, mandatory pre-write read-back, drift-driven reconfirmation, mutation reconciliation,
one proven-safe retry, independent verification, and audit finalization.

Live page parsers and mutation locators are intentionally gated on a supervised Admin console
observation; the repository does not ship guessed write selectors.

## Safety boundary

- Every eventual mutation requires an exact, current plan/state/change-set confirmation hash.
- Only resources with both a visible managed marker and a matching local ownership record may be
  changed.
- Root OU, administrator identity, and Workspace identity must all be established.
- Mutation controls must be semantic, scoped, and unique; ambiguity aborts the operation.
- A write timeout is reconciled by read-back before any retry.
- UI persistence and Gmail propagation are reported separately.
- Browser credentials and session data never enter audit artifacts.

## Development

Python 3.12 and 3.13 are supported. Dependencies are exact-pinned and resolved by `uv`.

```powershell
uv sync --extra dev
uv run compliance-agent version
uv run compliance-agent block add --domain spammer.com --notice "Mail rejected."
uv run compliance-agent block list
uv run pytest
```

Direct commands emit the same `TaskPlan` shape used by the LLM planner and do not require Ollama.
They are plan-only until the live browser observation gate is completed.

For natural-language planning:

```powershell
uv run compliance-agent plan "Block spammer.com with notice Mail rejected."
```

Audit retention is non-destructive by default:

```powershell
uv run compliance-agent audit prune
uv run compliance-agent audit prune --apply
```

See [docs/api-feasibility.md](docs/api-feasibility.md),
[docs/architecture.md](docs/architecture.md), and [docs/live-test-procedure.md](docs/live-test-procedure.md)
before enabling any live work.
