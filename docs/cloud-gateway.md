# Cloud Gateway Runbook

One gateway container runs per dealership. The container entrypoint is the
profile router, not a shared dashboard:

```bash
python -m tui_gateway.profile_router
```

The router listens on port 9320, provisions one Hermes profile and one private
child dashboard per Wheelbase user, and proxies backend REST/WS traffic to the
right child process.

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
    - validates X-Wheelbase-User-Id
    - swaps router token for per-child token
    - eagerly starts existing wb-* profiles on boot
          |
          | loopback only
          v
per-user child dashboards
  127.0.0.1:9400-9899
  HERMES_HOME=/data/hermes/profiles/wb-<uid>
```

Key rules:

- Port 9320 is private. It is reachable only by the backend and authorized
  internal services on the same Docker network. Never map it to a public port.
- Each dealership gets its own gateway container and `/data/hermes` volume.
- Each authenticated Wheelbase user gets an isolated profile named
  `wb-<user_id>`, where `user_id` must match `^[A-Za-z0-9_-]{1,64}$`.
- Child dashboards bind to `127.0.0.1` only. The router is the only listener
  the backend should dial.

---

## Profile Router

The router is implemented in `tui_gateway/profile_router.py`.

On first use, it provisions:

- `/data/hermes/profiles/wb-<uid>`
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
  --no-open --insecure --skip-build \
  --host 127.0.0.1 --port <9400-9899>
```

Each child gets a random `HERMES_DASHBOARD_SESSION_TOKEN`. The router keeps the
dealership-level token at the edge and swaps in the child token when proxying.

Supervision behavior:

- one child per valid user id
- capped exponential restart backoff, 1s to 60s
- no idle stop, because per-user crons must keep firing
- boot reconcile starts existing valid `wb-*` profile dirs and skips invalid
  names

---

## Auth Contract

The backend broker contract is unchanged:

- REST `/api/{path}` requires `X-Hermes-Session-Token` equal to the router's
  `HERMES_DASHBOARD_SESSION_TOKEN`.
- WS `/api/ws` requires `?token=` equal to the router's
  `HERMES_DASHBOARD_SESSION_TOKEN`.
- REST and WS both require a valid `X-Wheelbase-User-Id`.
- The backend should also forward `X-Wheelbase-Tenant-Id`,
  `X-Wheelbase-Dealership-Id`, `X-Wheelbase-User-Jwt`, and
  `X-Wheelbase-Cdp-Url` when available.

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
| `HERMES_HOME` | yes | Router state root, normally `/data/hermes`. Profiles default to `$HERMES_HOME/profiles`. |
| `HERMES_DASHBOARD_SESSION_TOKEN` | yes | Dealership-level router secret. Must match the backend gateway token. |
| `PORT` | no | Router listen port. Defaults to `9320`. |
| `WHEELBASE_PROFILES_ROOT` | no | Overrides profile root. Defaults to `$HERMES_HOME/profiles`. |
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
  --name gateway-dealership-123 \
  --network wheelbase-private \
  --env-file /etc/wheelbase/dealership-123.env \
  -v wb-hermes-d123:/data/hermes \
  wheelbase-gateway:latest
```

The container is not published on any host port. The backend reaches it at:

```text
http://gateway-dealership-123:9320
```

---

## Smoke Tests

REST must include both the router token and Wheelbase identity:

```bash
curl -i \
  -H "X-Hermes-Session-Token: ${HERMES_DASHBOARD_SESSION_TOKEN}" \
  -H "X-Wheelbase-User-Id: user-aaaa" \
  http://gateway-dealership-123:9320/api/status
```

Missing or invalid `X-Wheelbase-User-Id` should return 403. That is healthy
router behavior, not a gateway outage.

WS smoke test:

```bash
wscat \
  -H "X-Wheelbase-User-Id: user-aaaa" \
  -c "ws://gateway-dealership-123:9320/api/ws?token=${HERMES_DASHBOARD_SESSION_TOKEN}"
```

Missing or bad token/identity should close with 4003.

---

## Volumes and State

| Path in container | Purpose | Backing |
|---|---|---|
| `/data/hermes` | Dealership state root and `profiles/wb-*` children | Named Docker volume per dealership |
| `/data/hermes/profiles/wb-<uid>` | Per-user Hermes profile, state DB, skills, cron, home | Same dealership volume |
| `/workspace/conversations/<sid>` | Per-conversation sandbox cwd | User's sandbox filesystem or user Docker volume |

Do not mount the same `/data/hermes` volume into multiple gateway containers
simultaneously. SQLite state DBs do not support concurrent writers across
container processes.

---

## Security Notes

1. Never publish port 9320 publicly. The backend must mediate all external
   access.
2. Use a scoped `DOCKER_HOST` per dealership when `TERMINAL_ENV=docker`. Sharing
   the host daemon lets a compromised sandbox inspect or destroy unrelated
   containers.
3. Rotate `HERMES_DASHBOARD_SESSION_TOKEN` and `WHEELBASE_GATEWAY_TOKEN` on
   credential compromise.
4. Keep identified sessions on sandboxed terminal backends. `local` execution
   is refused unless `WHEELBASE_ALLOW_UNSANDBOXED=1` is set for dev/tests.
