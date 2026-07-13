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

The loopback-only operator console adds guided readiness, natural-language and deterministic
planning, run control, evidence-gate status, ownership inspection, audit history, integrity review,
retention confirmation, and propagation follow-up without exposing a remote control plane.

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

### Start the console

For the first run on Windows, double-click [`Setup-Gmail-Agent.cmd`](Setup-Gmail-Agent.cmd). It:

1. creates a safe plan-only `.env` without overwriting an existing one;
2. offers to install `uv` from the official `astral-sh.uv` WinGet package when needed;
3. creates or repairs the exact locked project environment;
4. runs a human-readable startup check; and
5. offers to launch the console immediately.

After setup, double-click [`Start-Gmail-Agent.cmd`](Start-Gmail-Agent.cmd). Keep its terminal window
open while using the agent. The launcher repairs a missing environment, checks startup readiness,
waits for the local server, opens the secure one-time URL, and signs the browser in automatically—
there is no token to copy or type. It uses a local, OneDrive-safe `uv` cache and copy mode. If the
configured port is busy, the console selects the next free loopback port automatically.

The equivalent terminal command is:

```powershell
uv run gmail-agent
```

If dependencies have not been installed yet, `uv` resolves them on the first launch. Press
`Ctrl+C` in the launcher window to stop the console.

```powershell
uv sync --extra dev
uv run compliance-agent doctor
uv run compliance-agent version
uv run compliance-agent block add --domain spammer.com --notice "Mail rejected."
uv run compliance-agent block list
uv run gmail-agent
uv run pytest
```

Direct commands emit the same `TaskPlan` shape used by the LLM planner and do not require Ollama.
They are plan-only until the live browser observation gate is completed.

For natural-language planning:

```powershell
uv run compliance-agent plan "Block spammer.com with notice Mail rejected."
```

Run modes are explicit: `CA_RUN_MODE=plan_only`, `dry_run`, or `live`. Legacy
`CA_PLAN_ONLY`/`CA_DRY_RUN` values are translated only when `CA_RUN_MODE` is absent; mixing the old
and new settings fails configuration validation. Browser-backed dry runs require supervised
live-read contract evidence, and live composition requires an accepted contract-pack digest.
Edit `.env` to change local configuration; `.env.example` documents the safe starting values.

Audit retention is non-destructive by default:

```powershell
uv run compliance-agent audit prune
uv run compliance-agent audit prune --apply
uv run compliance-agent audit export-redacted-zip <run-directory> <destination.zip>
```

See [docs/api-feasibility.md](docs/api-feasibility.md),
[docs/architecture.md](docs/architecture.md), [docs/operator-console.md](docs/operator-console.md),
and [docs/live-test-procedure.md](docs/live-test-procedure.md)
before enabling any live work.
