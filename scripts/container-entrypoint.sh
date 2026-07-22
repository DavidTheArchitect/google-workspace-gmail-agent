#!/bin/sh
# Keep this entrypoint POSIX-compatible because it runs in the Debian image.
set -eu

seed_root=/opt/gmail-agent/reflex-web
cache_root=${GMAIL_AGENT_REFLEX_WEB_DIR:-/var/lib/gmail-agent-reflex}
seed_version=$(cat "$seed_root/.seed-version")
versioned_cache="$cache_root/$seed_version"
runtime_app="$versioned_cache/app"
runtime_web="$versioned_cache/web"

if [ ! -f "$versioned_cache/.seed-complete" ]; then
    echo "[gmail-agent] Preparing the Mission Control frontend cache."
    seed_web=
    for candidate in "$seed_root"/*; do
        if [ -d "$candidate" ]; then
            seed_web=$candidate
            break
        fi
    done
    if [ -z "$seed_web" ]; then
        echo "[gmail-agent] The prebuilt Mission Control frontend is missing." >&2
        exit 1
    fi
    mkdir -p "$runtime_app" "$runtime_web"
    cp -a "$seed_web/." "$runtime_web/"
    cp -a /app/assets "$runtime_app/assets"
    cp -a /app/gmail_admin_agent "$runtime_app/gmail_admin_agent"
    cp -a /app/reflex.lock "$runtime_app/reflex.lock"
    cp -a /app/rxconfig.py "$runtime_app/rxconfig.py"
    touch "$versioned_cache/.seed-complete"
fi

export GMAIL_AGENT_REFLEX_WEB_DIR="$versioned_cache"
export REFLEX_WEB_WORKDIR="$runtime_web"
cd "$runtime_app"
echo "[gmail-agent] Starting the Mission Control console."
exec "$@"
