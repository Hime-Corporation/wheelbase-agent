# Cloud Gateway Runbook

One shared gateway container runs the cloud router. The container entrypoint is
the profile router, not a shared dashboard:

```bash
python -m tui_gateway.profile_router
```

The router listens on port 9320, provisions one Hermes profile and one private
child dashboard per authenticated `(tenant_id, user_id)`, and proxies backend
REST/WS traffic to that child process.

---

## Topology

```text
Internet / Wheelbase clients
          |
          v
Wheelbase backend (Go / Gin)
  public   :8000
  internal :8091
          |
          | private Docker network
          v
gateway container
  :9320 profile router
    - REST /api/* and WS /api/ws auth gate
    - validates trusted tenant and user identity
    - swaps router token for per-child token
    - eagerly starts existing wb-* profiles on boot
          |
          | loopback only
          v
per-user child dashboards
  127.0.0.1:9400-9899
  HERMES_HOME=/data/hermes/tenants/<tenant_id>/profiles/wb-<user_id>
```

Key rules:

- Port 9320 is private. It is reachable only by the backend and authorized
  internal services on the same Docker network. Never map it to a public port.
- The shared router groups profiles beneath tenant directories; tenants do not
  receive separate long-running router processes or containers.
- Each authenticated `(tenant_id, user_id)` gets an isolated profile named
  `wb-<user_id>`. Both identifiers must match `^[A-Za-z0-9_-]{1,64}$`.
- Child dashboards bind to `127.0.0.1` only. The router is the only listener
  the backend should dial.

---

## Profile Router

The router is implemented in `tui_gateway/profile_router.py`.

On first use, it provisions:

- `/data/hermes/tenants/<tenant_id>/profiles/wb-<user_id>`
- the same profile directory skeleton used by `hermes profile create`
- seed-once `config.yaml`
- seed-once Wheelbase dealership `SOUL.md`
- bundled profile skills through `hermes_cli.profiles.seed_profile_skills`

Default seeded config:

```yaml
model: minimax/minimax-m3
provider: openrouter
skin: wheelbase
plugins:
  enabled:
    - wheelbase-core
    - wheelbase-onboarding
    - wheelbase-auction-browser
    - wheelbase-demand-matrix
```

Child process command:

```bash
python -m hermes_cli.main dashboard \
  --isolated --no-open --insecure --skip-build \
  --host 127.0.0.1 --port <9400-9899>
```

Each child gets a random `HERMES_DASHBOARD_SESSION_TOKEN`. The router keeps the
shared edge token at the edge and swaps in the child token when proxying.

Supervision behavior:

- one child per valid `(tenant_id, user_id)`
- capped exponential restart backoff, 1s to 60s
- no idle stop, because per-user crons must keep firing
- boot reconcile walks `tenants/*/profiles/wb-*`, starts valid children, and
  skips invalid tenant or profile names

---

## Auth Contract

The backend broker authenticates clients, mints an immutable agent-session
scope, and injects trusted headers on the private hop to the router:

- REST `/api/{path}` requires `X-Hermes-Session-Token` equal to the router's
  `HERMES_DASHBOARD_SESSION_TOKEN`.
- WS `/api/ws` requires `?token=` equal to the router's
  `HERMES_DASHBOARD_SESSION_TOKEN`.
- REST and WS both require valid `X-Wheelbase-User-Id` and
  `X-Wheelbase-Tenant-Id` values.
- The backend forwards `X-Wheelbase-Dealership-Id`,
  `X-Wheelbase-User-Jwt`, and `X-Wheelbase-Client` (`desktop` or `mobile`).
- Desktop sessions also carry a stable `X-Wheelbase-Device-Id`. The broker
  includes `X-Wheelbase-Shell-Relay-Url` and `X-Wheelbase-Cdp-Url` only while
  that exact tenant/user/device peer is online.
- Mobile sessions carry no device ID and receive neither desktop relay URL.
- Identified JSON-RPC requests may not contain `params.profile`; the child
  profile is derived exclusively from the trusted connection identity.

Reject behavior:

- REST auth or identity failure: HTTP 403.
- WS auth or identity failure: close code 4003.
- The router refuses to start if `HERMES_DASHBOARD_SESSION_TOKEN` is missing.

Do not point `WHEELBASE_AGENT_WS_OVERRIDE` at the profile router during local
desktop development. That override lacks Wheelbase identity headers. Point it
at a plain locally-run `hermes dashboard` or directly at a child dashboard port.

---

## Per-Conversation Workspaces

Identified Wheelbase sessions run tools inside the user's sandbox with:

```text
/workspace/conversations/<stored_session_id>
```

`session.cwd.set` for identified sessions is a sandbox path operation:

- accepted paths must resolve under `/workspace`
- escapes such as `/workspace/../etc`, `/etc`, relative paths, and
  `/workspacefoo` are rejected
- host `os.path.isdir` is not used for identified sessions
- anonymous desktop/dev sessions keep the old host-path behavior

