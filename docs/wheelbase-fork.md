# The Wheelbase Fork of hermes-agent

**Audience:** whoever has to carry this fork through the next upstream merge.
**Last verified:** 2026-09-04, after merging `upstream/main = 13e72fb205`
(`1c275d17dd`) and restoring the canvas-hint inject that merge dropped.

> The 2026-09-04 merge (`1c275d17dd`) caught the fork up to `upstream/main`.
> It is **0 commits behind**. Re-derive §3.1 with the `comm -12` command below
> *before* the next merge; the July 30 conflict map is spent.

This document describes **everything Wheelbase has added to or changed in
`NousResearch/hermes-agent`**. It is derived mechanically from
`git diff upstream/main..HEAD`, not from memory — if you re-derive it after a
merge, use the same command.

---

## 1. What the fork is

| | |
|---|---|
| `origin` | `https://github.com/Hime-Corporation/wheelbase-agent` |
| `upstream` | `https://github.com/NousResearch/hermes-agent` |
| Working branch | `wheelbase` (this is the deployed branch; there is no `main` here) |
| Merge-base with `upstream/main` | `13e72fb205` (`Merge pull request #102986`, 2026-09-04) — the last merge |
| Current `upstream/main` | `13e72fb205` (2026-09-04) |
| **Behind upstream by** | **0 commits** |
| Ahead by | **148 commits** |
| Delta size | **280 files, +37,310 / −1,613 lines** |

> Always `git fetch upstream --no-tags` before trusting a behind-count. A
> previous version of this doc reported "0 behind" against a stale cached ref;
> the 2026-09-04 number is against a freshly fetched `upstream/main`.

The vast majority of that delta is **additive** (new files upstream has never
heard of). A few thousand lines still sit inside upstream-owned files — that
small slice is where all the merge pain lives. See §3.

### How upstream merges are done here

The pattern in `git log` is consistent and should be continued:

```
git fetch upstream --no-tags
git merge upstream/main            # on branch `wheelbase`, real merge commit
# reconcile fork tests that assert on upstream internals, then:
git commit -m "Merge upstream/main (hermes <sha>) into wheelbase — N-commit catch-up"
```

Past merges: `d5c5519ef` (+361), `0d8089290` (+830), `51f1707c0` (+765),
`98fc7d668` (+888), `a0f133dcb` (+712), `2d92c6a23` (v0.19.0),
`0dec985fa` (3a2b33298), `33be7e0c6` (3312947e14), and most recently
`1c275d17dd` (13e72fb205, 4,557-commit catch-up). Merges are **not** rebased —
history is preserved so the fork delta stays computable from the merge-base.

Almost every merge has needed a follow-up "reconcile fork tests" commit
(e.g. `f1b00a6da`, `028061bd7`). Budget for that; it is normal, not a signal
that something is broken.

---

## 2. The customizations

### 2.1 Cloud gateway: the per-user profile router — **additive, load-bearing**

This is the biggest and most important thing the fork adds. Upstream has no
concept of a multi-user gateway; Wheelbase runs one container per dealership
that fans out to one isolated Hermes profile per employee.

| File | Lines | What it does |
|---|---|---|
| `tui_gateway/profile_router.py` | 752 | The container's primary process. Listens on `:9320`, keeps the upstream dashboard auth contract (WS `?token=`, REST `X-Hermes-Session-Token`), requires `X-Wheelbase-User-Id`, and spawns/keeps alive one private `hermes dashboard --isolated --skip-build` child per user on `127.0.0.1:9400-9899`. Provisions each profile (`DEFAULT_SOUL`, `PROFILE_PLUGINS`, `PROFILE_DISABLED_TOOLSETS`). |
| `tui_gateway/wheelbase_identity.py` | 115 | Parses the six `X-Wheelbase-*` headers the Go backend injects on `/api/ws` upgrade into a frozen `WheelbaseIdentity`, validates `user_id`/`tenant_id` against `^[A-Za-z0-9_-]{1,64}$` (path-traversal guard), and writes the per-user Supabase credential file the SDK reads for RLS-scoped tool calls. |
| `tui_gateway/wheelbase_inject.py` | 211 | The pre-turn seam. Binds one turn's `task_id` to one user's identity: SDK identity context, `register_task_cdp_url`, `register_task_env_overrides` (per-user `sandbox_key`, docker volumes/env), `/workspace` cwd containment. **Fail-closed** — any unexpected error raises and the turn is aborted rather than run unscoped. |
| `tui_gateway/profile_cron.py` | 127 | Daemon thread inside the router that runs `hermes cron tick` per `wb-<uid>` profile. Per-user children are dashboard processes and never tick their own cron, so without this every employee's scheduled job silently never fires. |
| `tui_gateway/tenant_migration.py` | 317 | One-shot startup migration from the flat `profiles/wb-<uid>` layout to the tenant-keyed `tenants/<tid>/profiles/wb-<uid>` layout, gated by a `tenants/.migration-completed` marker. |

