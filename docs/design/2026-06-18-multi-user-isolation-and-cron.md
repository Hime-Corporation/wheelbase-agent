# Multi-User Per-Tenant Isolation Hardening + Per-User Background Cron

- **Date:** 2026-06-18
- **Repo:** `wheelbase-agent` (Wheelbase fork of Hermes)
- **Status:** Draft for review
- **Author:** Wheelbase eng (via Claude Code brainstorming)

---

## 1. Context & Problem

Wheelbase runs Hermes as a multi-user agent for used-car dealership staff. The
deployment model is:

- **One Docker container per tenant** (dealership) = the tenant trust boundary.
- Inside it, a **24/7 parent router** (`tui_gateway/profile_router.py`, port
  9320) authenticates each request by `X-Wheelbase-User-Id` and **proxies it to
  a per-user child process** bound to `127.0.0.1` on a port in 9400–9899.
- Each child runs `--isolated` with its own `HERMES_HOME=/data/hermes/profiles/wb-<uid>`,
  its own `state.db`, `memories/`, and skills, provisioned by `provision_profile`
  (`profile_router.py:99-196`). Children are spawned on first request and kept
  warm (no idle reaper); a dead child is restarted on the next request.
- The agent's shell/file tools execute inside a **per-user Daytona sandbox**
  (`terminal.backend: daytona`), so `terminal`/`read_file`/`write_file`/
  `execute_code` cannot reach the host filesystem where sibling `HERMES_HOME`s
  live (`tools/file_tools.py:642` routes file ops through the same backend).

**Requirements:** each user's chat sessions and memory must be private (User A
must never read User B's data), while **skills, tools, and tenant data are
shared** within the tenant.

The architecture already delivers most of this. Investigation surfaced **two
concrete gaps**:

1. **`session_search` bypasses the sandbox.** It is an in-process tool (runs in
   the Hermes process, not the Daytona sandbox) and can read **any** profile's
   `state.db` by path via its `profile=` parameter and a cross-profile scan
   (`tools/session_search_tool.py:111` `_resolve_profile_db`, `:134`
   `_locate_session_db`). It ships in `_HERMES_CORE_TOOLS` (`toolsets.py:55`),
   so it is **on by default** in every child. A user's agent could read another
   user's conversation history.

2. **Per-user background cron does not run at all.** Children are launched as
   `hermes_cli.main dashboard --isolated` (`profile_router.py:187-208`), i.e.
   dashboard/web_server processes — **not** full gateways. The cron ticker only
   starts in a gateway (`gateway/run.py:_start_cron_ticker`) or desktop
   (`HERMES_DESKTOP=1`), neither of which is set for children. There is no
   multi-profile cron sweep anywhere. Consequently a `wb-<uid>` user's
   `cron/jobs.json` **never fires** — online or offline.

## 2. Goals / Non-Goals

**Goals**

- Close the `session_search` cross-profile read for non-admin users while
  keeping it for the admin instance.
- Make per-user scheduled/background work fire reliably, including while the
  user is offline (their child not spawned).
- Document and lock the **config-immutability invariants** the isolation model
  depends on.
- Inventory and decide on sibling cross-profile tools.

**Non-Goals (explicitly deferred)**

- The self-improving "overseer/coordinator" agent that monitors sessions and
  edits skills fleet-wide. Deferred; to be built standalone, later.
- Cross-tenant isolation changes (handled by the per-tenant container).
- Memory provider changes. Per-user `memories/` separation is automatic from
  per-user `HERMES_HOME`; a shared-but-scoped memory (Honcho peers) is out of
  scope here.
- Switching children from on-demand-warm to always-on persistent gateways.

## 2a. Decisions Locked (2026-06-18 review)

1. **Guard scope → session_search only; siblings deferred.** Lock the
   `session_search` disable in this spec. The skill-name metadata leak
   (`_find_skill_in_other_profiles`) and the `cross_profile=True` write flag
   are **spun out to a separate audit task** (§4.3), not implemented here.
