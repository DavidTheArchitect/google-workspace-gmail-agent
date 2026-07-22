# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.11.23 AS uv

FROM node:22.22.0-bookworm-slim AS node

FROM python:3.13-slim-bookworm AS builder

COPY --from=uv /uv /uvx /bin/
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    REFLEX_USE_NPM=1 \
    GMAIL_AGENT_REFLEX_WEB_DIR=/opt/gmail-agent/reflex-web

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY assets ./assets
COPY gmail_admin_agent ./gmail_admin_agent
COPY reflex.lock ./reflex.lock
COPY rxconfig.py ./rxconfig.py
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable
RUN uv run --no-sync reflex export --env prod --frontend-only --no-zip \
    && sha256sum reflex.lock/package-lock.json assets/styles.css \
       src/compliance_agent/reflex_console/app.py \
       src/compliance_agent/reflex_console/state.py rxconfig.py \
       | sha256sum | cut -d ' ' -f 1 \
       > /opt/gmail-agent/reflex-web/.seed-version

FROM python:3.13-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="Gmail Compliance Agent" \
      org.opencontainers.image.description="Local plan-only console for Google Workspace Gmail compliance changes" \
      org.opencontainers.image.source="https://github.com/DavidTheArchitect/google-workspace-gmail-agent" \
      org.opencontainers.image.licenses="MIT"

RUN groupadd --gid 10001 compliance-agent \
    && useradd --uid 10001 --gid compliance-agent --create-home --shell /usr/sbin/nologin compliance-agent \
    && install -d -o compliance-agent -g compliance-agent -m 0700 \
        /config /data/audit /data/browser-profile /data/state /var/lib/gmail-agent-reflex

COPY --from=builder --chown=compliance-agent:compliance-agent /app/.venv /app/.venv
COPY --from=builder --chown=compliance-agent:compliance-agent /app/assets /app/assets
COPY --from=builder --chown=compliance-agent:compliance-agent \
    /app/gmail_admin_agent /app/gmail_admin_agent
COPY --from=builder --chown=compliance-agent:compliance-agent /app/reflex.lock /app/reflex.lock
COPY --from=builder --chown=compliance-agent:compliance-agent /app/rxconfig.py /app/rxconfig.py
COPY --from=builder --chown=compliance-agent:compliance-agent \
    /opt/gmail-agent/reflex-web /opt/gmail-agent/reflex-web
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY --chmod=0755 scripts/container-entrypoint.sh /usr/local/bin/container-entrypoint

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    REFLEX_USE_NPM=1 \
    CA_RUN_MODE=plan_only \
    CA_CONSOLE_BIND_HOST=0.0.0.0 \
    CA_CONSOLE_OPEN_BROWSER=false \
    CA_OLLAMA_BASE_URL=http://ollama:11434/v1 \
    CA_OLLAMA_MODEL=gemma4:12b \
    CA_BROWSER_MODEL=gemma4:12b \
    NPM_CONFIG_CACHE=/var/lib/gmail-agent-reflex/.npm \
    REFLEX_DIR=/var/lib/gmail-agent-reflex/.reflex \
    GMAIL_AGENT_REFLEX_WEB_DIR=/var/lib/gmail-agent-reflex \
    CA_PROFILE_DIR=/data/browser-profile \
    CA_AUDIT_DIR=/data/audit \
    CA_STATE_DIR=/data/state

USER compliance-agent
WORKDIR /app

EXPOSE 8765

HEALTHCHECK --interval=10s --timeout=3s --start-period=120s --retries=6 \
    CMD ["python", "-c", "import os, urllib.request; port=os.environ.get('CA_CONSOLE_PORT', '8765'); urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=2).read(1)"]

ENTRYPOINT ["/usr/local/bin/container-entrypoint"]
CMD ["gmail-agent", "console"]
