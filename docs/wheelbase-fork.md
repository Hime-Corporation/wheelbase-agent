# The Wheelbase Fork of hermes-agent

**Audience:** whoever has to carry this fork through the next upstream merge.
**Last verified:** 2026-07-30, against `HEAD = 82f5eff5f`, with `upstream` freshly
fetched: `upstream/main = 3a2b33298` (2026-07-30).

> ⚠️ **The fork is 3,076 commits behind upstream.** See §3.1 for the conflict map —
> that section is the one to read before attempting a merge.

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
| Merge-base with `upstream/main` | `3ef6bbd20` (`chore: release v0.19.0`, 2026-07-20) — the last merge |
| Current `upstream/main` | `3a2b33298` (2026-07-30) |
| **Behind upstream by** | **3,076 commits** (4,748 files changed upstream since the merge-base) |
| Ahead by | **99 commits** |
| Delta size | **194 files, +22,503 / −1,153 lines** |

> A previous version of this doc said "0 behind." That was measured against a
> **stale cached ref** — `upstream/main` had not been fetched since 2026-07-20, so
> it was comparing against the commit the last merge already brought in. Always
> `git fetch upstream --no-tags` before trusting a behind-count.

The vast majority of that delta is **additive** (new files upstream has never
heard of). Only ~1,400 lines sit inside upstream-owned files — but that small
slice is where all the merge pain lives. See §3.

### How upstream merges are done here

The pattern in `git log` is consistent and should be continued:

```
git fetch upstream --no-tags
git merge upstream/main            # on branch `wheelbase`, real merge commit
# reconcile fork tests that assert on upstream internals, then:
git commit -m "Merge upstream/main (hermes <sha>) into wheelbase — N-commit catch-up"
```