2. **Enforcement → config disable only.** No in-child code guard. Isolation
   relies on `agent.disabled_toolsets` baked into provisioning. **Consequence:**
   the config-immutability invariants (§4.4) become the *sole* backstop and are
   therefore load-bearing — they must be enforced and tested.
3. **Cron tool surface → full toolset allowed.** Background jobs get the same
   tools as live sessions. Rationale: Daytona runs 24/7, so the unattended-hang
   concern is accepted as low. (Safety note retained in §5.4.)
4. **Cron delivery → router sweep + host-cron fallback.** Primary sweep thread
   in `profile_router`; host/k8s cron runs the same sweep as a restart-safety
   fallback; per-home lock prevents double-fire.

## 3. Isolation Model (validated context)

| Layer | Mechanism | Status |
|---|---|---|
| Tenant boundary | One Docker container per tenant | existing |
| Per-user data at rest | Separate `HERMES_HOME=wb-<uid>` (own `state.db`, `memories/`) | existing |
| Tool confinement (shell/file) | Per-user Daytona sandbox | existing |
| Cross-profile in-process reads | **`session_search` config disable** (siblings deferred) | **this spec §4** |
| Per-user background work | **Central cron sweep** | **this spec §5** |
| Shared skills | `skills.external_dirs` layering (global → tenant → user-local, local wins) | existing seam |
| Shared tenant data | Domain tools / backend with tenant-scoped credentials | existing |
| Config immutability | No config-write tool + sandbox + read-only file + pinned backend | **this spec §4.4 (invariants)** |

Admin vs. user distinction: there is **no role flag** in `WheelbaseIdentity`
(`tui_gateway/wheelbase_identity.py`). The **admin/dashboard is the main Hermes
root** (`HERMES_HOME=/data/hermes`); it is *not* a `wb-<uid>` child and never
runs through `provision_profile`. This is the lever: anything applied in
`provision_profile` affects only user children, leaving admin untouched.

## 4. Component A — Per-User Data Isolation Hardening

### 4.1 Disable `session_search` for user profiles (required)

`agent.disabled_toolsets` removes a toolset in two stages: name-removal in
`hermes_cli/tools_config.py`, then `difference_update` in
`model_tools.py:388-405` before `registry.get_definitions()`. The result is
that the tool is **fully absent from the schema the model receives** — it
cannot be called, not merely discouraged. `session_search` is its own
single-member toolset (`toolsets.py:228-232`) with no aliases, so disabling the
name `session_search` removes exactly that tool.

**Change:** in `provision_profile` (`profile_router.py:144-155`), add to the
config dict written for each child:

```yaml
agent:
  disabled_toolsets:
    - session_search
```

**Back-fill for existing profiles:** add `_ensure_session_search_disabled(config_path)`
mirroring the existing idempotent `_ensure_profile_plugins_enabled`
(`profile_router.py:87-124`) — read YAML, merge the entry if missing, rewrite
only on change. Call it alongside `_ensure_profile_plugins_enabled` (~`:157`)
so already-provisioned `wb-<uid>` children get the disable on next restart, not
just newly created ones.

### 4.2 Defense-in-depth guard — NOT in scope (decision)

Per the 2026-06-18 decision (§2a.2), enforcement is **config-only**. The
previously-proposed in-child code guard on `_locate_session_db` /
`_resolve_profile_db` is **not implemented in this spec**. The trade-off
accepted: correctness depends entirely on `agent.disabled_toolsets` being
present and on the config-immutability invariants (§4.4) holding. If a future
review wants a code-level backstop, the guard sketch lives in this section's
git history.

### 4.3 Sibling cross-profile tools — DEFERRED to a separate audit task

Per the 2026-06-18 decision (§2a.1), these are **out of scope for this spec**
and tracked as a standalone follow-up audit:

- **`skill_manager_tool._find_skill_in_other_profiles` (`:372-435`)** — scans
  all profiles' `skills/` and can leak **skill names** (not session content)
  into tool error output. Low risk.
- **`code_execution_tool` / `file_tools` `cross_profile=True` flag
  (`file_tools.py:417`)** — a write-path bypass of the cross-profile guard;
  not reachable through the sandboxed surface.
- Confirmed safe today (scoped to own `HERMES_HOME`): `memory`, `todo`,
  `cronjob`, `skills_list`, `skill_view`.

The follow-up audit should decide per-tool whether to guard, disable, or accept.

### 4.4 Config-immutability invariants (document + enforce)

The isolation rests on the agent being unable to edit its own `config.yaml`
(which carries `disabled_toolsets`). Because enforcement is **config-only**
(§2a.2), these invariants are the **sole backstop** — there is no code-level
guard behind them, so they must be asserted and kept tested, not treated as
nice-to-haves:

1. **No config-write tool exists** — Hermes exposes no `config_set`/
   `update_config` model tool; `hermes config` is CLI-only. Keep it that way.
2. **Backend stays sandboxed** — `terminal.backend: daytona` in the
   provisioning template; never fall back to `local`. Confirm Daytona-
   unreachable fails closed (hang/error), not fallback-to-local.
3. **`config.yaml` is OS-read-only** to the child's effective identity
   (owned by provisioner/root, `0444`).
4. **Sandbox file-sync cannot overwrite live config** — verify an in-sandbox
   write to `~/.hermes/config.yaml` lands only in `cache/remote-syncs/…` and
   never the live profile config (`file_tools.py:419-448`).
5. **Config-write HTTP surface unreachable** — the dashboard `/api/config`
   endpoint is not reachable from inside the sandbox network and the child
   holds no token for it.

## 5. Component B — Per-User Background Cron Sweep

### 5.1 Crux

Children do not tick cron (see §1.2). Per-user scheduled work is currently a
no-op. Fixing it must not depend on the child being alive (offline users have
no child).

### 5.2 Design

Decouple scheduling from child liveness using the existing `hermes cron tick`
(`hermes_cli/cron.py:148`), which reads `HERMES_HOME` from the environment, runs
due jobs through `oneshot.py`/`run_agent` (no dashboard needed), and is
protected by a file lock (`cron/.tick.lock`).

Add `_start_profile_cron_sweep` to the **parent router**
(`tui_gateway/profile_router.py`), running alongside the child supervisor.
Every ~60s it:

1. Iterates `/data/hermes/profiles/wb-*/cron/jobs.json`.
2. For any profile with a due job, launches a detached subprocess:
   ```
   HERMES_HOME=/data/hermes/profiles/wb-<uid>  python -m hermes_cli.cron tick
   ```
3. The `cron/.tick.lock` per-home prevents overlap with itself and with any
   future live-child ticker.

**Fallback (belt-and-suspenders):** a host/k8s cron entry running the same
sweep, so a router restart mid-minute does not drop a run. The per-home lock
keeps a double-fire safe.

### 5.3 Why this over alternatives

- **Persistent children that self-tick** — still misses offline users (never
  spawned) and costs hundreds of idle web_server processes. Rejected.
- **Shared scheduler profile fanning out via kanban/delegate** — no IPC for
  cross-home injection; over-engineered. Rejected.
- **External-only host cron** — works, but couples scheduling to deploy
  plumbing; keep it as the fallback, not the primary.

The sweep is O(profiles) `stat` calls per minute, one thread, reuses existing
`cron tick` + lock primitives. ~40 lines.

### 5.4 Cron tool surface (decision)

Per §2a.3, background cron jobs use the **full toolset** (same as live
sessions), on the basis that Daytona runs 24/7 so the unattended-hang concern is
low. **Safety note (not a blocker):** a per-sandbox stop/archive/stuck state can
still hang a tool even when the Daytona service itself is up (prior ops
finding). To bound that without restricting tools, set a hard
`HERMES_CRON_TIMEOUT` (or `delegation.child_timeout_seconds` for delegated
work) so an unattended run fails closed instead of hanging indefinitely. This
is a config knob, not new code.