Daytona sandboxes create the cwd on connect and when a cached live environment
receives a new cwd. Docker workdirs are created through container startup.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `HERMES_HOME` | yes | Router state root, normally `/data/hermes`. Children live below `$HERMES_HOME/tenants/<tenant>/profiles`. |
| `HERMES_DASHBOARD_SESSION_TOKEN` | yes | Shared router secret. Must match the backend gateway token. |
| `PORT` | no | Router listen port. Defaults to `9320`. |
| `WHEELBASE_PROFILES_ROOT` | no | Overrides the router state root used for the tenant-nested tree. |
| `WHEELBASE_PROFILE_MODEL` | no | Seeded profile model override. Defaults to `minimax/minimax-m3`. |
| `WHEELBASE_PROFILE_PROVIDER` | no | Seeded profile provider override. Defaults to `openrouter`. |
| `WHEELBASE_PROFILE_SKIN` | no | Seeded profile skin override. Defaults to `wheelbase`. |
| `OPENROUTER_API_KEY` | yes | Central Wheelbase OpenRouter key inherited by children. |
| `SUPABASE_URL` | yes | Supabase project URL inherited by children. |
| `SUPABASE_ANON_KEY` | yes | Supabase anon key inherited by children. |
| `WHEELBASE_GO_API_ORIGIN` | yes | Public URL of the Wheelbase Go API backend. |
| `WHEELBASE_INTERNAL_API` | yes | Backend internal API, for usage reporting and CDP relay. |
| `WHEELBASE_GATEWAY_TOKEN` | yes | Token sent as `X-Gateway-Token` to backend internal endpoints. |
| `TERMINAL_ENV` | yes | Use a sandboxed backend such as `daytona` or `docker` for identified sessions. |
| `DOCKER_HOST` | docker only | Scoped Docker daemon. Never mount the host `/var/run/docker.sock`. |
| `TERMINAL_DOCKER_IMAGE` | docker only | Image for sandbox containers. |
| `WHEELBASE_WORKSPACE_VOLUME_PREFIX` | no | Docker workspace volume prefix. Defaults to `wb-ws-`. |
| `WHEELBASE_SANDBOX_KEY_PREFIX` | no | Daytona sandbox key prefix. Defaults to `wb-`. |

---

## Build

```bash
docker build -f Dockerfile.gateway -t wheelbase-gateway:dev .
```

The image installs `wheelbase_sdk` from this repo and includes the bundled
Wheelbase plugins under `plugins/wheelbase/`. The old `WITH_WB_SDK` vendor
build path is no longer used.

---

## Running a Gateway Container

```bash
docker run -d \
  --name wheelbase-gateway \
  --network wheelbase-private \
  --env-file /etc/wheelbase/gateway.env \
  -v wheelbase-hermes:/data/hermes \
  wheelbase-gateway:latest
```

The container is not published on any host port. The backend reaches it at:

```text
http://wheelbase-gateway:9320
```

---

## Smoke Tests

REST must include both the router token and Wheelbase identity:

```bash
curl -i \
  -H "X-Hermes-Session-Token: ${HERMES_DASHBOARD_SESSION_TOKEN}" \
  -H "X-Wheelbase-User-Id: user-aaaa" \
  -H "X-Wheelbase-Tenant-Id: tenant-aaaa" \
  -H "X-Wheelbase-Client: mobile" \
  http://wheelbase-gateway:9320/api/status
```

Missing or invalid user/tenant identity should return 403. That is healthy
router behavior, not a gateway outage.

WS smoke test:

```bash
wscat \
  -H "X-Wheelbase-User-Id: user-aaaa" \
  -H "X-Wheelbase-Tenant-Id: tenant-aaaa" \
  -H "X-Wheelbase-Client: mobile" \
  -c "ws://wheelbase-gateway:9320/api/ws?token=${HERMES_DASHBOARD_SESSION_TOKEN}"
```

Missing or bad token/identity should close with 4003.

---

## Volumes and State

| Path in container | Purpose | Backing |
|---|---|---|
| `/data/hermes` | Shared router state root and tenant tree | Named gateway Docker volume |
| `/data/hermes/tenants/<tenant>/profiles/wb-<user>` | Per-identity Hermes profile, `state.db`, skills, cron, home | Shared gateway volume, isolated path |
| `/workspace/conversations/<sid>` | Per-conversation sandbox cwd | User's sandbox filesystem or user Docker volume |

Do not mount the same `/data/hermes` volume into multiple gateway containers
simultaneously. SQLite state DBs do not support concurrent writers across
container processes.

---

## Security Notes

1. Never publish port 9320 publicly. The backend must mediate all external
   access.
2. Use a scoped `DOCKER_HOST` when `TERMINAL_ENV=docker`. Sharing
   the host daemon lets a compromised sandbox inspect or destroy unrelated
   containers.
3. Rotate `HERMES_DASHBOARD_SESSION_TOKEN` and `WHEELBASE_GATEWAY_TOKEN` on
   credential compromise.
4. Keep identified sessions on sandboxed terminal backends. `local` execution
   is refused unless `WHEELBASE_ALLOW_UNSANDBOXED=1` is set for dev/tests.
5. Desktop-origin shell/file/process/code and browser tools are bound to the
   originating installation. Relay loss returns `desktop_unavailable`; the
   gateway never queues, redirects, replays, or falls back to cloud/local
   execution. Mobile-origin tools use configured cloud execution from the start.