**Conflict risk: LOW.** These filenames do not exist upstream. They break only
when upstream changes an internal they call into — which has happened (see the
`test_wheelbase_upstream_contracts.py` note in §2.9).

Two security fixes worth knowing about, both in `profile_router.py`:

* `f767ee762` — the router used to forward a client-supplied `?profile=` query
  param verbatim to the child, letting any authenticated caller read another
  profile's directory. Now rejected. (Covered by
  `tests/gateway/test_profile_router_profile_param.py`.)
* `PROFILE_DISABLED_TOOLSETS = ("session_search",)` — `session_search` can open
  *any* profile's `state.db` by path in-process, bypassing the sandbox, so it is
  stripped from every `wb-<uid>` profile. The admin/root profile keeps it.

### 2.2 Tenant keying — **additive + router edits, recent**

`10f48e81c` moved profile storage from `<home>/profiles/wb-<uid>` to
`<home>/tenants/<tid>/profiles/wb-<uid>`, specifically so upstream's
`parent.name == "profiles"` walk-up in `hermes_constants.py` resolves a child's
"global root" to the *per-tenant* directory — which is what gives each tenant a
shared `auth.json` fallback without cross-tenant leakage.

This depends on undocumented upstream behaviour.
`tests/gateway/test_tenant_auth_fallback.py` pins the walk-up and the shared
`auth.json` fallback so an upstream merge that changes them fails loudly
instead of silently mis-resolving credentials. **Do not delete that test file
during a merge conflict** — it is the tripwire. `test_wheelbase_upstream_contracts.py`
pins dashboard OAuth / `/api/model/set`, not this layout.

Note `profiles_root()` (flat) and `hermes_home_root()` (tenant-nested) are both
still live: `profile_cron` sweeps both layouts because a deferred migration
leaves profiles flat until the next successful boot.

### 2.3 Desktop exec relay plugin — **additive, newest subsystem**

`plugins/wheelbase-desktop-exec/` (~1,060 lines + ~1,400 lines of tests).

Registers a `tool_execution` middleware that routes **all 7 built-in tools**
(`terminal`, `process`, `read_file`, `write_file`, `patch`, `search_files`,
`execute_code`) to the user's own machine when the session identity carries
`shell_relay_url`, and falls through to the sandboxed cloud path via
`next_call` when it doesn't (mobile/offline). Explicitly designed for **zero
upstream-core edits**.

* `__init__.py` — routing + the built-in per-tool safety chain + result
  post-processing (it relays at the outer wrapper without calling `next_call`,
  so it has to re-fire what `handle_function_call` would have done).
* `relay_env.py` — `DesktopRelayEnvironment`, a `BaseEnvironment` backed by the
  relay, mirroring `DaytonaEnvironment`'s pattern so upstream's
  `execute()`/`_wait_for_process` machinery works unchanged. Contains a
  non-obvious heredoc-stdin override: the base implementation appends `<< DELIM`
  to the *end* of a command, which attaches the heredoc to the trailing `trap`
  in `ShellFileOperations._atomic_write` and silently truncates every relayed
  write. Don't "simplify" it back.
* `transport.py` / `ws_transport.py` — WS dial to the Go backend's `ExecHub`
  (`wheelbase-backend/internal/handlers/agent_exec.go`). `relay_url` is the full
  URL the backend built and injected as `X-Wheelbase-Shell-Relay-Url`; the
  capability token is already in the query string.

**Conflict risk: LOW** (own directory), but **coupling risk: MEDIUM-HIGH** — it
reaches into `tools.terminal_tool._active_environments` and
`_resolve_container_task_id`, and reproduces `tools/file_tools.py`'s result
envelope. An upstream refactor of either will break it silently; the tests under
`tests/plugins/wheelbase_desktop_exec/` are the guard.

