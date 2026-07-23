# Container deployment

The default Compose stack runs two services on one private network:

- `ollama` runs the official `ollama/ollama` image and stores models in the
  `ollama-models` named volume.
- `gmail-agent` starts only after Ollama is healthy, pulls the configured model when missing, and
  then serves the full Reflex Mission Control console through `http://ollama:11434/v1`.

Only the application console is published, on host loopback. Ollama port `11434` is not published.
No restart policy is configured, so an invalid model, failed pull, or unhealthy Ollama produces a
visible failed container and clear logs instead of an uncontrolled restart loop.

## Clean-checkout start

Install Docker Desktop, or Docker Engine with Compose v2. No host Python, Node, `uv`, Ollama, or
manual model download is required. From the repository root, run this single command:

```powershell
docker compose up
```

The first start can take several minutes while `gemma4:12b` downloads. The image already contains
the Node runtime and a prebuilt Mission Control frontend seed; its generated runtime workspace is
placed in `gmail-agent-reflex-cache`, not on the read-only image filesystem. Application startup
pulls the model when missing. Later starts report that it is already available and skip the
download. Open `http://127.0.0.1:8765` after `gmail-agent` is healthy.

For detached operation:

```powershell
docker compose up --detach
docker compose logs --follow ollama gmail-agent
```

Native execution remains available on Windows and Linux with `uv run gmail-agent`; it is useful for
the attended Google Admin workflow that requires a visible, operator-controlled browser profile.

On an NVIDIA host with Docker GPU support, add the optional override so local-model review uses
the GPU while preserving the same services, network, and named model volume:

```powershell
docker compose -f compose.yaml -f compose.gpu.yaml up --detach
```

The default command remains CPU-compatible and does not require a GPU. Confirm accelerator use with
`docker compose -f compose.yaml -f compose.gpu.yaml exec ollama ollama ps`; its `PROCESSOR` column
should report GPU use while a model request is active.

The Compose file directly names the published application image with the `latest` tag; no image
environment variable, separate pull, or local application build is required.

If the GHCR package is private, authenticate first with a token that has only `read:packages`:

```powershell
$env:CR_PAT | docker login ghcr.io -u DavidTheArchitect --password-stdin
```

## Configuration

The defaults work without a `.env` file. To override them for Compose, set shell variables before
startup or use a Compose-specific env file:

```dotenv
OLLAMA_MODEL=gemma4:12b
OLLAMA_BASE_URL=http://ollama:11434/v1
LLM_REQUEST_TIMEOUT_SECONDS=600
GROUP_CHAT_TIMEOUT_SECONDS=1800
CA_CONSOLE_PORT=8765
```

`OLLAMA_MODEL` is passed to the application as `CA_OLLAMA_MODEL` and `CA_BROWSER_MODEL`. Use an exact
Ollama model tag. `OLLAMA_BASE_URL` must end in `/v1` for the application's OpenAI-compatible client.
The startup model check derives Ollama's native endpoint from that URL. The default internal URL
should normally remain unchanged.
The longer container timeout defaults accommodate CPU-only inference; both remain bounded by the
application's existing validated limits.

For example, save overrides in `.env.compose` and run:

```powershell
docker compose --env-file .env.compose up
```

The general [`.env.example`](../.env.example) retains `CA_` values for optional native development
and documents the Compose-only overrides as comments. Do not expose or publish Ollama merely to run
the containerized application.

## Start and verify

Start the full stack in the background:

```powershell
docker compose up --detach
```

For the optional NVIDIA configuration, add `-f compose.yaml -f compose.gpu.yaml` to subsequent
Compose commands so they use the same effective configuration.

Confirm health and model installation:

```powershell
docker compose ps
docker compose exec ollama ollama list
docker compose exec ollama ollama show gemma4:12b
```

`ollama` and `gmail-agent` should be `healthy`. The page at
`http://127.0.0.1:8765` should show the dark Mission Control shell with Home, New policy, Runs,
Ownership, Audits, and Settings. To confirm the application can send a real request through the
internal network and preserve the existing `TaskPlan` API contract:

```powershell
docker compose exec gmail-agent compliance-agent plan "Block spammer.com with notice Mail rejected."
```

The command should return the same schema-v2 JSON plan as native execution. If `OLLAMA_MODEL` was
changed, replace `gemma4:12b` in direct `ollama show` commands with that exact tag.

Confirm Ollama is not published to the host. Its exposed container port should map to `null`:

```powershell
docker inspect --format '{{json .NetworkSettings.Ports}}' google-workspace-gmail-agent-ollama-1
```

## Persistence check

Restart Ollama, wait for its health check, restart the application, and inspect the model:

```powershell
docker compose restart ollama
docker compose up --detach --wait ollama
docker compose restart gmail-agent
docker compose exec ollama ollama show gemma4:12b
```

The application log should report that the model is already available. This proves it survived
the container restart in `ollama-models`. `docker compose down` also preserves that volume.

## Failure diagnosis

If Ollama does not pass its bounded health check, `gmail-agent` is not started. A model pull failure
stops the application before Mission Control starts. Inspect the exact failure without triggering
restarts:

```powershell
docker compose ps --all
docker compose logs ollama gmail-agent
```

Correct the model tag, network, proxy, disk, or memory problem, then rerun `docker compose up`; the
application safely checks existing state before pulling. For proxies, configure Docker/Ollama's
`HTTPS_PROXY`; do not set `HTTP_PROXY`, because it can interfere with container-to-container Ollama
traffic.

## Stop and reset

Stop containers while retaining all named volumes:

```powershell
docker compose down
```

Reset the entire environment, including downloaded models, configuration, audit history, browser
profile, and application state:

```powershell
docker compose down --volumes --remove-orphans
```

The reset is destructive. The next start downloads the configured model again.

## Runtime boundary

The container defaults to safe `plan_only` mode, runs the full Mission Control UI as an unprivileged
user with a read-only root filesystem, drops Linux capabilities, and enables `no-new-privileges`.
Content Compliance drafting, multi-expression validation, persona generation, specialist review,
Runs, Ownership, and Audits use the same Reflex application as native startup. The attended Google
Admin observer remains an optional native development workflow because it requires a visible,
operator-controlled browser profile; this does not make host Ollama a dependency of the Compose
stack.

## Automated publication

`.github/workflows/container.yml` builds the application image for every pull request targeting
`main`. Pushes to `main` publish `latest`. Pushing a semantic-version tag such as `v0.1.0` publishes
the user-facing image tags `0.1.0` and `0.1` to GHCR; commit-SHA image tags are intentionally not
published. Major-only tags begin at `1` once the project reaches `1.x`, avoiding an overly broad
`0` tag during initial development. Create and publish a matching GitHub Release after the tagged
container workflow passes:

```powershell
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0
gh release create v0.1.0 --verify-tag --generate-notes --title "v0.1.0"
```

The workflow uses the repository-scoped `GITHUB_TOKEN`; no registry password or personal access
token is stored in the repository.
