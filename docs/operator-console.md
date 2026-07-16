# Local operator console

Start the console with:

```powershell
uv run gmail-agent
```

On Windows, double-click `Setup-Gmail-Agent.cmd` once, then use `Start-Gmail-Agent.cmd`. Setup
creates the safe `.env`, installs or repairs the locked environment, and runs `compliance-agent
doctor`. The regular launcher repeats the non-mutating checks, waits until the server is ready,
then opens and exchanges the one-time bootstrap link automatically. You should not need to copy or
enter a token. Keep the launcher window open; press `Ctrl+C` there to stop the console.

If the configured port is already occupied, a normal launch tries the next 20 loopback ports and
prints the selected address. An explicitly supplied `--port` remains exact and fails with a clear
message when occupied.

`uv run compliance-agent console` remains available for advanced use, including `--port` and
`--no-open`.

Native execution binds only to `127.0.0.1` on `CA_CONSOLE_PORT` (default `8765`). The optional
container binds on its internal interface, but Compose publishes that port only to host
`127.0.0.1`; the browser-facing security boundary is unchanged. The per-launch 256-bit token is
printed locally and placed after `#` in the bootstrap URL. A small local script posts the fragment
once, clears it from browser history, and receives an in-memory `HttpOnly`, `SameSite=Strict`
session cookie. Restarting the process invalidates the session and every pending approval.

If the one-time link is used, expires, or is opened without its fragment, type `link` (or press
Enter) in the terminal that owns the running console. The console rotates the launch token and
prints a new sign-in URL; the previous URL becomes invalid immediately, while an existing signed-in
session remains active. When standard input is unavailable (for example, some containers or
detached Windows launchers), restart the console to obtain a fresh link. This recovery path adds no
HTTP control endpoint.

The server rejects unexpected Host values. After the one-time bootstrap exchange, it also requires
the exact loopback Origin and a CSRF token for every state-changing form. API documentation is
disabled, no CDN assets are served, and a restrictive Content Security Policy applies. It is not a
LAN service and must not be placed behind a proxy.

## Operator flow

1. Start on Home, which shows the available workflow and the explicit Google Admin capability
   limit. In the default plan-only mode, no Google Admin configuration is required.
2. Choose **Block a sender** and use the built-in form. It works without Ollama or Google access.
   Natural-language planning with Ollama is an optional secondary path.
3. If local AI cannot create a draft, the recovery page explains that account settings are not the
   cause and prefills one safely recovered sender in the built-in form.
4. Review the finished plan. A plan-only run ends here and never presents preview, approval, or
   execution as unfinished work.
5. Open **Settings** to see separate capability states for planning, the expected Google account,
   and Google Admin integration. Expected identities are optional for planning. The authenticated
   form validates and writes only those two non-secret values to the local `.env`; it does not sign
   in to Google or unlock a writer.
6. Browser preview also requires supervised UI evidence and an installed accepted read adapter.
   The current web composition does not install that adapter. Once one is reviewed and injected, a
   dry run performs preflight, a fresh root-OU read, ownership resolution, desired-state
   calculation, deterministic diffing, and audit finalization.
7. A live preview displays exact impact and server-owned hashes. Approval expires after ten minutes
   and requires `APPLY <short-run-id>` plus an acknowledgement. Expiry discards the stale preview
   and requires a fresh read. Cancellation is authoritative even if background planning or preview
   work completes later.
8. Execution remains unavailable until a reviewed accepted contract pack and live runner are
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
