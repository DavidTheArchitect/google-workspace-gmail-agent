# Gmail Compliance Agent

This project is a local, human-approved Google Workspace Gmail policy agent. It manages both
standard blocked-sender rules (domains, email addresses, approved-sender bypass lists, custom
rejection notices, and organizational units) and Reject-only Content compliance rules (simple,
advanced, metadata, and predefined expressions across all four message directions).

The current implementation completes the public-API feasibility gate, project baseline, typed
planning boundary, deterministic domain core, structured Ollama planner, direct-command planning,
the full typed Agent Framework workflow, hash-chained audit primitives, and fail-closed
browser/session foundations. The workflow includes clarification, manual login, exact-hash
confirmation, mandatory pre-write read-back, drift-driven reconfirmation, mutation reconciliation,
one proven-safe retry, independent verification, and audit finalization.

The loopback-only Reflex operator console provides the policy composer, local Gemma 4 specialist
group, application-sampled bounce-message personas, draft evidence, and a locked exact-approval
surface. Each persona begins as randomly selected application-owned facts — age, occupation,
location, traits, goals, personality, time period, current mood, D&D alignment, and delivery style —
which the local model then verbalizes into a role, voice, motif, and sender-facing notice. A
field-influence contract requires every sampled fact to affect the writing without being disclosed.
The independently sampled delivery style ranges from blunt, casual, and eccentric to lyrical,
theatrical, and professional, so professional prose is only one possible result. All nine alignments
are reachable, and a random draw excludes only the immediately previous alignment to avoid
back-to-back repeats. Every accepted notice explicitly says the sender is blocked. Exact
approval controls appear only after the headed browser supplies a fresh Google before-state read
and change-set hash; a local draft hash is never presented as permission to write. The Ownership
page can also read the current Google state on demand: the attended Playwright browser agent
inventories the live blocked-sender and Content compliance configuration without writing, and each
observed managed rule links straight into the edit, enable/disable, and remove approval flows. The
console ships with a persisted light/dark theme toggle.

Content compliance remains a Google Admin UI-only workflow. The local Gemma browser navigator sees
a screenshot and bounded accessibility snapshot, but can act only through opaque IDs for unique
semantic controls and application-supplied values. The built-in attended driver reads the current
Admin UI at run time, keeps the operator's headed Chrome window visible, and aborts on ambiguous or
unverified controls.

## Safety boundary

- Every eventual mutation requires an exact, current plan/state/change-set confirmation hash.
- Only resources with both a visible managed marker and a matching local ownership record may be
  changed.
- Root OU, administrator identity, and Workspace identity must all be established.
- Mutation controls must be semantic, scoped, and unique; ambiguity aborts the operation.
- A write timeout is reconciled by read-back before any retry.
- UI persistence and Gmail propagation are reported separately.
- Browser credentials and session data never enter audit artifacts.
- The compliance writer supports only **Reject message**. Quarantine, modification, routing, and
  private Google APIs are outside its authorization boundary.
- One approval covers the exact plan/state/change hashes; the autonomous browser run is bounded,
  rechecks the target OU before Save, and performs an independent editor read-back.

## Development

Python 3.12 and 3.13 are supported. Dependencies are exact-pinned and resolved by `uv`. Setup also
installs a checksum-verified project-local Node 22 runtime for the Reflex frontend; it does not
modify the machine-wide Node installation.

### Start the console

For the first run on Windows, double-click [`Setup-Gmail-Agent.cmd`](Setup-Gmail-Agent.cmd). It:

1. creates a safe plan-only `.env` without overwriting an existing one;
2. offers to install `uv` from the official `astral-sh.uv` WinGet package when needed;
3. creates or repairs the exact locked project environment;
4. runs a human-readable startup check; and
5. offers to launch the console immediately.

After setup, double-click [`Start-Gmail-Agent.cmd`](Start-Gmail-Agent.cmd). Keep its terminal window
open while using the agent. The launcher repairs a missing environment, checks startup readiness,
starts Reflex on the configured loopback port, and opens the local console. Google Admin login is
always performed by the operator in the visible browser; credentials are never copied into the
application. The scripts use a local, OneDrive-safe `uv` cache and copy mode. A busy configured port
fails visibly instead of silently moving an approval session to another origin.

The equivalent terminal command is:

```powershell
uv run gmail-agent
```

### Optional Docker console

Native execution remains fully supported. As an alternative, the plan-only console can run from
the automatically published GHCR image:

```powershell
docker compose pull
docker compose up
```

Use `docker compose up --build` to build and run the current local source instead. Configuration,
audit, and state survive container replacement in named volumes, and the port is published only to
host loopback. The attended Google Admin observer remains a native workflow because it needs a
visible, operator-controlled browser profile. See [docs/containers.md](docs/containers.md).

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

Direct commands emit the same schema-v2 `TaskPlan` shape used by the LLM planner and do not require
Ollama. Console proposals run through four Microsoft Agent Framework group-chat participants in
two round-robin passes by default, so every specialist can react to the group before the typed plan
is approved. The default local model is `gemma4:12b`.

For natural-language planning:

```powershell
uv run compliance-agent plan "Block spammer.com with notice Mail rejected."
```

Run modes are explicit: `CA_RUN_MODE=plan_only`, `dry_run`, or `live`. Legacy
`CA_PLAN_ONLY`/`CA_DRY_RUN` values are translated only when `CA_RUN_MODE` is absent; mixing the old
and new settings fails configuration validation. The console Settings page can select and persist
the run mode, expected administrator email, Workspace domain, group-chat model, and browser vision
model. Model selectors are populated from the installed Ollama catalog, and an exact model tag can
be downloaded locally from the same page before it is assigned to either role. Plan-only reviews
never open Google. Dry runs open the attended browser and read the current Google configuration
without writing. Live runs perform that same read, show exact before/change
hashes, require a one-time phrase and acknowledgement, re-read for drift, then apply and verify.
Changing any policy field invalidates the pending approval immediately. `.env.example` documents
the safe starting values.

Before a review, the console proves the configured Ollama model exists. Browser-backed modes also
require the selected model to advertise vision capability. The four-agent Microsoft Agent
Framework group chat accepts only strict, attributed JSON verdicts; incomplete, clarification,
unsafe, or unattributed reviews never unlock execution. Accepted turns are persisted as hash-bound
audit evidence and verified packages can be opened or exported from the Audits view.

On Windows, Reflex's generated Node/build directory is kept under
`~/.compliance_agent/reflex-web` by default so OneDrive file locking cannot strand frontend builds.
Set `REFLEX_WEB_WORKDIR` or `GMAIL_AGENT_REFLEX_WEB_DIR` to override that generated location.

Audit retention is non-destructive by default:

```powershell
uv run compliance-agent audit prune
uv run compliance-agent audit prune --apply
uv run compliance-agent audit export-redacted-zip <run-directory> <destination.zip>
```

See [docs/api-feasibility.md](docs/api-feasibility.md),
[docs/architecture.md](docs/architecture.md), [docs/operator-console.md](docs/operator-console.md),
[docs/advanced-blocking.md](docs/advanced-blocking.md),
[docs/containers.md](docs/containers.md), and
[docs/live-test-procedure.md](docs/live-test-procedure.md)
before enabling any live work.
