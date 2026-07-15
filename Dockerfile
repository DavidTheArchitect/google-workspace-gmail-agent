# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.11.23 AS uv

FROM python:3.13-slim-bookworm AS builder

COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.13-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="Gmail Compliance Agent" \
      org.opencontainers.image.description="Local plan-only console for Google Workspace Gmail compliance changes" \
      org.opencontainers.image.source="https://github.com/DavidTheArchitect/google-workspace-gmail-agent" \
      org.opencontainers.image.licenses="MIT"

RUN groupadd --gid 10001 compliance-agent \
    && useradd --uid 10001 --gid compliance-agent --create-home --shell /usr/sbin/nologin compliance-agent \
    && install -d -o compliance-agent -g compliance-agent -m 0700 \
        /config /data/audit /data/browser-profile /data/state

COPY --from=builder --chown=compliance-agent:compliance-agent /app/.venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CA_RUN_MODE=plan_only \
    CA_CONSOLE_BIND_HOST=0.0.0.0 \
    CA_CONSOLE_OPEN_BROWSER=false \
    CA_PROFILE_DIR=/data/browser-profile \
    CA_AUDIT_DIR=/data/audit \
    CA_STATE_DIR=/data/state

USER compliance-agent
WORKDIR /config

EXPOSE 8765

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import os, urllib.request; port=os.environ.get('CA_CONSOLE_PORT', '8765'); urllib.request.urlopen(f'http://127.0.0.1:{port}/bootstrap', timeout=2).read(1)"]

ENTRYPOINT ["compliance-agent"]
CMD ["console", "--no-open"]
