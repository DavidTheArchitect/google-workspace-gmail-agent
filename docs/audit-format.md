# Audit format

Each run uses `CA_AUDIT_DIR/runs/<UTC timestamp>-<run-id>/`. The directory can contain the request,
plan and schema, confirmation, before/desired/expected-after/after states, verification,
deterministic report, sanitized diagnostics, and trace.

`run.jsonl` is append-only for the life of a run. Every canonical event includes a sequence,
previous event hash, and SHA-256 hash over the event without `event_hash`. `manifest.json` records
component versions, final status, and SHA-256 hashes for finalized artifacts.
Attended Reflex runs write `preview.json`, `before.json`, `expected_after.json`, and
`change_set.json`; live execution also writes `execution_result.json` when a typed result exists.
Dry-run packages use the terminal status `dry_run_preview_ready` when a mutation preview exists.
The one-time approval phrase is deliberately excluded.
Accepted Reflex group reviews write `agent-review.json` with the exact plan hash, local model tag,
participant roster, bounded turn indexes, typed verdicts, and sanitized findings. Approval phrases,
credentials, screenshots, and raw Google page content are excluded. A convention-named run folder
left without a terminal manifest after interruption remains visible as indeterminate.

Audit content must never include cookies, authorization headers, session tokens, password or 2SV
fields, copied profile files, or unrelated organization data. Redacted export applies a second
allowlist-oriented sanitization pass; it does not expose the protected source directory.

Retention is plan-first. `compliance-agent audit prune` lists convention-named run directories older
than `CA_AUDIT_RETENTION_DAYS` without deleting them. `compliance-agent audit prune --apply` deletes
only the exact candidates after revalidating their age, name, type, symlink status, and parent root.

`audit export-redacted-zip` creates a deterministic ZIP with fixed entry timestamps and an
`export-manifest.json` containing the hashes and sizes of the redacted artifacts. The export
manifest describes the shareable package; it does not replace or modify the protected source
manifest.
