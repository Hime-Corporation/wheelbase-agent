---
title: "fix: teach the multi-user sandbox guard about the desktop exec relay"
status: implemented
date: 2026-07-19
type: fix
target_repo: hermes-agent
origin: Windows desktop first-launch investigation (wheelbase-monorepo) — "multi-user session refused: TERMINAL_ENV='local' is not sandboxed" on every chat send
---

> **Implementation note (2026-07-19):** All four tasks below are done —
> `wheelbase-desktop-exec` added to `PROFILE_PLUGINS`, `ws_transport.py`
> written and tested (8 new tests + all 69 existing plugin tests pass),
> `_require_sandboxed_env()` made relay-aware (5 new tests + all 21 existing
> `test_wheelbase_inject.py` tests pass, 404 passed across the wider
> `tui_gateway` suite with zero regressions), and both doc side-effects
> applied. Code changes are UNCOMMITTED on branch
> `docs/desktop-relay-sandbox-guard-plan` in this repo pending review — this
> plan doc is not itself a deploy; the live gateway container has not been
> touched.

# fix: teach the multi-user sandbox guard about the desktop exec relay

## Summary

Every desktop chat send currently hits `_require_sandboxed_env()`'s refusal
(`tui_gateway/wheelbase_inject.py:83-100`) because that check has no concept
of the desktop exec relay ("ExecHub" backend-side / "exec-sidecar"
desktop-side / `wheelbase-desktop-exec` gateway-side plugin) — the mechanism
that's supposed to let desktop users run shell/file tools on their **own**
machine instead of on the shared cloud gateway container. The check only
knows "is `TERMINAL_ENV` one of the sandboxed backends" (`docker`, `daytona`,
`modal`, `singularity`); it has no branch for "this session has a working
desktop relay, so the gateway-side sandbox requirement doesn't apply to it at
all."

This is a documented architecture decision, not a bug in the check's
intent — `plugins/wheelbase-desktop-exec/__init__.py`'s own docstring already
states the design: *"for a desktop user who is online (identity carries
`shell_relay_url`), run the safety chain then relay to the user's machine.
Mobile/offline users (no relay url) and any ambiguous identity fall back to
the sandboxed cloud path via `next_call`."* The sandbox guard was just never
updated to match that design once the relay was built.

**Decided scope (do not relitigate these — confirmed 2026-07-19):**
- **Desktop**: exempt from the sandboxed-`TERMINAL_ENV` requirement — it
  delegates execution to the desktop relay instead.
- **Mobile**: unchanged — always requires a real sandboxed `TERMINAL_ENV`
  (`daytona` in practice). No local machine to delegate to.
- **Telegram / everything else** (`tui`, `cli`, unclassified): unchanged —
  stays unsandboxed as-is. This is accepted as trusted/internal-only use, not
  something this plan fixes.

## Current state (verified 2026-07-19, file:line)

**wheelbase-backend — fully built and tested.** Not a gap.
- `internal/services/agentsession/execcap.go` — `MintExecCap`/`VerifyExecCap`.
- `internal/handlers/agent_exec.go` — `ExecHub`, `DesktopConnect` (83-150,
  verifies the Supabase JWT, registers the desktop peer by userID),
  `GatewayConnect` (156-215, the gateway-side dial-in endpoint).
- `internal/handlers/agent_chat_handler.go:48-53,72-83,116-119` — mints a
  short-TTL capability token and injects it as the `X-Wheelbase-Shell-Relay-Url`
  header, symmetric with the existing `X-Wheelbase-Cdp-Url` (browser relay).
- Routes wired live: `cmd/api/routes.go:24,422-423`
  (`v1.GET("/agent/exec", ...)`), `cmd/api/internal_server.go:17`,
  `cmd/api/main.go:296-362`.
- `docs/superpowers/plans/2026-07-06-go-exechub.md` is marked "Status: Plan
  only" in its frontmatter but the code already matches it exactly — that
  status label is stale and should be corrected as a side-effect of this plan.