Related: `d56e504b1` added a carve-out in `wheelbase_inject._require_sandboxed_env`
so a session **with** a working relay is exempt from the sandboxed-`TERMINAL_ENV`
requirement (its shell tools run on the user's own machine, so the gateway host
is not the isolation boundary for them). Mobile sessions with no relay still
hard-require a sandboxed backend. This is the one place the fail-closed guard is
deliberately relaxed — understand it before touching it. Design doc:
`docs/plans/2026-07-19-desktop-relay-sandbox-guard.md`.

### 2.4 Wheelbase product plugins — **additive**

`plugins/wheelbase/` — six plugin packages, ~3,400 lines of source and ~5,300
lines of tests. These are product features (dealership tools), not fork
infrastructure:

| Plugin | Surface |
|---|---|
| `wheelbase_core` | ~20 tools: work items, recon lifecycle, inventory search/stats/status, vendors, runlists, batched inspect, demand scoring |
| `wheelbase_auction_browser` | auctions, runlists, IMX picks, vote/flag |
| `wheelbase_demand_matrix` | demand-matrix setup + labelling workflow |
| `wheelbase_inspection` | render-only tools (validate + return; the Go backend persists) |
| `wheelbase_onboarding` | onboarding mode, mascot reactions |
| `wheelbase_dealercenter_import` | gated DealerCenter historic import |

Enabled per-profile via `PROFILE_PLUGINS` in `profile_router.py` **and**
separately in the seeded Telegram `config.yaml` in `scripts/gateway-entrypoint.sh`.
The six product plugins must be kept in sync by hand — there is no shared
constant. `wheelbase-desktop-exec` is dashboard-child only (it needs a
`shell_relay_url`); do not add it to the Telegram seed.

**Conflict risk: LOW** (own directory tree). Their real dependency is on
`wheelbase_sdk` and on Wheelbase's Postgres schema, not on upstream.

> **Not here:** the older *OpenClaw* (TypeScript) versions of these plugins were
> moved out to `wheelbase-app/legacy-plugins/` and are frozen reference material
> only — not built, not bundled, not loaded. Don't treat them as live.

### 2.5 `wheelbase_sdk/` — **additive**

A small pip package (`client.py`, `runtime.py`, `session.py`, `workspace.py`,
`errors.py`, ~390 lines + tests) that the six plugins import at runtime. It was
moved into this repo from `wheelbase-app/hermes-plugins/` (`d157d0de0`) and the
old `WITH_WB_SDK` vendoring mechanism was deleted (`58b242da6`) — it is now
installed unconditionally by `Dockerfile.gateway`.

**Conflict risk: NONE.**

### 2.6 Runtime edits inside upstream-owned files — **the expensive part**

