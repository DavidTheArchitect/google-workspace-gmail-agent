#!/usr/bin/env bash
set -Eeuo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export UV_LINK_MODE=copy
export PYTHONUNBUFFERED=1
export UV_CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/uv-gmail-agent"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env with safe plan-only defaults."
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is missing. Run ./Setup-Gmail-Agent.sh first."
  exit 1
fi

if [[ ! -x .venv/bin/gmail-agent ]]; then
  echo "The project environment is missing. Repairing it now..."
  uv sync --locked --extra dev
fi

echo "Checking the project-local Node runtime..."
uv run --no-sync python scripts/install_node.py >/dev/null

echo "Checking startup requirements..."
uv run --no-sync compliance-agent doctor

echo
echo "Starting Gmail Compliance Agent..."
echo "The secure local console will open in your browser automatically."
echo "Keep this terminal open while you use the console. Press Ctrl+C to stop."
echo

exec uv run --no-sync gmail-agent
