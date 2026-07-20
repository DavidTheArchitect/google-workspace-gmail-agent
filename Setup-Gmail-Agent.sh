#!/usr/bin/env bash
set -Eeuo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export UV_LINK_MODE=copy
export PYTHONUNBUFFERED=1
export UV_CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/uv-gmail-agent"

echo "Gmail Compliance Agent setup"
echo

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env with safe plan-only defaults."
else
  echo "Existing .env preserved."
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

echo
echo "Creating or repairing the locked project environment..."
uv sync --locked --extra dev

echo
echo "Installing the checksum-verified project-local Node 22 runtime..."
uv run --no-sync python scripts/install_node.py

echo
uv run --no-sync compliance-agent doctor

echo
echo "Setup complete. Run ./Start-Gmail-Agent.sh to start the console."

if [[ "${1:-}" == "--no-launch" ]]; then
  exit 0
fi

read -r -p "Start Gmail Compliance Agent now? [y/N] " response
if [[ "$response" =~ ^[Yy]$ ]]; then
  exec ./Start-Gmail-Agent.sh
fi