| File | Δ | What Wheelbase changed | Conflict risk |
|---|---|---|---|
| `tui_gateway/server.py` | **+190/−small** | Identity threading (`_transport_identity`, `_request_profile` reject for identified callers). Most session.* RPC scoping and the three prompt-inject sites moved out during upstream's TUI split: inject now lives in `tui_gateway/prompt_turn.py` (direct) and `tui_gateway/methods_prompt.py` `_spawn_side_agent` (background + preview), with cleanup in `finally`. `methods_session.py` is the large remaining session-scoping file (+518). | **HIGH** |
| `tools/environments/daytona.py` | +183/−~30 | `always_on` (never reaped on idle), `_call_with_timeout` hard wall-clock cap on every blocking Daytona control-plane call (they otherwise have *no* timeout — this was a real production hang), `ensure_cwd()`, and a procps-free process-tree kill that walks `/proc` from `python3` because the slim Daytona image has no `pkill`. | **MEDIUM-HIGH** |
| `tools/browser_tool.py` | +85/−~15 | Per-task CDP registry (`register_task_cdp_url`, `_get_cdp_override(task_id)`) so each user's browser tool lands on *their own* desktop Chrome via the backend relay; `_resolve_cdp_override` now **fails closed** (returns `""` on discovery failure instead of the raw endpoint); query-string preservation when appending `/json/version`; local-Chromium fallback when a CDP session fails; `AGENT_BROWSER_CLI` absolute-path override for the bundled Electron build. | **MEDIUM** |
| `tools/terminal_tool.py` | +58/−~10 | `register_task_env_overrides` now **merges** instead of replacing (so injection and the ACP adapter can each own a slice); `sandbox_key` pins one stable sandbox per user instead of per-turn `task_id`; `docker_volumes`/`docker_env` per-task overrides merged with globals; `TERMINAL_DAYTONA_ALWAYS_ON`; idle reaper skips always-on envs. | **MEDIUM** |
| `hermes_cli/web_routers/cron.py` | +small | `GET /api/cron/channels` for the delivery-channel UI (moved out of `web_server.py`). | **LOW** |
| `hermes_cli/web_server_dashboard.py` | (upstream) | `mount_spa` now mounts unconditionally and 404s per-request (`check_dir=False`), so a missing `web_dist/assets` no longer crashes `--skip-build` children. `Dockerfile.gateway` still stubs `web_dist/index.html` + `web_dist/assets/.keep`. | **MEDIUM** if the stub is lost |
| `gateway/run.py` | +22 | Telegram forum "General" topic: thread id is `None` or `"1"` and both map to `message_thread_id=None` on send, so media/files landed detached from the topic. Carries the trigger message id as a reply anchor instead. | **MEDIUM** (big file, small hunk) |
| `plugins/platforms/telegram/adapter.py` | +7 | Consumes the `telegram_general_reply_fallback` metadata set above. | **LOW** |
| `agent/prompt_builder.py` | +55 | `WHEELBASE_CANVAS_PROTOCOL_HINT` — teaches the agent the `#preview/` and `#media:` markdown-href schemes the Wheelbase desktop parses to drive its canvas/artifacts rail. | **LOW** (appended constant) |
| `agent/system_prompt.py` | +small | Injects that hint from `_guidance_parts` into the **stable** prompt tier, gated on `"inventory_search" in agent.valid_tool_names`. The 2026-09-04 merge pasted this after a `return` in `_tool_guidance_block` (dead code). Pin: `tests/agent/test_system_prompt.py` (`TestWheelbaseCanvasProtocolHint`). | **LOW-MEDIUM** |
| `hermes_state_sessions.py` | +20 | `user_id` filter on `list_sessions_rich` / `session_count` (`s.user_id = ?`), so one owner's sidebar never sees another's rows. Legacy `NULL user_id` rows are deliberately excluded. Moved out of `hermes_state.py` in the September 2026 decomposition. | **LOW** |
| `tui_gateway/ws.py` | +2 | One import + `_attach_identity_to_transport(ws, transport)` on accept. Tiny but load-bearing — everything in §2.1 hangs off it. | **LOW** |
| `gateway/platforms/base.py` | ±6 | **Trailing-whitespace-only churn.** No semantic change. | **Take upstream's side on every merge.** |
| `plugins/hermes-achievements/dashboard/dist/{index.js,style.css}` | −872 | Fork deletes stale upstream build artifacts (`be0b45bac`). | Will **reappear** on merges that touch them; just re-delete. |
| `tests/cli/test_cli_background_status_indicator.py` | −1 | Incidental. | LOW |

### 2.7 Deployment: `Dockerfile.gateway` + `scripts/gateway-entrypoint.sh` — **additive**

See §4.

### 2.8 Fork documentation — **additive**

* `docs/cloud-gateway.md` (253 lines) — the operator runbook: topology, ports,
  the auth contract, env vars. **Read this first if you're operating the thing.**
* `docs/plans/2026-06-18-multi-user-isolation-and-cron.md` + the matching
  `docs/superpowers/plans/` entry — design + implementation plan for §2.1/§2.2.
* `docs/plans/2026-07-19-desktop-relay-sandbox-guard.md` — design for §2.3's
  guard carve-out.
* `docs/superpowers/plans/2026-07-02-remove-fork-dead-weight.md` — the
  already-executed removal plan (see §5).
* `docs/superpowers/plans/2026-07-06-cloud-exec-plugin.md` — the exec-plugin plan.
* `AGENTS.md` at the repo root is **upstream's**, not Wheelbase's. It describes
  upstream's contribution rubric. Don't mistake it for fork policy.

### 2.9 Tests — **additive, ~9,000 lines**

Roughly 60 new test files. The ones that matter most during a merge:

* `tests/gateway/test_tenant_auth_fallback.py` — pins `get_default_hermes_root()`
  walking `…/tenants/<tid>/profiles/wb-<uid>` up to the tenant root, plus the
  shared `auth.json` fallback and the cross-tenant non-consultation guard.
  **This is the tenant-layout merge canary.**
