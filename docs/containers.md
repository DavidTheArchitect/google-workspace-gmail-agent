# Optional Docker deployment

The native Windows and `uv` workflows remain the default. The container provides the same
plan-only operator console without installing Python on the host. It intentionally does not
containerize the attended Google Admin observation workflow, which requires a visible browser and
a protected, operator-controlled profile.

## Run the published image

Install Docker Desktop, then run:

```powershell
docker compose pull
docker compose up
```

Read the secure fallback link from the terminal and open it in the same machine's browser. The
console is published only on `127.0.0.1:8765`; do not replace the host-side address with `0.0.0.0`
or place the console behind a proxy.

Stop the console without deleting its named volumes:

```powershell
docker compose down
```

The configuration, audit, browser-profile, and state directories persist in named volumes. The
container filesystem is read-only, runs as an unprivileged user, drops Linux capabilities, and
enables `no-new-privileges`.

If the GHCR package is private, authenticate before pulling:

```powershell
$env:CR_PAT | docker login ghcr.io -u DavidTheArchitect --password-stdin
```

Use a token with `read:packages` only. Alternatively, change the package visibility to public on
GitHub after its first publication.

## Build from local source

To run the current working tree instead of the published image:

```powershell
docker compose up --build
```

Native execution remains unchanged:

```powershell
uv run gmail-agent
```

## Ollama

Compose maps `host.docker.internal` to the Docker host and configures the agent to use
`http://host.docker.internal:11434/v1`. The guided deterministic form works even when Ollama is not
running. Override `GMAIL_AGENT_OLLAMA_BASE_URL` or `GMAIL_AGENT_OLLAMA_MODEL` before
`docker compose up` when needed. The Docker-specific names prevent values in the native `.env`
file—especially a `localhost` Ollama address—from being applied inside the container by mistake.

## Automated publication

`.github/workflows/container.yml` builds the image for every pull request targeting `main`. It
loads and health-checks that pull-request image without publishing it. Every push to `main`,
including a merged pull request, builds and publishes these GHCR tags:

- `ghcr.io/davidthearchitect/google-workspace-gmail-agent:latest`
- `ghcr.io/davidthearchitect/google-workspace-gmail-agent:sha-<full-commit-sha>`

The workflow authenticates with the repository-scoped `GITHUB_TOKEN`; no registry password or
personal access token is stored in the repository.
