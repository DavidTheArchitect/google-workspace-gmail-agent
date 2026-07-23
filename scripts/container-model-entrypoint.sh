#!/bin/sh
# Keep this entrypoint POSIX-compatible because it runs in the Debian image.
set -eu

python -m compliance_agent.container_startup
exec /usr/local/bin/container-entrypoint "$@"