* `tests/gateway/test_profile_router_tenant_keying.py` — nested spawn,
  same-user-two-tenants isolation, invalid tenant header rejection.
* `tests/gateway/test_wheelbase_upstream_contracts.py` — dashboard OAuth routes
  and `/api/model/set` (the desktop Accounts tab). Not the tenant canary.
* `tests/hermes_state/test_wheelbase_v26_merge_policy.py` — non-unique titles +
  per-user list vs upstream v26 provenance/hidden/leases.
* `tests/agent/test_system_prompt.py` (`TestWheelbaseCanvasProtocolHint`) —
  canvas `#preview/` / `#media:` hint still lands in the stable tier.
* `tests/test_profile_router.py`, `tests/gateway/test_profile_router_*.py`,
  `tests/gateway/test_tenant_*.py` — router, tenant keying, `?profile=` rejection.
* `tests/test_wheelbase_inject.py`, `test_wheelbase_identity.py`,
  `test_wheelbase_multiuser.py`, `test_wheelbase_cwd_containment.py` — the
  cross-user data-bleed guards. Treat failures here as security failures.
* `tests/plugins/wheelbase_desktop_exec/*` — 11 files covering the relay.
* `tests/test_dockerfile_gateway_router.py` — asserts the image/entrypoint shape.
* Modified upstream tests: `tests/tools/test_browser_*.py`,
  `tests/test_tui_gateway_server.py`, `tests/gateway/test_session_list_allowed_sources.py`.
  These are the ones that need reconciling after most merges.

---

## 3.1 Measured remaining delta — against upstream/main @ 13e72fb205 (2026-09-04)

The 2026-09-04 merge landed at `1c275d17dd`. Merge-base **is** `upstream/main`,
so the next merge starts from a clean catch-up. Re-derive the overlap the
moment `upstream/main` moves:

```bash
git fetch upstream --no-tags
MB=$(git merge-base HEAD upstream/main)
comm -12 <(git diff --name-only $MB upstream/main | sort) \
         <(git diff --name-only $MB HEAD          | sort)
```

Remaining fork delta vs `13e72fb205`: **280 files, +37,310 / −1,613**. Most of
that is additive (`tui_gateway/profile_router.py`, `plugins/wheelbase/**`,
`plugins/wheelbase-desktop-exec/**`, `wheelbase_sdk/**`, tests, docs). The
slice still sitting inside upstream-owned files, after the merge:

| File | remaining Δ vs upstream | Notes |
|---|---:|---|
| `tui_gateway/methods_session.py` | +518 | User-scoped session list/resume/move. Largest remaining upstream-owned hunk. |
| `tui_gateway/server.py` | +190 | Identity on the session; identified callers cannot pick `profile`. |
| `tools/environments/daytona.py` | +215 | `always_on`, `_call_with_timeout`. |
| `tui_gateway/methods_prompt.py` | +94 | `_spawn_side_agent` inject (background + preview). |
| `agent/prompt_builder.py` | +61 | `WHEELBASE_CANVAS_PROTOCOL_HINT` constant. |
| `hermes_state_schema.py` | +46 | Drop unique title index on open. |
| `hermes_state_titles.py` | +44 | Non-unique title writes. |
| `tui_gateway/ws.py` | +34 | `_attach_identity_to_transport` on accept. |
| `gateway/run.py` | +22 | Telegram General-topic reply fallback. |
| `hermes_state_sessions.py` | +20 | `s.user_id = ?` list/count filter. |
| `tools/terminal_tool.py` | +20 | Env-override merge; `sandbox_key`. |
| `tui_gateway/prompt_turn.py` | +18 | Direct-path `apply_session_injection`. |
| `agent/system_prompt.py` | +small | Canvas hint in `_guidance_parts` (stable tier). |
| `tools/browser_tool.py` | +7 | Per-task CDP registry. |
| `plugins/platforms/telegram/adapter.py` | +7 | Consumes the General-topic fallback. |

`ours` / `theirs` on the *next* merge will not match this table. When `theirs`
dwarfs `ours` by orders of magnitude, upstream rewrote the file: **do not try
to merge those hunks — re-apply our intent by hand onto the new upstream
version.**

The 2026-09-04 merge taught one extra lesson: after upstream splits a module,
a mechanically pasted Wheelbase hunk can land after a `return` and still look
clean. `TestWheelbaseCanvasProtocolHint` exists so that particular silent
failure cannot recur unnoticed.

---

## 3. Merge-conflict hot spots, ranked

