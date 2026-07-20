FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.23 /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1