**wheelbase-app (Electron desktop) — fully built and tested.** Not a gap.
- `electron/runtime/exec-sidecar/` — `relay-client.ts`, `protocol.ts`,
  `dispatcher.ts`, `runner.ts` (spawns via `Bun.spawn`), `fileops.ts`,
  `jail.ts`, all with test coverage.
- `relay-client.ts:120-123` dials the backend's `/v1/agent/exec` with the raw
  Supabase JWT as a `bearer.<jwt>` WS subprotocol — confirmed this matches
  `ExecHub.DesktopConnect`'s expectation exactly (an earlier doc comment in
  `exec-relay-session.ts:16-33` describes a now-abandoned mint-endpoint design;
  the current JWT-direct approach is what's actually live on both sides).
- `desktop-runtime-supervisor.ts` manages the sidecar's lifecycle the same way
  it manages the Chrome/CDP process (`ensureExecSidecarStarted`,
  `startExecSidecar`/`stopExecSidecar`, `EXEC_RELAY_URL`/`EXEC_RELAY_TOKEN`
  env vars).
- Separately (already fixed on `wheelbase-app` — see `fix/windows-electron-issues`):
  the sidecar couldn't even start on a fresh checkout because nothing staged
  `resources/bin/bun-runtime[.exe]`. That's now fixed by
  `scripts/stage-bun-runtime.mjs`. Unrelated but adjacent: the sidecar's OS
  sandbox (`exec-sidecar-sandbox.ts`, macOS Seatbelt only) still fails closed
  on Windows/Linux — tracked separately in
  `docs/superpowers/plans/2026-07-06-exec-os-sandbox.md` in `wheelbase-app`,
  out of scope here.

**wheelbase-agent (this repo) — mostly built, two real gaps.**
- `tui_gateway/wheelbase_identity.py:26-27,44-45,68-69` reads the
  `x-wheelbase-shell-relay-url` header into `identity.shell_relay_url`,
  symmetric with `cdp_url`. Works today.
- `tui_gateway/wheelbase_inject.py:139` plumbs `shell_relay_url` into
  `wb_runtime.set_task_identity(...)`. Works today.
- `plugins/wheelbase-desktop-exec/__init__.py` — full routing middleware for
  all 7 built-in tools (terminal/process/read_file/write_file/patch/
  search_files/execute_code). Built and internally tested against
  `FakeTransport`.
- `plugins/wheelbase-desktop-exec/relay_env.py:18` —
  `DesktopRelayEnvironment(BaseEnvironment)`. Built.
- **Gap 1 (blocks the relay from ever running): `plugins/wheelbase-desktop-exec/ws_transport.py`
  does not exist.** `_make_transport` imports
  `from .ws_transport import WebsocketExecTransport`, which will `ImportError`
  at runtime. The plugin's own module docstring (lines 30-38) already lists
  this as an open item: *"authenticated WS dial to backend `/v1/agent/exec`
  carrying the signed capability token."* Everything downstream of this class
  is already implemented and tested against a fake — only the real socket
  client is missing.
- **Gap 2 (this plan's primary fix): `_require_sandboxed_env()` doesn't know
  the relay exists.** `tui_gateway/wheelbase_inject.py:83-100` is called
  unconditionally in `apply_session_injection()` (line 117) — **before**
  `shell_relay_url` is even read (line 139) — and has no branch for "identity
  carries a shell relay URL, so this session doesn't need a sandboxed
  `TERMINAL_ENV`." A desktop session with `TERMINAL_ENV=local` (the natural
  config once the relay is live, since execution happens on the user's
  machine, not the gateway) hits the same hard refusal a real unsandboxed
  multi-tenant session would.
- **Gap 3 (verify, not necessarily fix): `plugin.yaml:5` marks
  `wheelbase-desktop-exec` as `kind: standalone`** — i.e. not loaded by
  default. No loader/manifest reference to it was found anywhere in this
  repo. Confirm how/where it's actually enabled for desktop sessions in the
  deployed gateway config before shipping Gap 1 + Gap 2 — if it's never
  loaded, fixing the transport and the guard accomplishes nothing.