Cheap (own files, conflict only on deletion/rename upstream):
`tui_gateway/wheelbase_*.py`, `profile_router.py`, `profile_cron.py`,
`tenant_migration.py`, `plugins/wheelbase/**`, `plugins/wheelbase-desktop-exec/**`,
`wheelbase_sdk/**`, `Dockerfile.gateway`, `scripts/gateway-entrypoint.sh`,
`docs/**`, all new tests.

Expensive (Wheelbase edits inside upstream-owned code), worst first:

1. **`tui_gateway/methods_session.py`** — largest remaining upstream-owned hunk.
   User-scoped list/resume/workspace.move; foreign and `NULL` owner rows fail
   closed. Re-apply that intent, do not keep ours mechanically.
2. **`tui_gateway/server.py`** — identity on the session dict; identified
   callers cannot select `profile`. Injection is no longer here: it lives in
   `prompt_turn.py` (direct) and `methods_prompt.py` `_spawn_side_agent`
   (background + preview), with cleanup in `finally`.
3. **`tools/environments/daytona.py`** — `always_on` + `_call_with_timeout`
   around upstream's `BaseEnvironment` contract.
4. **`tools/browser_tool.py`** and **`tools/terminal_tool.py`** — per-task CDP
   fail-closed; `register_task_env_overrides` must **merge**.
5. **`agent/system_prompt.py`** — canvas hint in `_guidance_parts`. A
   mechanical paste after a `return` is how the 2026-09-04 merge dropped it.
6. **`gateway/run.py`** — small Telegram General-topic hunk, huge file.
7. Everything else in the §2.6 table is a small appended block.

**Silent-failure watchlist** (things that break without an error, so a merge can
"succeed" and still be wrong): the canvas hint in `_guidance_parts`, the
`Dockerfile.gateway` `web_dist/assets` stub (upstream `mount_spa` 404s
per-request but `--skip-build` still needs the stub files), the tenant-root
walk-up, `_attach_identity_to_transport` in `ws.py`, and the heredoc override
in `relay_env.py`.

### Active policy: this repo is excluded from the umbrella's tooling migrations

`wheelbase-agent` was deliberately left out of the umbrella's ESLint→oxlint and
TypeScript 7 migration (see `wheelbase/docs/2026-07-30-upgrade-report.md`,
finding 3): converting upstream-owned config files would create a conflict on
every merge for no product benefit. Keep it that way. Its TS is reportedly
TS7-clean already if that ever changes.

Its `package-lock.json` shows uncommitted churn in `git status`. That is
incidental noise, not a customization.

---

## 4. How it's deployed