Past merges: `d5c5519ef` (+361), `0d8089290` (+830), `51f1707c0` (+765),
`98fc7d668` (+888), `a0f133dcb` (+712), and most recently `2d92c6a23`
(v0.19.0). Merges are **not** rebased — history is preserved so the fork delta
stays computable from the merge-base.

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
`tests/gateway/test_wheelbase_upstream_contracts.py` (174 lines) exists purely
to pin those behaviours so an upstream merge that changes them fails loudly
instead of silently mis-resolving credentials. **Do not delete that test file
during a merge conflict** — read it, it is the tripwire.

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
Both lists must be kept in sync by hand — there is no shared constant.

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
| `tui_gateway/server.py` | **+755/−246** | ~30 hunks across a 15k-line file that upstream churns constantly. Identity threading (`_transport_identity`, `_wheelbase_explicit_cwd`), `wheelbase_identity` carried on the session dict, per-profile store resolution for every `session.*` RPC, the three `apply_session_injection` call sites (`_run_prompt_submit`, background, preview) with their cleanup handlers, credential-file refresh on JWT update, and CDP-resolution changes. | **HIGH** |
| `tools/environments/daytona.py` | +183/−~30 | `always_on` (never reaped on idle), `_call_with_timeout` hard wall-clock cap on every blocking Daytona control-plane call (they otherwise have *no* timeout — this was a real production hang), `ensure_cwd()`, and a procps-free process-tree kill that walks `/proc` from `python3` because the slim Daytona image has no `pkill`. | **MEDIUM-HIGH** |
| `tools/browser_tool.py` | +85/−~15 | Per-task CDP registry (`register_task_cdp_url`, `_get_cdp_override(task_id)`) so each user's browser tool lands on *their own* desktop Chrome via the backend relay; `_resolve_cdp_override` now **fails closed** (returns `""` on discovery failure instead of the raw endpoint); query-string preservation when appending `/json/version`; local-Chromium fallback when a CDP session fails; `AGENT_BROWSER_CLI` absolute-path override for the bundled Electron build. | **MEDIUM** |
| `tools/terminal_tool.py` | +58/−~10 | `register_task_env_overrides` now **merges** instead of replacing (so injection and the ACP adapter can each own a slice); `sandbox_key` pins one stable sandbox per user instead of per-turn `task_id`; `docker_volumes`/`docker_env` per-task overrides merged with globals; `TERMINAL_DAYTONA_ALWAYS_ON`; idle reaper skips always-on envs. | **MEDIUM** |
| `hermes_cli/web_server.py` | +75/−~5 | (a) `GET /api/cron/channels` for the delivery-channel UI. (b) `mount_spa` runs frontend-less when `web_dist/assets` is missing, not just when `web_dist` is — without this the `--skip-build` per-profile child crashes at startup and per-profile routing silently falls back to the shared store. | **MEDIUM** |
| `gateway/run.py` | +22 | Telegram forum "General" topic: thread id is `None` or `"1"` and both map to `message_thread_id=None` on send, so media/files landed detached from the topic. Carries the trigger message id as a reply anchor instead. | **MEDIUM** (big file, small hunk) |
| `plugins/platforms/telegram/adapter.py` | +7 | Consumes the `telegram_general_reply_fallback` metadata set above. | **LOW** |
| `agent/prompt_builder.py` | +55 | `WHEELBASE_CANVAS_PROTOCOL_HINT` — teaches the agent the `#preview/` and `#media:` markdown-href schemes the Wheelbase desktop parses to drive its canvas/artifacts rail. | **LOW** (appended constant) |
| `agent/system_prompt.py` | +9 | Injects that hint into the **stable** prompt tier, gated on `"inventory_search" in agent.valid_tool_names` so it stays O(1) and byte-stable (prompt-cache safe — upstream's cardinal rule). | **LOW-MEDIUM** |
| `hermes_state.py` | +18 | `user_id` filter on `list_sessions_rich` and the matching session count, so one owner's sidebar never sees another's rows. Legacy `NULL user_id` rows are deliberately excluded. | **LOW** |
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

* `tests/gateway/test_wheelbase_upstream_contracts.py` — pins undocumented
  upstream behaviours the tenant layout depends on. **This is your merge canary.**
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

## 3.1 Measured conflict map — against upstream/main @ 3a2b33298 (2026-07-30)

Derived mechanically. `MB=3ef6bbd20`:
```bash
git fetch upstream --no-tags
comm -12 <(git diff --name-only $MB upstream/main | sort) \
         <(git diff --name-only $MB HEAD          | sort)
```

**Only 19 of our 194 changed files are also touched upstream.** The other 175 are
purely additive and will merge without a murmur. The whole cost is in these 19 —
and it is very unevenly distributed.

`ours` / `theirs` = lines changed on each side since the merge-base. When `theirs`
dwarfs `ours` by orders of magnitude, upstream effectively rewrote the file: **do
not try to merge those hunks — re-apply our intent by hand onto the new upstream
version.**

| File | ours | theirs | Verdict |
|---|---:|---:|---|
| `tui_gateway/server.py` | 755 | **10,450** | 🔴 **Worst.** 30 hunks in a file upstream rewrote wholesale. Re-apply by intent (§3 item 1). Budget the bulk of the merge here. |
| `tests/test_tui_gateway_server.py` | 114 | **5,810** | 🔴 Follows server.py. Expect to rewrite our test additions against the new structure. |
| `gateway/run.py` | 22 | **36,015** | 🔴 Upstream churn is total. Our 22 lines are noise — take upstream whole, re-add our lines deliberately. |
| `hermes_cli/web_server.py` | 75 | **5,086** | 🔴 Same pattern. Re-apply the `mount_spa` assets guard by hand — and note it **fails silently** if lost. |
| `hermes_state.py` | 18 | **5,423** | 🔴 Small edit, huge upstream delta. Re-apply by hand. |
| `plugins/platforms/telegram/adapter.py` | 7 | 890 | 🟠 Tiny edit, large rewrite. Re-apply. |
| `gateway/platforms/base.py` | 6 | 1,060 | ✅ **Take upstream's entirely.** Verified `git diff -w` = **0** non-whitespace lines on our side. Pure trailing-whitespace churn. Never resolve this one manually. |
| `tests/tools/test_browser_cdp_override.py` | 99 | 31 | 🟡 We changed more than upstream — our tests likely survive; reconcile normally. |
| `tools/browser_tool.py` | 85 | 130 | 🟡 Comparable magnitudes — a genuine three-way merge. Review carefully; this is CDP fail-closed logic. |
| `tools/terminal_tool.py` | 58 | 153 | 🟡 Genuine merge. Touches the desktop-exec relay carve-out. |
| `agent/prompt_builder.py` | 55 | 164 | 🟡 Genuine merge. Canvas-link protocol. |
| `agent/system_prompt.py` | 9 | 126 | 🟡 Small, manageable. |
| `tools/browser_cdp_tool.py` | 12 | 7 | 🟢 Both tiny. Trivial. |
| `tui_gateway/ws.py` | 2 | 86 | 🟢 Two lines — but they are `_attach_identity_to_transport`, which **fails silently** if lost. Verify after merging. |
| `tests/tools/test_browser_cdp_tool.py` | 18 | 186 | 🟢 |
| `tests/tools/test_browser_cloud_fallback.py` | 16 | 97 | 🟢 |
| `tests/tools/test_browser_hybrid_routing.py` | 7 | 105 | 🟢 |
| `tests/gateway/test_session_list_allowed_sources.py` | 4 | 33 | 🟢 |

All four of the big files (`gateway/run.py`, `tui_gateway/server.py`,
`hermes_state.py`, `hermes_cli/web_server.py`) **still exist upstream** — verified.
So this is a real merge, not a rename/relocation hunt.

**Strategy implied by the table:** 6 files are re-apply-by-hand, 1 is take-theirs,
and only ~5 are genuine three-way merges. That is a much smaller job than "3,076
commits behind" suggests — but `tui_gateway/server.py` alone will dominate it, and
the silent-failure items (§6) mean a clean-looking merge can still be wrong.

---

## 3. Merge-conflict hot spots, ranked

Cheap (own files, conflict only on deletion/rename upstream):
`tui_gateway/wheelbase_*.py`, `profile_router.py`, `profile_cron.py`,
`tenant_migration.py`, `plugins/wheelbase/**`, `plugins/wheelbase-desktop-exec/**`,
`wheelbase_sdk/**`, `Dockerfile.gateway`, `scripts/gateway-entrypoint.sh`,
`docs/**`, all new tests.

Expensive (Wheelbase edits inside upstream-owned code), worst first:

1. **`tui_gateway/server.py`** — by far the worst. 30 hunks in the fastest-moving
   file in the repo. When it conflicts, resolve by re-applying the *intent*
   (identity on the session dict; per-profile store for every `session.*` RPC;
   injection wrapped around all three prompt-submit paths with cleanup in
   `finally`) rather than by mechanically keeping "ours".
2. **`tools/environments/daytona.py`** — heavily rewritten around upstream's
   `BaseEnvironment` contract.
3. **`tools/browser_tool.py`** and **`tools/terminal_tool.py`** — signature
   changes (`_get_cdp_override(task_id)`) that upstream call sites don't know about.
4. **`hermes_cli/web_server.py`** — the `mount_spa` guard sits in a function
   upstream edits regularly; losing it re-breaks per-profile routing *silently*.
5. **`gateway/run.py`** — small hunk, huge file.
6. Everything else in the §2.6 table is a small appended block.

**Silent-failure watchlist** (things that break without an error, so a merge can
"succeed" and still be wrong): the `mount_spa` guard, the tenant-root walk-up,
`_attach_identity_to_transport` in `ws.py`, and the heredoc override in
`relay_env.py`.

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
