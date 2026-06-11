# Cloud Gateway Runbook

One gateway container per dealership. Each container runs the Hermes dashboard
server, hosts the TUI gateway for connected users, and reports usage to the
Wheelbase backend.

---

## Topology

```
                  Internet / Wheelbase backend
                        │
                  ┌─────▼──────────────────────────────┐
                  │  backend (Go / Gin)                │
                  │  public  :8000   ← user-facing API │
                  │  internal :8091  ← private network │
                  │    POST /internal/agent/usage       │
                  │    GET  /internal/agent/cdp/{uid}   │
                  └────────────────┬───────────────────┘
                                   │ private Docker network
                  ┌────────────────▼───────────────────┐
                  │  gateway (this container)          │
                  │  :9320  dashboard + TUI gateway    │
                  │  NEVER published to the internet   │
                  │  HERMES_HOME = /data/hermes volume │
                  │  DOCKER_HOST → scoped daemon       │
                  └────────────────────────────────────┘
```

Key rules:

- Port 9320 is **private**. It is reachable only by the backend and authorised
  internal services on the same Docker network. Never map it to a public port.
- Each dealership gets its **own gateway container** with its own `HERMES_HOME`
  volume and its own `DOCKER_HOST` pointing at a scoped, rootless-DinD or
  dedicated Docker daemon. The host daemon socket (`/var/run/docker.sock`) must
  **never** be mounted — this would allow sandbox containers from one dealership
  to interfere with others (spec §10).
- The backend's internal API (`WHEELBASE_INTERNAL_API`) is on the private
  network and is not reachable from the public internet.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `HERMES_HOME` | yes | Path inside container for all agent state (`/data/hermes`; backed by a named volume) |
| `HERMES_DASHBOARD_SESSION_TOKEN` | yes | Shared secret between gateway and backend. Used for HTTP API auth (`X-Hermes-Session-Token`) and as the backing value for the Wheelbase DashboardAuthProvider. Must match `HERMES_GATEWAY_TOKEN` on the backend side. |
| `OPENROUTER_API_KEY` | yes | Central Wheelbase OpenRouter key (spec §9); delivered to agent at session injection |
| `SUPABASE_URL` | yes | Supabase project URL |
| `SUPABASE_ANON_KEY` | yes | Supabase anon key |
| `WHEELBASE_GO_API_ORIGIN` | yes | Public URL of the Wheelbase Go API backend |
| `WHEELBASE_INTERNAL_API` | yes | Base URL of the backend internal API (e.g. `http://backend:8091`); used by the usage reporter and CDP relay |
| `WHEELBASE_GATEWAY_TOKEN` | yes | Same value as `HERMES_DASHBOARD_SESSION_TOKEN`; sent as `X-Gateway-Token` on usage sink POSTs |
| `TERMINAL_ENV` | yes | Set to `docker` to enable per-user sandbox containers (spec §7) |
| `DOCKER_HOST` | yes | Unix or TCP socket for the scoped Docker daemon — **never** the host `docker.sock` |
| `TERMINAL_DOCKER_IMAGE` | yes | Image used for per-user sandbox containers |
| `WHEELBASE_WORKSPACE_VOLUME_PREFIX` | no | Volume name prefix for workspace volumes (default `wb-ws-`) |

---

## WS Auth Mode — How Non-Loopback Token Auth Works

The `hermes_cli/web_server.py` `start_server()` function calls
`should_require_auth(host, allow_public)` to decide the auth mode:

```python
def should_require_auth(host: str, allow_public: bool) -> bool:
    return (host not in _LOOPBACK_HOST_VALUES) and (not allow_public)
```

`_LOOPBACK_HOST_VALUES = {"localhost", "127.0.0.1", "::1"}`.

There are three WS auth modes (`_ws_auth_mode` in `web_server.py`):

| Bind | `--insecure` | Mode | `?token=` WS auth |
|------|-------------|------|-------------------|
| loopback | n/a | `loopback` | accepted (constant-time) |
| non-loopback | yes | `insecure` | accepted (constant-time) |
| non-loopback | no | `gated` | **unconditionally rejected** (requires `?ticket=`/`?internal=`) |