This repo is deployed as the **cloud Agent Gateway on Dokploy**, one container
per dealership tenant, built from **`Dockerfile.gateway`** (not the root
`Dockerfile`, which is upstream's dev image).

Image specifics that exist for Wheelbase reasons:

* `python:3.11-slim` + `git`, `curl`, `docker.io` CLI, `gh`, Node 22, bun 1.4.0
  (pinned to the umbrella's `packageManager`), `ripgrep`/`jq`/`build-essential`
  — so the **unsandboxed Telegram process** (`TERMINAL_ENV=local`) has a usable
  shell baseline.
* `pip install -e ".[all,messaging,anthropic,daytona]"` — the Daytona SDK is
  baked in because the container has no PyPI access at runtime and the env
  lazy-imports it.
* `pip install ./wheelbase_sdk`.
* A **stub `web_dist/index.html` + `web_dist/assets/.keep`** — the dashboard
  refuses to start without them and the image ships no node/SPA toolchain.
* Exposes `:9320` (router) and `:8642` (API server). **Neither may ever be
  published publicly** — both are private-dealership-network only; the Go
  backend is the only caller.

`scripts/gateway-entrypoint.sh` (POSIX `sh` — the base image has dash, no bash)
runs **up to three processes** with an explicit failure-isolation contract:

1. `tui_gateway.profile_router` — **PRIMARY/critical.** The script `wait`s on
   its PID and exits with its exact status so Dokploy restarts the container.
2. `gateway.run` API server on `:8642` (`HERMES_HOME=/data/hermes/api-server`)
   — secondary, own retry subshell.
3. `gateway.run` Telegram (`@hermesauto_bot`, `HERMES_HOME=/data/hermes/telegram`)
   — secondary, only when `WB_TELEGRAM_BOT_TOKEN` is set.

Two things in that script are load-bearing and must not be "simplified":

* **Per-process secret scoping.** `gateway.run` auto-enables a platform whenever
  its trigger env var is present, so a *global* `TELEGRAM_BOT_TOKEN` would make
  both secondary processes poll the same bot (Telegram 409). The token is mapped
  from `WB_TELEGRAM_BOT_TOKEN` inside the Telegram subshell only, and
  `API_SERVER_*` is `env -u`'d there so it doesn't double-bind `:8642`.
* **Idempotent config seeding.** Each `config.yaml` is written only `if [ ! -f ]`,
  so hand-edits survive restarts. The corollary: **changing the seeded config in
  git does nothing to an existing container** — you have to remove the file on
  the volume (this has bitten before).

The seeded Telegram config pins `model: gpt-5.5 / provider: openai-codex` (the
owner's personal ChatGPT-plan OAuth, auth in `$TELEGRAM_HERMES_HOME/auth.json`
via `hermes auth add openai-codex`), with **no fallback provider** — quota
exhaustion errors rather than silently billing per-token. Subagent delegation
goes to `deepseek/deepseek-v4-flash`; vision is pinned via `auxiliary.vision`
because DeepSeek is text-only.

Provisioning a new tenant's gateway is scripted in the umbrella:
`scripts/dokploy/provision-tenant-gateway.sh` (clones the reference Dokploy app,
fresh `/data/hermes` volume + `HERMES_DASHBOARD_SESSION_TOKEN`, upserts the
`public.agent_gateway` row the Go backend routes by).

---

## 5. Already removed — do not resurrect

`46595a720` (plan: `docs/superpowers/plans/2026-07-02-remove-fork-dead-weight.md`)
deleted three fork subsystems confirmed dead in production. If you find
references to them in older docs, those docs are stale, not the code:

* **`WHEELBASE_APPROVAL_GATE` tool-approval machinery** (`9da8b005e`) — the flag
  was never set anywhere, so it protected nothing. `plugins/wheelbase/wheelbase_core/hooks.py`
  and the `pending_approval` dispatch branch in `hermes_cli/plugins.py` are gone.
  Upstream's `tools/approval.py` primitives are untouched and still used by
  terminal/execute_code approval.
* **`scripts/migrate_shared_store_to_profiles.py`** (`6fe0dc29b`) — one-shot,
  already run against prod.
* **`wheelbase_usage.py` / `report_session_usage`** (`57247f2db`) — the agent-side
  usage sink was broken; removed.

## 6. Flagged: unverified or possibly inert

* **`skin: wheelbase`** in the seeded Telegram `config.yaml`
  (`scripts/gateway-entrypoint.sh`). No `wheelbase` skin is defined anywhere in
  this repo — `skin` is an upstream display setting with a fixed set of values.
  Most likely silently ignored. *Unverified; harmless either way.*
* **Open items declared in `plugins/wheelbase-desktop-exec/__init__.py`'s
  docstring** — `workspace_root` is read from `identity["workspace_root"]` and
  the cloud turn is supposed to populate it per conversation (spec §7.2.1); the
  approval round-trip for destructive local exec is documented as "must be
  validated E2E before shipping". The docstring also still lists `_make_transport`
  as an open item, but `ws_transport.py` landed in `d56e504b1`, so **that bullet
  is stale** — the rest were not re-verified here.
* **Whether real employee traffic currently flows through `profile_router`**
  (versus having consolidated onto Telegram) is a production question this repo
  cannot answer. The 2026-07-01 audit flagged it as the single fact that would
  change the most conclusions about what's removable; it is still open.
* This document is **derived from the diff, not from runtime observation**.
  Everything in §2 is verified to exist in the tree; nothing here is verified to
  be *exercised in production*.

## 7. Related documents

* `docs/cloud-gateway.md` — operator runbook for the gateway container.
* `wheelbase/outputs/wheelbase-agent-fork-audit/fork-audit-2026-07-01.md` —
  earlier removability audit. **Partly superseded:** its REMOVE recommendations
  were executed (§5), it predates tenant keying and the desktop exec relay, and
  its "no branching on tenant_id" claim is no longer true.
* `wheelbase/docs/2026-07-30-upgrade-report.md` §"Findings" 3 — the
  no-tooling-migration policy for this repo.
* `wheelbase-app/legacy-plugins/README.md` — the frozen OpenClaw plugin ancestors.
