# Local operator console

Start the console with:

```powershell
uv run compliance-agent console
```

It binds only to `127.0.0.1` on `CA_CONSOLE_PORT` (default `8765`). The per-launch 256-bit token is
printed locally and placed after `#` in the bootstrap URL. A small local script posts the fragment
once, clears it from browser history, and receives an in-memory `HttpOnly`, `SameSite=Strict`
session cookie. Restarting the process invalidates the session and every pending approval.

The server rejects unexpected Host values. After the one-time bootstrap exchange, it also requires
the exact loopback Origin and a CSRF token for every state-changing form. API documentation is
disabled, no CDN assets are served, and a restrictive Content Security Policy applies. It is not a
LAN service and must not be placed behind a proxy.

## Operator flow

1. Review Readiness. Missing administrator, Workspace, or UI-contract evidence blocks browser work.
2. Create a plan from natural language or the deterministic add form.
3. In dry-run mode, an accepted read adapter performs preflight, a fresh root-OU read, ownership
   resolution, desired-state calculation, deterministic diffing, and audit finalization. No writer
   exists in this composition.
4. A live preview displays exact impact and server-owned hashes. Approval expires after ten minutes
   and requires `APPLY <short-run-id>` plus an acknowledgement. Expiry discards the stale preview
   and requires a fresh read. Cancellation is authoritative even if background planning or preview
   work completes later.
5. Execution remains unavailable until a reviewed accepted contract pack and live runner are
   injected. A process restart or state drift always requires a new read and approval.

The console also exposes protected audit history, integrity state, local ownership evidence,
contract-gate status, explicit retention confirmation, and propagation follow-up. UI
reconfirmation never claims mail-flow enforcement; a separately authorized mail-flow audit is
required for that evidence level.

Ownership recovery is fail-closed. An observed managed-looking name is insufficient: the selected
audit must have intact manifest and event-chain integrity, an applied-and-verified terminal report,
an exact creation in `change_set.json`, and a matching `after.json` rule/list pair. Future mutation
still requires a fresh Admin-console read. Interrupted execution is treated as outcome-uncertain;
operators must reconcile audit evidence and current state before retrying.