The Wheelbase backend chat broker authenticates its gateway dial with
`ws://gateway:9320/api/ws?token=<HERMES_DASHBOARD_SESSION_TOKEN>` — the
legacy token credential. Gated mode rejects that credential outright (and
its `?ticket=` flow is a browser-SPA mint the broker cannot perform), so
**the gateway container MUST run with `--insecure`**:

```
python -m hermes_cli.main dashboard \
    --no-open --insecure --host 0.0.0.0 --port 9320
# WITH HERMES_DASHBOARD_SESSION_TOKEN set (high-entropy, shared with backend)
```

What `--insecure` actually means here: it skips the OAuth browser-login
gate. It does NOT disable token auth — the WS upgrade still constant-time
compares `?token=` against `HERMES_DASHBOARD_SESSION_TOKEN`, and HTTP API
endpoints still require the `X-Hermes-Session-Token` header (or
`Authorization: Bearer …`). This combination is acceptable only under the
deployment invariants below:

- the gateway port is **never published publicly** — it is reachable only on
  the private Dokploy network, with the Go backend as the single public door
  (spec §10);
- `HERMES_DASHBOARD_SESSION_TOKEN` is a strong random secret (32+ bytes),
  matching `HERMES_GATEWAY_TOKEN` on the backend.

Do not "harden" this by removing `--insecure`: that flips the container into
gated mode and silently breaks the backend broker (every chat connect returns
502 because the gateway rejects the WS upgrade).

---

## Build Instructions

```bash
# Basic build (no wheelbase_sdk plugin):
docker build -f Dockerfile.gateway -t wheelbase-gateway:dev .

# Build with wheelbase_sdk plugin from sibling repo:
cp -r ../wheelbase-app/hermes-plugins/wheelbase_sdk vendor/wheelbase_sdk
docker build \
    -f Dockerfile.gateway \
    --build-arg WITH_WB_SDK=1 \
    --build-arg HERMES_GIT_SHA="$(git rev-parse HEAD)" \
    -t wheelbase-gateway:dev .
```

The umbrella build script (`scripts/build-gateway.sh`, to be created) should
handle the vendor copy and SHA injection automatically for CI.

---

## Running a Gateway Container

```bash
docker run -d \
  --name gateway-dealership-123 \
  --network wheelbase-private \
  --env-file /etc/wheelbase/dealership-123.env \
  -v wb-hermes-d123:/data/hermes \
  wheelbase-gateway:latest
```

The container is **not** published on any host port. The backend reaches it at
`http://gateway-dealership-123:9320` on the shared private Docker network.

---

## Smoke Test

Check the HTTP status endpoint (requires `HERMES_DASHBOARD_SESSION_TOKEN`):

```bash
curl -sf \
  -H "X-Hermes-Session-Token: ${HERMES_DASHBOARD_SESSION_TOKEN}" \
  http://gateway-dealership-123:9320/api/status
```

Expected response: JSON with `{"status": "ok", ...}` (or similar health fields).

If the gateway is unreachable from the backend host but reachable from inside
the Docker network, exec into the backend container:

```bash
docker exec -it backend \
  curl -sf \
    -H "X-Hermes-Session-Token: ${HERMES_DASHBOARD_SESSION_TOKEN}" \
    http://gateway-dealership-123:9320/api/status
```

---

## Volumes and State

| Path in container | Purpose | Recommended backing |
|---|---|---|
| `/data/hermes` | All Hermes state: sessions, messages, skills, config | Named Docker volume per dealership (`wb-hermes-<id>`) |

Do not mount the same volume into multiple gateway containers simultaneously —
the SQLite state DB (`state.db`) does not support concurrent writers across
container processes.

---

## Security Notes

1. **Never publish port 9320 publicly.** It exposes an unauthenticated admin
   surface for the agent's conversation history and tool execution. The backend
   reverse proxy must mediate all external access.
2. **Use a scoped `DOCKER_HOST`** (rootless DinD or a dedicated daemon socket)
   per dealership. Sharing the host daemon allows a compromised sandbox to
   inspect or destroy containers belonging to other dealerships.
3. **Rotate `HERMES_DASHBOARD_SESSION_TOKEN`** on credential compromise. The
   token is used both for usage-sink auth (`X-Gateway-Token`) and dashboard
   HTTP auth (`X-Hermes-Session-Token`).
