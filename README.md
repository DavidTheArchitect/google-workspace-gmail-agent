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
group, dynamic bounce-message personas, draft evidence, and a locked exact-approval surface. Exact
approval controls appear only after the headed browser supplies a fresh Google before-state read
and change-set hash; a local draft hash is never presented as permission to write.

Content compliance remains a Google Admin UI-only workflow. The local Gemma browser navigator sees
a screenshot and bounded accessibility snapshot, but can act only through opaque IDs for unique
semantic controls and application-supplied values. Live writes remain gated on accepted supervised
UI evidence for the administrator's current Admin console version.

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
Ollama. Natural-language requests run through four Microsoft Agent Framework group-chat
participants before the final schema-constrained plan. The default local model is `gemma4:12b`.

For natural-language planning:

```powershell
uv run compliance-agent plan "Block spammer.com with notice Mail rejected."
```

Run modes are explicit: `CA_RUN_MODE=plan_only`, `dry_run`, or `live`. Legacy
`CA_PLAN_ONLY`/`CA_DRY_RUN` values are translated only when `CA_RUN_MODE` is absent; mixing the old
and new settings fails configuration validation. Browser-backed dry runs require supervised
live-read contract evidence, and live composition requires an accepted contract-pack digest. The
console Settings page can select and persist the run mode and can validate and save the expected
administrator email and Workspace domain. Mode changes apply to new runs; a completed plan-only run
can continue into a ready browser-backed mode without being drafted again. Other settings remain in
`.env`, with `.env.example` documenting the safe starting values. The current web composition creates
and reviews plans but does not install the accepted read adapter or live writer required for Google
Admin preview or apply, so selecting those modes exposes their exact setup blockers instead of
offering an action that cannot run.

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
