# Audit format

Each run uses `CA_AUDIT_DIR/runs/<UTC timestamp>-<run-id>/`. The directory can contain the request,
plan and schema, confirmation, before/desired/expected-after/after states, verification,
deterministic report, sanitized diagnostics, and trace.

`run.jsonl` is append-only for the life of a run. Every canonical event includes a sequence,
previous event hash, and SHA-256 hash over the event without `event_hash`. `manifest.json` records
component versions, final status, and SHA-256 hashes for finalized artifacts.

Audit content must never include cookies, authorization headers, session tokens, password or 2SV
fields, copied profile files, or unrelated organization data. Redacted export applies a second
allowlist-oriented sanitization pass; it does not expose the protected source directory.

Retention is plan-first. `compliance-agent audit prune` lists convention-named run directories older
than `CA_AUDIT_RETENTION_DAYS` without deleting them. `compliance-agent audit prune --apply` deletes
only the exact candidates after revalidating their age, name, type, symlink status, and parent root.
