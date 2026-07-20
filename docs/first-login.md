# First login

1. Set absolute, distinct `CA_PROFILE_DIR`, `CA_STATE_DIR`, and `CA_AUDIT_DIR` paths.
2. Set `CA_EXPECTED_ADMIN_EMAIL` and `CA_EXPECTED_WORKSPACE_DOMAIN`.
3. Leave `CA_RUN_MODE=plan_only` during initial setup.
4. Run `uv run python scripts/login.py` from an attended workstation.
5. Complete Google sign-in and 2-Step Verification directly in headed Chrome.
6. Close the browser through the script. Do not copy the profile or its cookies.

The application never reads password or second-factor controls. A normal daily-use Chrome profile
must not be supplied. Before live reads, follow `live-test-procedure.md` and validate current page
identity evidence.

The local console may be started with `uv run gmail-agent`. The server-rendered fallback may be
started with `uv run compliance-agent console`; its one-time token is
placed in the URL fragment, exchanged for an in-memory loopback session, and removed from the URL.
The console never asks for Google credentials and never embeds the Admin console.

## Stale-lock recovery

If a prior process crashed, inspect `CA_STATE_DIR/run.lock`. Confirm that its process ID is no
longer running on the recorded host and that no Chrome process is using the dedicated profile.
Archive the record for incident evidence, then remove only that exact lock file. Never automate
lock deletion based on age alone.