- `tools/terminal_tool.py`'s `_create_environment`/`check_terminal_requirements`
  (~1515-1665, ~3062) only dispatch on
  `TERMINAL_ENV ∈ {local, docker, singularity, modal, daytona, ssh}` — no
  `"desktop"`/`"relay"` value. This is fine as-is: the plugin bypasses this
  dispatch entirely by injecting `DesktopRelayEnvironment` directly into
  `tools.terminal_tool._active_environments`, so `terminal_tool.py` itself
  does not need to change for this plan.

## Global Constraints

- **Do not touch the live gateway container until this plan is reviewed** —
  it currently serves real tenant traffic (all 4 rows in `agent_gateway`
  point at the same shared app) plus the Telegram bot. This doc is
  design-only; implementation is a separate, explicitly-approved step.
- **Telegram's unsandboxed `TERMINAL_ENV=local` is an accepted, unrelated
  fact of the current deployment** — do not fold a Telegram sandboxing change
  into this plan. If that ever changes, it's its own decision.
- **Mobile must keep requiring a real sandboxed backend.** Any change to
  `_require_sandboxed_env()` must preserve "no `shell_relay_url` → still
  needs `TERMINAL_ENV ∈ SANDBOXED_TERMINAL_ENVS`" exactly as today.
- **Gap 3 must be checked before Gap 1/2 are considered "done."** Shipping a
  working transport + a relaxed guard is pointless if the plugin that uses
  them is never loaded.

## File Structure

| File | Responsibility |
|---|---|
| `plugins/wheelbase-desktop-exec/ws_transport.py` (create) | `WebsocketExecTransport(ExecTransport)` — real WS dial to the Go backend's `/internal/agent/exec/:userID/ws?token=<cap>`, implementing `send`/`recv`/`close` per `transport.py`'s ABC. |
| `tui_gateway/wheelbase_inject.py` (modify) | `_require_sandboxed_env()` gains a `shell_relay_url` (or equivalent identity) parameter/check; skip the sandboxed-`TERMINAL_ENV` requirement when it's present. |
| `tests/tui_gateway/test_wheelbase_inject.py` or nearest existing test module (modify/create) | Cover: desktop + relay available → no refusal even with `TERMINAL_ENV=local`; mobile (no relay url) + `TERMINAL_ENV=local` → still refused; existing sandboxed-env-passes and unsandboxed-refused cases keep passing unchanged. |
| gateway deployment config (wherever plugins are enabled — location TBD, see Gap 3) | Confirm/enable `wheelbase-desktop-exec` is actually loaded for desktop-sourced sessions. |
| `wheelbase-backend/docs/superpowers/plans/2026-07-06-go-exechub.md` (modify, different repo) | Frontmatter `status: plan only` → `status: shipped` (or equivalent) — the code already matches the plan. |
| `wheelbase-app/CLAUDE.md` (modify, different repo) | Currently documents the CDP/Chrome relay but never mentions the exec-sidecar/ExecHub relay despite it being fully built — add a short section. |

## Task 1: Confirm plugin load path (Gap 3) — do this FIRST

Before writing any code, find where Hermes plugins get enabled for a running
gateway session (profile config? `PROFILE_PLUGINS`-style constant? an env
var?) and confirm whether `wheelbase-desktop-exec` is reachable for a
desktop-sourced session today. If it isn't wired in anywhere, that's an
additional task this plan must add, not an assumption to skip.

- [x] Trace how `platform=="desktop"` sessions get their plugin set today
      (compare against how `session_search` gets disabled per-profile in
      `tui_gateway/profile_router.py`'s `PROFILE_DISABLED_TOOLSETS`, from the
      2026-06-18 multi-user-isolation plan — likely the same
      injection/back-fill point).
- [x] Document the actual answer here before proceeding to Task 2/3.

## Task 2: Write `ws_transport.py` (Gap 1)

**Files:**
- Create: `plugins/wheelbase-desktop-exec/ws_transport.py`
- Test: mirror the existing `FakeTransport`-based test file for this plugin,
  adding real-transport-specific cases (connect failure, auth-reject,
  mid-stream disconnect) against a local mock WS server rather than the real
  Go backend.