### 5.5 Concurrency (default)

To bound load when many profiles have jobs due in the same minute, the sweep
caps concurrent `cron tick` subprocesses (configurable; **default 8**). Excess
due profiles run on the next sweep. Confirm the cap value during implementation.

## 6. Data Flow & Error Handling

- **Session read path:** request → router (auth) → child (own `HERMES_HOME`) →
  agent turn → tools (shell/file in Daytona; in-process tools scoped to own
  home). No path reaches a sibling home.
- **Cron path:** router sweep thread → due check per `wb-*/cron/jobs.json` →
  `cron tick` subprocess (own `HERMES_HOME`, lock-guarded) → agent run → output
  to that home / messaging.
- **Locking:** `cron/.tick.lock` per home prevents overlapping ticks (router
  thread vs. host-cron fallback vs. any future child ticker).
- **Missed runs:** 60s sweep resolution matches the existing ticker; a job due
  during a router restart is caught on the next sweep or by the host-cron
  fallback.
- **Daytona unreachable:** tools hang/fail closed (no host fallback). Cron runs
  that need the sandbox surface inherit this; surface the failure rather than
  silently degrading isolation.
- **Crash recovery:** a crashed child is re-spawned on next request (existing
  supervisor); cron is independent of child liveness post-change.

## 7. Testing Strategy

- **Isolation (A):** provision two child profiles with seeded sessions; assert a
  child agent (a) has no `session_search` in its tool schema, and (b) with the
  guard, `_locate_session_db`/`_resolve_profile_db` cannot resolve a sibling
  home even if invoked directly. Assert the admin root **retains**
  `session_search`.
- **Back-fill:** an existing profile config without the key gains
  `disabled_toolsets: [session_search]` after a re-provision; idempotent on
  repeat.
- **Config invariants:** static check that no registered tool writes
  `config.yaml`; integration check that an in-sandbox write to the mirrored
  `~/.hermes/config.yaml` does not alter the host config.
- **Cron (B):** create a due job in an offline profile (no child running);
  assert the sweep fires it exactly once; assert the per-home lock prevents a
  concurrent sweep + host-cron double-fire.

## 8. File Touch-Points

| File | Change |
|---|---|
| `tui_gateway/profile_router.py` | A: inject `disabled_toolsets` in `provision_profile` (~144-155) + `_ensure_session_search_disabled` back-fill (~157). B: `_start_profile_cron_sweep` thread + per-profile `cron tick` launcher. |
| `tools/session_search_tool.py` | _(deferred audit only — no code guard this spec, per §2a.2)_ |
| `tools/skill_manager_tool.py`, `tools/file_tools.py`, `code_execution_tool.py` | _(deferred to separate sibling audit, §4.3)_ |
| provisioning template / deploy | A: `terminal.backend: daytona` pinned, `config.yaml` `0444`. B: host/k8s cron fallback entry + `HERMES_CRON_TIMEOUT`. |
| tests | §7 coverage. |

## 9. Open Questions

All major decisions were locked on 2026-06-18 (§2a). Remaining items are
implementation-time confirmations, not blockers:

1. **Cron concurrency cap value** — defaulted to 8 concurrent `cron tick`
   subprocesses (§5.5); confirm the number against real profile counts.
2. **`HERMES_CRON_TIMEOUT` value** — pick a hard unattended-run timeout (§5.4).
3. **Sibling audit** — tracked as a separate follow-up task (§4.3), not part of
   this spec's implementation.

## 10. Sequencing

A and B are independent and can land separately. Suggested order: **A first**
(closes an active data-isolation gap), then **B** (adds a capability that does
not currently exist). Neither requires architectural rework — both are
surgical changes to `profile_router.py` plus a small in-child guard.