**Interfaces:**
- Implements `ExecTransport` (`transport.py:22-33`): `send(frame)`,
  `recv(request_id, timeout=None) -> dict`, optional `close()`.
- Raises `PreDispatchError` (per `transport.py:13-19`) for any failure
  *before* the desktop has started executing (connection not established) —
  never for a failure *after* dispatch, which must surface as a normal tool
  error instead (spec §5.1 M4, cited in the existing docstring).
- Reads its target URL + capability token from wherever `_make_transport`
  currently expects them (check the call site — likely
  `identity.shell_relay_url` plus a token embedded in that URL or a sibling
  field).

- [x] **Step 1: Write the failing test(s)** against a local mock WS server —
      connect success, `send`/`recv` round-trip keyed by `request_id`,
      connect failure → `PreDispatchError`, mid-stream drop → NOT a
      `PreDispatchError` (per the existing pre-vs-post-dispatch contract this
      plugin already enforces elsewhere).
- [x] **Step 2: Implement `WebsocketExecTransport`.**
- [x] **Step 3: Wire it into `_make_transport`** (currently raises/fails the
      import — replace with the real class).
- [x] **Step 4: Run the full `wheelbase-desktop-exec` test suite** to confirm
      no regression in the already-tested `FakeTransport` paths.

## Task 3: Make `_require_sandboxed_env()` relay-aware (Gap 2 — the actual reported bug)

**Files:**
- Modify: `tui_gateway/wheelbase_inject.py:83-100` (`_require_sandboxed_env`)
  and its call site in `apply_session_injection` (~line 117) so the relay
  identity is available at check time (today the check runs *before*
  `shell_relay_url` is read at line 139 — reorder or pass it through
  explicitly).

**Interfaces:**
- `_require_sandboxed_env(shell_relay_url: str | None = None) -> str` (or
  equivalent) — when `shell_relay_url` is truthy, return early without
  requiring `TERMINAL_ENV ∈ SANDBOXED_TERMINAL_ENVS`. When falsy, behavior is
  **byte-for-byte unchanged** from today (mobile, Telegram, everything else).

- [x] **Step 1: Write the failing tests**:
  - desktop session, `shell_relay_url` set, `TERMINAL_ENV=local` → no
    refusal.
  - desktop session, `shell_relay_url` set, `TERMINAL_ENV=daytona` → no
    refusal (already sandboxed AND relay-available; must not double-require).
  - mobile session (or any session with no `shell_relay_url`),
    `TERMINAL_ENV=local` → refusal, unchanged from today.
  - mobile session, `TERMINAL_ENV=daytona` → no refusal, unchanged from
    today.
  - `WHEELBASE_ALLOW_UNSANDBOXED=1` escape hatch → still works exactly as
    today for the no-relay case (dev/test only, per its existing docstring).
- [x] **Step 2: Implement the change.**
- [x] **Step 3: Run the full `wheelbase_inject`/`server.py` sandbox-related
      test suite** (whatever currently covers `_require_sandboxed_env`) to
      confirm no regression.

## Task 4: Doc cleanup (side-effects, low priority)

- [x] `wheelbase-backend/docs/superpowers/plans/2026-07-06-go-exechub.md`:
      update `status: plan only` → reflect that the code is shipped.
- [x] `wheelbase-app/CLAUDE.md`: add a short "Local exec relay" section
      alongside the existing CDP/Chrome relay docs — currently the fully-built
      exec-sidecar mechanism isn't mentioned at all.

## Explicitly out of scope for this plan

- Telegram sandboxing (decided: leave as-is).
- `exec-sidecar-sandbox.ts`'s Windows/Linux OS-sandbox gap (tracked in
  `wheelbase-app`'s own `2026-07-06-exec-os-sandbox.md`).
- Splitting the shared Telegram+tenant gateway container into separate apps
  (was raised as an option, not chosen).
- Any change to `tools/terminal_tool.py`'s `TERMINAL_ENV` dispatch set — the
  plugin's cache-injection approach means this doesn't need a new
  `"desktop"` env value.
