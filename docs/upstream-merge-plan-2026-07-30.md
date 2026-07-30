# Upstream Merge Plan — `wheelbase-agent` ← `NousResearch/hermes-agent`

**Date:** 2026-07-30
**Author:** derived mechanically from git; every claim below is reproducible with the command shown.
**Companion doc:** [`docs/wheelbase-fork.md`](./wheelbase-fork.md) — read §2 (what the fork is) and §3.1 (conflict map) first. This document supersedes §3.1's *verdicts* where they conflict; §3.1's estimates were made without simulating the merge.

---

## 0. TL;DR

**Recommended strategy: a single big-bang `git merge`, used as a *transport*, with a per-file resolution policy — hunk-merge the 7 small files, and `--theirs`-then-re-apply-by-intent the 3 big ones.** Not a re-fork, not a staged merge.

The merge was simulated read-only (`git merge-tree`). The result is far better than "3,076 commits behind" implies:

| | |
|---|---|
| Files git must auto-merge | 4,748 upstream-side, **0 conflicts** on all but 10 |
| **Real conflicts** | **10 files, 39 hunks, ~1,035 conflicted lines** |
| Of which `tui_gateway/server.py` | 15 hunks, **6,268 conflicted lines (86% of the pain)** |
| Our 172 additive files | **all present and unconflicted in the simulated merge tree** |
| Our 71 fork test files | **all present** |
| **Fork code we can DELETE** | **~16 of `server.py`'s 48 per-profile hunks** — upstream built the same thing (§4) |

**The best news:** upstream independently shipped per-profile session-store resolution (`_db_for_profile` / `_profile_db`) and converged on our call sites — `session.branch`, `session.title`, `session.history`, `session.status`, `session.delete`, `_finalize_session` and more. Roughly a third of the `server.py` work deletes itself, and our remaining per-profile customization **collapses from ~16 scattered hunks to one concept in one function**.

**The biggest risk is not a conflict — it is a clean auto-merge that is silently wrong.** Upstream's `_db_for_profile` resolves the profile from a **client-supplied `params["profile"]`** and, when absent, **fails open to the shared launch store**. Our fork's identity-derived fallback (`wb-<user_id>`) does not exist upstream. Adopting upstream's version as-is re-opens cross-user session bleed across four handlers at once, with no error and no failing test. See §6.1 — this is the single highest-consequence edit in the merge.

**Effort: 40–55 engineer-hours (5–7 working days), medium confidence.** The largest single line item is no longer `server.py` (6–9h) but **adapting our 71 fork test files to upstream's rebuilt test harness** (§6.5).

---

## 1. Measured baseline

```
origin    Hime-Corporation/wheelbase-agent   branch: wheelbase   HEAD = 82f5eff5f
upstream  NousResearch/hermes-agent          upstream/main = 3a2b33298 (2026-07-30)
merge-base MB = 3ef6bbd20 (v0.19.0, 2026-07-20)
behind 3,076 · ahead 99 · umbrella gitlink pinned to 82f5eff5f · origin/wheelbase == local
```

Our 194-file delta decomposes as:

| | count |
|---|---|
| Added by us (purely additive) | 172 |
| Modified by us (inside upstream-owned files) | 20 |
| Deleted by us (stale `hermes-achievements` dist artifacts) | 2 |

Of the 20 modified files, **19 are also touched upstream**; the 20th (`tools/environments/daytona.py`) is not — see §5.9.

### 1.1 The simulated merge (the single most useful measurement)

```bash
git merge-tree --write-tree --name-only HEAD upstream/main
```

This is read-only — it touches no ref, index, or working tree. It produced tree `d0285298d` and this conflict set:

| File | conflict hunks | conflicted lines | ours Δ | theirs Δ |
|---|---:|---:|---:|---:|
| `tui_gateway/server.py` | **15** | **6,268** | 755 | 10,450 |
| `tests/tools/test_browser_cloud_fallback.py` | 2 | 202 | 16 | 97 |
| `tests/tools/test_browser_cdp_override.py` | 1 | 127 | 99 | 31 |
| `gateway/run.py` | 2 | 98 | 22 | 36,015 |
| `tests/tools/test_browser_hybrid_routing.py` | 1 | 90 | 7 | 105 |
| `tests/tools/test_browser_cdp_tool.py` | 5 | 89 | 18 | 186 |
| `tools/browser_tool.py` | 3 | 52 | 85 | 130 |
| `hermes_state.py` | 2 | 42 | 18 | 5,423 |
| `tests/test_tui_gateway_server.py` | 5 | 35 | 114 | 5,810 |
| `tools/terminal_tool.py` | 3 | 32 | 58 | 153 |
| **Total** | **39** | **~1,035** | | |

**Nine of the 19 "both changed" files auto-merge cleanly** and were verified correct in the merged tree (§6.2): `agent/prompt_builder.py`, `agent/system_prompt.py`, `gateway/platforms/base.py`, `hermes_cli/web_server.py`, `plugins/platforms/telegram/adapter.py`, `tests/cli/test_cli_background_status_indicator.py`, `tests/gateway/test_session_list_allowed_sources.py`, `tools/browser_cdp_tool.py`, `tui_gateway/ws.py`.

`gateway/run.py`'s "36,015 lines changed" is **not** a rewrite — it is 155 ordinary commits on a 25k-line file. Our anchor `GatewayRunner._thread_metadata_for_target` is byte-identical between MB and upstream. Same story for `hermes_state.py` (70 commits, mixins added, our two anchor methods intact). §3.1 of the fork doc marked both 🔴 "re-apply by hand"; **that was pessimistic** — they are 2-hunk conflicts each, ~1 hour combined.

### 1.2 The one structural change that matters

Upstream **split `tui_gateway/server.py`** in `f67ca220a` *"refactor(tui): split @method handlers into methods_\* modules (mechanical move, registry set-equality verified)"* (PR #74298). `server.py` went from **123 `@method` handlers to 9**. New modules at `upstream/main`:

| New module | Lines | Holds |
|---|---:|---|
| `methods_session.py` | 3,013 | `session.create/list/most_recent/resume/delete/title/status/history/branch`, `cwd.set` — **our hot zone** |
| `methods_tools.py` | 1,912 | `command.dispatch`, `insights.get`, `rollback.*`, `browser.manage` |
| `methods_prompt.py` | 898 | `prompt.submit`, `prompt.background`, `preview.restart` — **injection sites 2 and 3** |
| `methods_complete.py` | 471 | `complete.*`, `model.*` |
| `methods_config.py` | 420 | `config.get`, `projects.*` |
| `turn_marker.py` | 159 | turn markers |
| `method_ctx.py` | 53 | `HandlerRegistry` — the split seam |

`server.py` shrank 16,281 → 13,388 lines. This is why 5 of our 15 conflicts are 500–1,900 lines each: git sees *ours modified a region / theirs deleted it*, and the deleted content is upstream code that merely **moved**. Resolving those conflict markers by hand would be pure noise.

Critically, `method_ctx.py`'s docstring states the split is **mechanical**:

> "Handler bodies are byte-identical to their pre-split server.py form; they are rebound onto server.py's globals at install time."

`HandlerRegistry.install()` rebinds each handler's `__globals__` to `server.py`'s namespace via `types.FunctionType`, so handlers still close over `_sessions`, `_ok`, `_err`, and everything else — **including any helper we add to `server.py`**. That is what makes re-application tractable: our `_transport_identity()`, `_wheelbase_explicit_cwd()`, `_request_profile()` and `_request_profile_db()` can stay in `server.py` and remain callable from handler bodies now living in `methods_session.py`.

---

## 2. Strategy: three options, evaluated

### Option A — Re-fork (start from `upstream/main`, re-apply the 99 commits' intent)

**Rejected.** It sounds cheap because only ~1,400 of our lines sit inside upstream files, but:

- You must manually reconstruct **172 additive files** across `plugins/wheelbase/` (72), `tests/plugins/` (56), `wheelbase_sdk/` (13), `tui_gateway/wheelbase_*` (5), `Dockerfile.gateway`, `scripts/gateway-entrypoint.sh`. Every one is a chance to silently drop a file. The merge simulation proves git carries all 172 across for **free, with zero conflicts**.
- You lose the merge history, and with it the fork doc's own methodology (`git diff upstream/main..HEAD` derives the fork delta from the merge-base). Six prior merges are on record specifically *not* rebased for this reason.
- You gain nothing git wasn't already going to do correctly.
- The 2 deletions (`plugins/hermes-achievements/dashboard/dist/*`) also survive the merge correctly — verified: those paths are absent from the merged tree.

### Option B — Staged merge (chunk by tag or date)

**Not available, and wouldn't help.**

- **No tag exists between MB and `upstream/main`.** Verified by walking every tag with `git merge-base --is-ancestor`; the range contains zero release commits. Staging would require picking arbitrary SHAs.
- Each intermediate merge would re-conflict `server.py` — you'd resolve the same 15 hunks N times against N intermediate shapes of a file that is being actively split apart. Strictly more work.
- Upstream velocity is **~250 commits/day** (weeks 29–30 alone account for 2,437 of the 3,076). Staging stretches the window; the goalpost moves faster than you close on it.

### Option C — Big-bang merge as transport + per-file resolution policy ✅ **RECOMMENDED**

Run one `git merge upstream/main`, then resolve each of the 10 conflicts with a **policy chosen per file by the ours/theirs ratio**, not by mechanically editing conflict markers:

| Policy | Files | Why |
|---|---|---|
| **Take upstream, then re-apply intent** (`git checkout --theirs`, then hand-edit) | `tui_gateway/server.py`, `tests/test_tui_gateway_server.py` | Upstream restructured; conflict markers are 86% moved-code noise |
| **Resolve hunks normally** (genuine 3-way) | `gateway/run.py`, `hermes_state.py`, `tools/terminal_tool.py`, the 4 browser test files | Small, well-localised, anchors intact |
| **Redesign** | `tools/browser_tool.py` | Both sides independently reworked `_get_cdp_override` (§5.5) |
| **Take upstream wholesale, unconditionally** | `gateway/platforms/base.py` | Whitespace-only on our side — **and git does NOT do this for you**, see §5.10 |

**Why this beats a re-fork:** git does the 4,748-file upstream side and the 172-file additive side perfectly; you spend 100% of your attention on the ~1,035 lines that actually need judgement. **Why it beats staging:** one conflict resolution per file instead of N.

**One scheduling constraint follows from the velocity number:** do this in one tight window (target ≤ 2 weeks wall-clock). At 250 commits/day, a merge that drags for a month is a merge you will have to redo.

---

## 3. Phased execution plan

Ordering principle: **get to a green, deployable baseline before touching `server.py`.** Every phase ends at a commit; the branch is never left mid-merge.

> ⚠️ **Never `git stash` during any phase.** This has cost the team twice (`MEMORY.md` → *never stash mid-merge*): stashing drops `MERGE_HEAD` and can silently lose resolved conflicts even after `pop`.

### Phase 0 — Preparation (≈1h)

```bash
cd wheelbase-agent
git fetch upstream --no-tags                 # re-verify; 3a2b33298 may have moved
git branch backup/wheelbase-premerge-82f5eff5f    # matches existing backup/* convention
git push origin backup/wheelbase-premerge-82f5eff5f
git merge-tree --write-tree --name-only HEAD upstream/main   # re-derive the conflict set
```

- Re-run the §1.1 table. If `server.py`'s conflict profile has changed materially, re-read §5.1 before proceeding.
- Snapshot the current gateway container's behaviour so you have a "before" (§6.4).
- Confirm `git status` is clean apart from the known noise (`package-lock.json`, `wheelbase_sdk/*.egg-info`).

### Phase 1 — The merge + all cheap resolutions (≈4h) → **commit 1**

```bash
git merge upstream/main          # expect the 10 conflicts from §1.1
```

Resolve in this order (cheapest first, to reach a compiling tree fast):

1. `gateway/platforms/base.py` — **`git checkout upstream/main -- gateway/platforms/base.py`.** Do this explicitly; it is *not* in the conflict list, and git's auto-merge silently keeps our trailing-whitespace churn (verified: merged tree differs from upstream by 3 whitespace-only lines).
2. `hermes_state.py` (§5.3) — 2 hunks, ~0.5h
3. `gateway/run.py` (§5.2) — 2 hunks, ~1h
4. `tools/terminal_tool.py` (§5.6) — 3 dict-literal collisions, ~2h
5. Defer the 4 browser test files and `browser_tool.py` to Phase 3; defer `server.py` + its test to Phase 4. Resolve their conflicts provisionally with `git checkout --ours` so the tree compiles, and **record that as a known-incomplete state in the commit message.**

Also in this phase, verify the nine clean auto-merges actually landed correctly (§6.2) — do not assume.

Gate: `python -c "import tui_gateway.server, hermes_cli.web_server, gateway.run"` succeeds.

### Phase 2 — Re-apply the small hand-carried patches (≈3h) → **commit 2**

`hermes_cli/web_server.py`, `agent/prompt_builder.py`, `agent/system_prompt.py`, `plugins/platforms/telegram/adapter.py`, `tui_gateway/ws.py`, `tests/gateway/test_session_list_allowed_sources.py` all auto-merged. This phase is **verification, plus the two follow-ups they need**:

- Relocate `/api/cron/channels` from `hermes_cli/web_server.py` into `hermes_cli/web_routers/cron.py` (§5.4) — it still *works* where auto-merge left it, but its siblings all moved.
- Add the `ws.py` identity regression test and log line (§5.8, §6.3).

Gate: `scripts/run_tests.sh tests/gateway/ tests/test_wheelbase_*.py tests/plugins/` green.

### Phase 3 — Browser + terminal tooling (≈12–16h) → **commit 3**

`tools/browser_tool.py` (redesign, §5.5), `tools/browser_cdp_tool.py` (§5.7), and the four `tests/tools/test_browser_*.py` files. This is a self-contained subsystem; keeping it out of the `server.py` commit means a bisect can tell CDP regressions from gateway regressions.

Gate: `scripts/run_tests.sh tests/tools/` green + the CDP fail-closed manual check (§6.3).

### Phase 4 — `tui_gateway/server.py` in isolation (≈6–9h) → **commit 4**

Done alone, on its own commit, after everything else is green. Full recipe in §5.1.

```bash
git checkout upstream/main -- tui_gateway/server.py      # discard the 6,268-line conflict
# then: delete the ~16 converged hunks (§4.1), and re-apply the rest across
# server.py / methods_session.py / methods_prompt.py / methods_tools.py
```

Gate: §5.1's grep checklist + the §6.1 security tests + the multi-user isolation suite + §6.4 runtime proofs.

### Phase 5 — Test reconciliation (≈8–14h) → **commit 5**

`tests/test_tui_gateway_server.py` plus the inevitable "reconcile fork tests after upstream merge" sweep. Every prior merge in this repo needed one (`f1b00a6da`, `028061bd7`); budget for it as normal, not as a signal of breakage. **New this time:** upstream rebuilt the test harness — see §6.5.

### Phase 6 — Deploy validation (≈3–4h)

Build `Dockerfile.gateway`, deploy to **one non-production tenant first**, run §6.4's runtime proofs against it, watch for 24h, then roll the rest. Bump the umbrella gitlink last.

---

## 4. What we can DELETE because upstream now does it

### 4.1 ✅ Per-profile session-store resolution — upstream built it, delete ~16 hunks

**The best finding of this analysis.** Upstream independently shipped the core of what `a54c8ab40` / `242463e33` / `d166d49a8` added to the fork:

```python
# upstream/main:tui_gateway/server.py:1146  — absent from our HEAD
def _db_for_profile(profile: str | None = None):
    """Return SessionDB for ``params.profile`` when it differs from launch. ..."""

# upstream/main:tui_gateway/server.py:1173
@contextlib.contextmanager
def _profile_db(params: dict | None = None):
```

These are structurally our `_request_profile_db` (`HEAD:tui_gateway/server.py:2141`), and upstream has already applied them to `session.list`, `session.most_recent`, `session.delete` and `session.status`. It also converged, on its own, on the exact call sites we hand-patched:

| Our customization | Upstream now | Verdict |
|---|---|---|
| `_finalize_session` → `_session_db` | byte-identical | **DELETE** |
| `_session_live_title` → `_session_db` | converged | **DELETE** |
| `session.title` wrapped in `_session_db` | converged | **DELETE** |
| `session.history` → `_session_db` | converged | **DELETE** |
| `session.status` per-profile db | converged (`_session_db` + `_profile_db` fallback) | **DELETE** |
| `session.delete` per-profile db + profile `sessions_dir` | converged | **DELETE** (1-line profile-source swap remains) |
| `session.list` / `session.most_recent` per-profile db | converged via `_profile_db(params)` | **DELETE** the wrapper |
| `session.branch` — 3 hunks (`_session_db`, branch db, `HERMES_HOME` override) | **fully converged** — `methods_session.py:2649-2679` does `parent_home`, `SessionDB(parent_home/state.db)`, `set_hermes_home_override`, `session_db=` on both `_make_agent` and `_init_session`, `reset_` in `finally` | **DELETE ALL 3** |
| `pending_title` `_pdb` in `_run_prompt_submit` | converged (`with _session_db(session) as _pdb`) | **DELETE** |
| `_set_session_cwd` persist-cwd half | converged (`_session_db` + `update_session_cwd`) | **DELETE** that half |
| 2 whitespace-only hunks in `session.resume` | n/a | **DROP** |
| `list_sessions_rich(offset=…)` pagination | **native upstream** (`hermes_state.py:5003`) | **DELETE** ours |
| `_on_tool_complete` `is_todo` restructure | native seam `_tool_lifecycle_required_for_ui(name)` (`server.py:3944`, `return name == "clarify"`) | **REPLACE** with a one-word edit: `return name in {"clarify", "todo"}` |

**~16 of `server.py`'s 48 per-profile hunks disappear.** More importantly, everything that remains **collapses to one concept**: upstream resolves the profile from `params["profile"]` *only*; we need it to fall back to `wb-<user_id>` from the connection identity. Patch that in **one place** (`_db_for_profile`) and all four already-converged handlers inherit it for free — instead of maintaining five `_request_profile(params)` call sites forever.

That is a genuine reduction in permanent fork surface, and it is why this merge is worth doing rather than deferring.

⚠️ **But see §6.1 before adopting `_db_for_profile` — it fails open.**

### 4.2 ✅ `tools/environments/daytona.py` — free

`git diff --stat MB upstream/main -- tools/environments/daytona.py` → **empty**. The file exists at the same path; `DaytonaEnvironment` is defined nowhere else upstream. Our +183 lines (`always_on`, `_call_with_timeout`, `ensure_cwd()`, procps-free `/proc` process-tree kill) carry across untouched. The fork doc ranks this the **#2 most expensive file**; it now costs nothing.

### 4.3 ❌ Everything else — still needed

Checked explicitly for every remaining customization; all genuinely negative results:

| Fork customization | Does upstream now provide it? |
|---|---|
| Per-task CDP registry (`register_task_cdp_url`, `_task_cdp_urls`) | No — absent from upstream entirely |
| Fail-closed CDP discovery | **No** — upstream's `_resolve_cdp_override` still `return`s the raw endpoint on failure |
| `always_on` / keep-warm sandbox | No — no `always_on`/`keep_warm` concept upstream |
| Daytona control-plane call timeouts (`_call_with_timeout`) | No — still unbounded upstream |
| `register_task_env_overrides` merging | No — upstream still does a full dict replace |
| `mount_spa` missing-`assets` guard | **No** — upstream is still `if _headless or not WEB_DIST.exists():` |
| `GET /api/cron/channels` | No — `git grep cron/channels upstream/main` → nothing |
| `user_id` scoping on `list_sessions_rich` / `session_count` | No — neither gained user scoping (`session_count` doesn't exist upstream at all) |
| Telegram forum "General" topic media threading | **No** — upstream built a large DM-topic subsystem (`_GENERAL_TOPIC_THREAD_ID`, `_effective_message_thread_id`, `_message_thread_id_for_send`) but it is scoped to `chat_type == "dm"`; the group/supergroup/forum reply-anchor case is still unfixed |
| Multi-user profile router / tenant keying | No — upstream has no multi-user gateway concept (`git grep -i tenant upstream/main -- tui_gateway/` → empty) |
| `user_id` on `session_count` | No — `session_count` does not exist upstream at all |
| `_request_profile` identity derivation | No — **fork-only, and upstream's replacement actively fails open (§6.1)** |
| `identity.update` RPC | No — fork-only, re-add wholesale |

---

## 5. Per-file re-application recipes

### 5.1 `tui_gateway/server.py` — 🔴 the largest conflict (but no longer the largest cost)

**Policy: `git checkout upstream/main -- tui_gateway/server.py`, then re-apply intent.** Do not edit the conflict markers.

**Why:** 15 conflict hunks totalling 6,268 lines, of which the five largest (1,885 / 1,332 / 816 / 708 / 591 lines) contain functions like `_lazy_resume_info`, `_spawn_trees_root`, `_notification_event_belongs_elsewhere` — **upstream code that moved into `methods_*.py`**, not our code. Our actual edits are 72 small hunks scattered inside.

**Anchor survival: excellent.** All 27 module-level helpers we touch still exist in `server.py`, unrenamed (`_finalize_session`, `_session_db`, `_ensure_session_db_row`, `_run_prompt_submit`, `_set_session_cwd`, `_background_agent_kwargs`, `_on_tool_complete`, `_probe_urls`, `_normalize_cdp_url`, `_browser_connect`, `_profile_home`, `_get_db`, …). Only two signatures changed, **neither of which we call**: `_run_prompt_submit(rid, sid, session, text, *, display_kind=None, display_metadata=None)` and `_ensure_session_db_row`'s internal `cwd=_persisted_session_cwd(session)` plus a new `profile_name=` kwarg.

**Where our anchors live now:**

| Our anchor | New home |
|---|---|
| `session.create`, `session.list`, `session.resume`, `session.branch` | **`methods_session.py`** |
| `prompt.background`, `preview.restart` (injection sites 2 and 3) | **`methods_prompt.py`** |
| `insights.get`, `command.dispatch` undo | `methods_tools.py` |
| `_run_prompt_submit` (injection site 1), `_on_tool_complete`, `_normalize_cdp_url`, `_probe_urls`, `_browser_connect`, `_session_db`, `_finalize_session`, `maybe_auto_title` | **still `server.py`** |
| `identity.update` RPC | **fork-only** — absent upstream, add to `methods_session.py` |

**Step 0 — delete first, then re-apply.** Work through §4.1's table and *drop* the ~16 converged hunks before writing any code. Re-applying work upstream already did is the main way this file's estimate blows up.

**The re-application checklist — the surviving marker sites.** Treat this as the definition of done. (Line numbers are from `HEAD`; use them to find the code, not to place it.)

```
server.py  1928  def _transport_identity()
server.py  1939  def _wheelbase_explicit_cwd(session)
server.py  1981  ident = session.get("wheelbase_identity")
server.py  2130  ident = _transport_identity()
server.py  2213  if session.get("wheelbase_identity") is not None:
server.py  2214  from tui_gateway.wheelbase_inject import contain_workspace_path
server.py  5981  "wheelbase_identity": _transport_identity(),      <- session dict seed
server.py  6045  ident = _transport_identity()
server.py  6157  ident = _transport_identity()
server.py  6184  @method("identity.update")                        <- fork-only RPC
server.py  6193  ident = _transport_identity()
server.py  6200  from tui_gateway.wheelbase_identity import update_user_jwt, write_credential_file
server.py  6429  resume_ident = _transport_identity()
server.py  6717  _sessions[sid]["wheelbase_identity"] = resume_ident
server.py 10223  wb_ident = session.get("wheelbase_identity")      \
server.py 10225  from tui_gateway.wheelbase_inject import ...       | injection site 1
server.py 10227  wb_inject_cleanup = apply_session_injection(       | (_run_prompt_submit)
server.py 10232  explicit_cwd=_wheelbase_explicit_cwd(session),     |
server.py 10656  logger.exception("wheelbase injection cleanup failed")   /
server.py 11391-11434   injection site 2 (background)  — same 5-line shape
server.py 11517-11564   injection site 3 (preview)     — same 5-line shape
```

Plus the fork helpers that must be re-added to `server.py`'s globals (so the rebound `methods_*` handlers close over them): `_transport_identity()`, `_wheelbase_explicit_cwd()`, and the identity fallback inside `_db_for_profile()`.

**Residual per-profile db swaps upstream did *not* converge** (these still need re-applying): `insights.get` (`methods_tools.py:1216`), the undo path in `command.dispatch` (`methods_tools.py:845`), `replace_messages` in `prompt.submit` (`methods_prompt.py:215`), `maybe_auto_title` (`server.py:9518`), and `_background_agent_kwargs`' `"session_db": _get_db()`.

**Order of work:**

1. Delete the ~16 converged hunks (§4.1). Do this before anything else.
2. Add `_transport_identity()` / `_wheelbase_explicit_cwd()` to `server.py`, plus the `identity.update` RPC in `methods_session.py`.
3. **§6.1's security edit** — add the identity fallback to `_db_for_profile` and make it fail closed. **Highest-consequence edit in the merge.** One function, four handlers inherit it.
4. Re-add the identity seed in `session.create` (`methods_session.py:109`, alongside the surviving `"transport": current_transport() or _stdio_transport`) and `_sessions[sid]["wheelbase_identity"] = resume_ident` (`methods_session.py:669`); thread `user_id=` into `_ensure_session_db_row`.
5. Re-apply the three `apply_session_injection` wrappers with `finally:` cleanup — site 1 after `_register_session_cwd(session)` at `server.py:9046` (cleanup *before* `reset_hermes_home_override(home_token)`), sites 2 and 3 in `methods_prompt.py`'s `prompt.background` and `preview.restart` `run()` bodies (both `run()`/`finally` structures unchanged). All three share one 5-line shape.
6. Re-apply the ownership gate on `session.resume`, after compression-tip resolution.
7. `_set_session_cwd` containment early-return (`contain_workspace_path`) — **note upstream now raises `ValueError` on a non-existent dir**, which our `/workspace` path must bypass.
8. `session.list` extras still ours: `user_id`, `session_count(user_id=…)`, `has_more`/`total`. (`offset` and `lineage_root_id` are now native — drop ours.)
9. Browser/CDP block — **upstream is byte-unchanged from the merge base here**, so `_redact_browser_url`, the `_probe_urls` query suffix, `_normalize_cdp_url` query preservation and the four `_browser_connect` redaction sites re-apply verbatim at `server.py:13172–13330`. Depends on Phase 3 landing first.
10. Credential-file refresh on JWT update.

**Proof of completion:**
```bash
grep -n 'apply_session_injection' tui_gateway/*.py     # exactly 3 call sites
grep -n '_transport_identity' tui_gateway/server.py    # helper + call sites
grep -n 'wheelbase_identity' tui_gateway/methods_session.py   # create + resume seeds
grep -n 'identity.update' tui_gateway/methods_session.py
grep -n 'PROFILE_PREFIX\|wb-' tui_gateway/server.py    # identity fallback inside _db_for_profile
```

**Effort: 6–9h, confidence medium-high** — far lower than the 755 / 10,450 line ratio suggests, because the split is mechanical, every anchor survived, and upstream deleted about a third of the work for us.

**Blow-up risks:** (a) a handler whose body upstream *did* change while moving, invalidating the "byte-identical" claim — **verify this in Phase 0**; (b) the `HandlerRegistry.install()` rebinding interacting badly with a helper added after install; (c) the *real* cost sitting outside this file — our ~60 other fork test files assert on old `server.py` internals, and anything that monkeypatches `server._request_profile_db` or reaches a handler as a `server.py` attribute rather than through `server._methods[...]` breaks on the split.

### 5.2 `gateway/run.py` — 🟡 2 hunks, ~1h, high confidence

Anchor `GatewayRunner._thread_metadata_for_target(...)` is **byte-identical MB→upstream** (now ~line 19598). Our patch: compute `_tg_general` (Telegram + `chat_type in {group,supergroup,forum}` + `thread_id in (None,"1")` + `reply_to_message_id` present) and, when true, return `{"thread_id": None, "telegram_reply_to_message_id": str(...), "telegram_general_reply_fallback": True}` from both the early `if thread_id is None:` short-circuit and a new `elif _tg_general:` branch placed **after** upstream's `_is_telegram_dm_topic_target` block.

Upstream's new DM-topic machinery (`_TELEGRAM_GENERAL_TOPIC_IDS`, `_recover_telegram_topic_thread_id`, `telegram_dm_topic_bindings`) is all `chat_type == "dm"` — orthogonal. Verify: `grep -c telegram_general_reply_fallback gateway/run.py` → 2, `grep -c _tg_general` → 3.

### 5.3 `hermes_state.py` — 🟢 2 hunks, ~0.5h, high confidence

Add `user_id: str = None` to `SessionDB.list_sessions_rich` (now ~line 4996) and `SessionDB.session_count` (~6835), and in each insert:

```python
if user_id:
    where_clauses.append("s.user_id = ?")
    params.append(user_id)
```

Upstream refactored the neighbouring `source` filter into `include_sources` (`s.source IN (...)`), so our old context line is gone — insert **after** the `include_sources` block, **before** `if exclude_sources:`. Keep the "Legacy rows with NULL user_id are intentionally excluded" comment; it doubles as a marker for the next merge. Verify: `grep -c 'Legacy rows with NULL user_id' hermes_state.py` → 2.

### 5.4 `hermes_cli/web_server.py` — ✅ auto-merged, 1 follow-up, ~1.5h

Both patches **survived auto-merge** — verified in the merged tree:
- `_no_dist = not WEB_DIST.exists() or not (WEB_DIST / "assets").is_dir()` / `if _headless or _no_dist:` ✅
- `@app.get("/api/cron/channels")` ✅

Upstream did **not** fix the assets case (still `if _headless or not WEB_DIST.exists():`) — our guard is still load-bearing.

Follow-up: upstream extracted cron routes to `hermes_cli/web_routers/cron.py` (`app.include_router(_cron_routes.router)`). Auto-merge left our route stranded in `web_server.py` under `@app.get`. It still functions (registered on the same `app`), but move it next to `get_cron_delivery_targets` in `web_routers/cron.py` and change the decorator to `@router.get`. All four helpers it needs (`_KNOWN_DELIVERY_PLATFORMS`, `_is_known_delivery_platform`, `_get_home_target_chat_id`, `_iter_home_target_platforms`) are unchanged in `cron/scheduler.py`.

### 5.5 `tools/browser_tool.py` — 🟠 redesign, 4–6h, confidence 65%

**This is the one file requiring genuine design work.** Both sides independently reworked `_get_cdp_override`:

- **Upstream** (`731aa0ccc`) split it into `_get_cdp_override_raw()` (no network I/O — used by all *gating* call sites: `_is_local_mode`, `_is_local_backend`, `_navigation_session_key`, `check_browser_requirements`, `browser_cdp_tool._browser_cdp_check`) and `_get_cdp_override()` (raw + `_resolve_cdp_override`, only where a connection is imminent). Motivation: a stale `cdp_url` was adding 10+s to every startup.
- **We** added a `task_id` parameter to the old, unsplit function for the per-user CDP registry.

**Resolution:** take upstream's split as the base, then:
1. Add `task_id: str = "default"` to **both** functions.
2. `_get_cdp_override_raw(task_id)` checks `_task_cdp_urls` **first** — this is a pure dict lookup, so it respects upstream's no-I/O-during-gating contract — then env, then config.
3. Re-thread `task_id` through every gating call site upstream moved to `_raw`.
4. Re-apply verbatim (clean regions, no upstream overlap): the fail-closed `return ""` and query-string preservation in `_resolve_cdp_override`; `_get_session_info`'s raise-on-failed-discovery; `_find_agent_browser`'s `AGENT_BROWSER_CLI` override.

Note upstream also moved `requests` to a lazy `__getattr__` import, with a local `import requests` inside the same `try:` we modify — a textual as well as semantic collision.

### 5.6 `tools/terminal_tool.py` — 🟡 3 hunks, 3h, confidence 75%

Good news: `register_task_env_overrides`, `_resolve_container_task_id`, and `_cleanup_inactive_envs` are **byte-identical MB→upstream** — our merge-vs-replace, `sandbox_key` precedence, `docker_volumes` in `_ISOLATION_KEYS`, and always-on reaper skip all apply clean.

The three conflicts are all **dict-literal insertion collisions** caused by upstream adding a `vercel_sandbox` backend at the same anchors we insert at: `_get_env_config` (our `daytona_always_on` vs their `vercel_*`, both after `daytona_image`), `_create_environment` (our `always_on=` on the Daytona branch, immediately followed by their new `elif env_type == "vercel_sandbox":`), and `terminal_tool()`'s `container_config` (our merged `docker_volumes`/`docker_env` vs their `vercel_runtime`). Resolution is mechanical key-interleaving.

**Coupling verified safe:** `plugins/wheelbase-desktop-exec/` reaches into `tools.terminal_tool._active_environments` and `_resolve_container_task_id` — both still exist upstream with unchanged shapes. `tools/file_tools.py`'s result envelope changed only by migrating hand-rolled `json.dumps({"error": ...})` to the shared `tool_error()` helper, which has the identical `{"error": str(message), **extra}` shape at both MB and upstream — the plugin's mirrored envelopes stay compatible. `BaseEnvironment._embed_stdin_heredoc` (which `relay_env.py` overrides — the silent heredoc-truncation fix) still exists at `base.py:731` and is still called at `:1150`; `ShellFileOperations._atomic_write` still appends after `cat > "$tmp"`, so **the override is still required**.

### 5.7 `tools/browser_cdp_tool.py` — 🟢 auto-merges, ~1h

Our `_resolve_cdp_endpoint(task_id)` threading auto-merges cleanly. But it leaves a latent inconsistency: upstream switched `_browser_cdp_check()` to `_get_cdp_override_raw` (no `task_id`), so after Phase 3 also update that call to `_get_cdp_override_raw(effective_task_id)`, or the CDP gate is unscoped per-user.

### 5.8 `tui_gateway/ws.py` — ✅ auto-merged, but see §6.3

Both lines survived in the merged tree (`from tui_gateway.wheelbase_identity import _attach_identity_to_transport`, and the call after `transport = WSTransport(...)`). `WSTransport` has no `__slots__` in either version, so the duck-typed attribute assignment is still legal.

**Caveat:** upstream *removed* the background-MCP-discovery block that used to sit between our anchor and `ready_ok = await transport.write_async(...)`. Do not paste against stale context. It also added `server.register_live_transport(transport)` / `unregister_live_transport` / `_release_wake_for_transport` on the same path.

### 5.9 `tools/environments/daytona.py` — ✅ FREE

`git diff --stat MB upstream/main -- tools/environments/daytona.py` → **empty**. File exists at the same path; `DaytonaEnvironment` is defined nowhere else upstream. Our +183 lines (`always_on`, `_call_with_timeout`, `ensure_cwd()`, procps-free `/proc` process-tree kill) carry across untouched. Update `docs/wheelbase-fork.md` §3 to drop it from rank #2.

### 5.10 `gateway/platforms/base.py` — ⚠️ requires an explicit action

The fork doc says "take upstream's entirely." **Git will not do that for you.** It auto-merges without conflict and *keeps our whitespace* — verified: the merged tree differs from `upstream/main` by 3 lines, all whitespace-only (`diff -w` = 0). Run `git checkout upstream/main -- gateway/platforms/base.py` explicitly in Phase 1 or the churn persists into the next merge.

### 5.11 `tests/test_tui_gateway_server.py` — cheaper than it looks (~2h)

Upstream did **not** split this file (still 15,270 lines), and 10 of our 11 host tests survive, as do both helpers (`_stub_urlopen`, `_stub_urlopen_capture`). Our 114 lines are 9 mechanical fixture shims plus 4 genuinely new tests:

- 5× `_FakeDB.create_session` shims — keep, but the signature is now `(…, cwd=None, profile_name=None)`; add `user_id=None` alongside. One host (`test_ensure_session_db_row_defaults_to_no_workspace`) **was deleted upstream** — drop that shim.
- 3× `list_sessions_rich(…, user_id=None)` shims — keep as-is; upstream's signatures at 11789/11808/11826 are unchanged.
- `manual_chrome_debug_command` patch — keep.
- The 4 new CDP tests — **keep verbatim**; the code under test is unchanged upstream. Add the missing `import logging`.

### 5.12 Small test files

- `tests/gateway/test_session_list_allowed_sources.py` — auto-merges. Upstream's prune wave deleted 3 of 4 tests; our `_StubDB.session_count` addition is in a non-overlapping region. **Keep it** — `session_count` does not exist upstream at all, and `server.py`'s `session.list` calls it unconditionally, so without the stub every test in the file `AttributeError`s.
- The four `tests/tools/test_browser_*.py` files — see §7.

---

## 6. Verification strategy

Passing tests **do not** prove this merge is correct. The verification below is layered deliberately: greps prove code exists, tests prove behaviour, and runtime proofs prove the things that fail silently.

### 6.1 🔴 THE CRITICAL CHECK — client-supplied `profile` in `session.*`

**This is the finding that should change how the merge is executed.**

§4.1 is good news with a sharp edge. Upstream's replacement for our `_request_profile_db` **fails open**:

```python
# upstream/main:tui_gateway/server.py:1146
def _db_for_profile(profile: str | None = None):
    profile_home = _profile_home(profile)
    if profile_home is None:
        return _get_db(), False        # <-- the SHARED LAUNCH STORE
    ...

# upstream/main:tui_gateway/server.py:1173
@contextlib.contextmanager
def _profile_db(params: dict | None = None):
    profile = None
    if isinstance(params, dict):
        profile = (params.get("profile") or "").strip() or None   # <-- client-supplied, no identity
```

Our fork's `_request_profile` (`HEAD:tui_gateway/server.py`, called at 5 sites) derives the profile from the **connection identity** when no param is given:

```python
profile = (params.get("profile") or "").strip() or None
if profile is None:
    ident = _transport_identity()
    if ident is not None and ident.user_id:
        from tui_gateway.profile_router import PROFILE_PREFIX
        profile = f"{PROFILE_PREFIX}{ident.user_id}"
```

`_request_profile` does not exist anywhere upstream. Adopting `_db_for_profile`/`_profile_db` unmodified therefore produces **two** distinct failures:

- **Silent fallback to the shared store.** Every mobile client and every desktop in own-profile mode sends no `profile` param → `_profile_home(None)` → `None` → `_get_db()`. The session binds the shared launch store instead of `wb-<uid>`. This is exactly the regression `242463e33` and `a54c8ab40` were written to fix — *"new chat doesn't show up / wrong agent"* and cross-user history bleed. **No exception. No failing test.** And because upstream routes `session.list`, `session.most_recent`, `session.delete` and `session.status` through the same helper, it lands on four handlers at once.
- **A second fail-open even when identity is used.** `_profile_home()` returns `None` whenever the profile *directory does not exist* — so a not-yet-provisioned `wb-<uid>` also silently resolves to the shared store rather than erroring.
- **The client-supplied path stays open.** The router's `?profile=` rejection (`f767ee762`) is a **query-string guard on the REST path only** (`_sanitized_query` → 403). The WS proxy pump forwards `receive_text()` **verbatim**, with zero JSON-RPC body inspection — so `{"method":"session.create","params":{"profile":"wb-<someone-else>"}}` was never covered by that fix. Pre-existing (our `_request_profile` also honours an explicit param), but the merge grows the number of handlers reading it from 3 to 7.

**Actions:**
1. **Phase 4, first code change:** add the identity fallback *inside* `_db_for_profile`, and make it **fail closed** — if an identity is present and its profile home is missing, raise rather than returning `_get_db()`. One function; all four converged handlers inherit it.
2. Audit the remaining raw reads: `methods_session.py:42`, `:316`, `:820`, `:2195`, and `server.py:1181`, `:1244`, `:2109`.
3. **Follow-up (separate commit, not merge-blocking):** make the resolver *ignore* a client-supplied `profile` whenever a connection identity is present, and add JSON-RPC body screening to the router's WS pump. This closes the WS-body twin of `f767ee762`.
4. **Regression tests (security-grade, treat failures as incidents):**
   - WS carrying `X-Wheelbase-User-Id: alice`, `session.create` with `params={"profile": "wb-bob"}` → session store must be alice's.
   - WS carrying alice's identity, **no** `profile` param → store must be `wb-alice`, **not** the launch store.
   - WS carrying alice's identity, `wb-alice` directory absent → must raise, not silently fall back.

### 6.2 Prove the nine clean auto-merges actually landed

These produced no conflict, so nothing draws your attention to them. Verify explicitly (these were confirmed correct in the *simulated* tree; re-confirm in the real one):

```bash
grep -n '_no_dist\|assets").is_dir()' hermes_cli/web_server.py     # mount_spa guard -> 1 hit each
grep -n 'cron/channels' hermes_cli/web_server.py hermes_cli/web_routers/cron.py
grep -n '_attach_identity_to_transport' tui_gateway/ws.py           # -> 2 hits (import + call)
grep -c 'WHEELBASE_CANVAS_PROTOCOL_HINT' agent/prompt_builder.py agent/system_prompt.py
grep -n 'inventory_search' agent/system_prompt.py                   # stable-tier gate intact
grep -n 'telegram_general_reply_fallback' plugins/platforms/telegram/adapter.py
grep -n 'def session_count' tests/gateway/test_session_list_allowed_sources.py
git diff upstream/main -- gateway/platforms/base.py                 # MUST be empty (see §5.10)
```

### 6.3 Runtime proofs for the silent-failure watchlist

Tests cannot prove any of these four. Each needs an observed runtime signal.

**(a) `mount_spa` assets guard.** No existing test covers the case — all three tests touching `mount_spa` create a *complete* dist (`(dist / "assets").mkdir(parents=True)`); the original fix (`6b79a3f26`) shipped without one. `cmd_dashboard --skip-build` only pre-checks `index.html`, so this is a faithful repro of the per-profile child's startup:

```bash
mkdir -p /tmp/stub_web_dist && echo '<html></html>' > /tmp/stub_web_dist/index.html   # NO assets/
HERMES_WEB_DIST=/tmp/stub_web_dist HERMES_HOME=/tmp/test_profile_home \
HERMES_DASHBOARD_SESSION_TOKEN=test-token \
  python -m hermes_cli.main dashboard --no-open --insecure --skip-build --isolated \
    --host 127.0.0.1 --port 9400 &
sleep 3
curl -s -o /dev/null -w '%{http_code}\n' -H 'X-Hermes-Session-Token: test-token' \
     http://127.0.0.1:9400/api/status     # expect 200
curl -s http://127.0.0.1:9400/            # expect the "Frontend not built" 404 JSON
```
Without the guard the process dies at import (`RuntimeError: Directory '.../web_dist/assets' does not exist`, raised by Starlette's `StaticFiles.__init__` before uvicorn binds) — the port never opens and the first `curl` connection-refuses. **Also add the missing unit test** (mirror `test_headless_serve_disables_spa_even_with_a_dist`, but write only `index.html` and no `assets/`).

> **Correction to the fork doc:** §3/§6 describe losing this guard as causing a *silent* fallback to the shared store. `profile_router.py`'s current error path is **loud** — `ensure_child` failures produce an explicit 502 `{"error": "child unavailable"}` on REST, WS close `1011`, and a logged `failed to ensure child for REST user=…`. The genuinely silent variant of this failure mode is §6.1, not this one. Practical check either way: those log lines must be **absent** while per-profile REST/WS traffic succeeds.

**(b) `_attach_identity_to_transport`.** Existing coverage is insufficient and this was confirmed, not assumed: `tests/test_wheelbase_identity.py` calls the helper **directly** on synthetic objects (proves the function, not the wiring), and the only test that drives `handle_ws()` end-to-end uses a `FakeWS` with **no `.headers` attribute at all** — so it passes identically whether the call site exists, is dropped by a bad merge, or is deleted outright. Fix:
1. Add a `FakeWS` variant returning the six `X-Wheelbase-*` headers.
2. Capture the live `WSTransport` (monkeypatch `ws_mod.WSTransport` to record instances — the existing disconnect tests already do this).
3. Assert `transport.wheelbase_identity.user_id` / `.tenant_id` match the injected headers.
4. **Add a log line** inside `_attach_identity_to_transport` (it currently emits none): `ws identity attached peer=%s user_id=%s tenant_id=%s`. This is permanent operator-visible observability for a failure mode that is otherwise invisible in `docker logs`.

**(c) Tenant-root walk-up.** ✅ **Verified safe.** `hermes_constants.py` changed upstream (+15/−33) but we never modified it, and the load-bearing behaviour survives — `if env_path.parent.name == "profiles":` is still at `upstream/main:hermes_constants.py:186`. `tests/gateway/test_wheelbase_upstream_contracts.py` (the merge canary) is present and unconflicted in the merged tree. **Run it first, before anything else** — it exists precisely to fail loudly here.

**(d) `relay_env.py` heredoc override.** ✅ **Verified safe** — anchors intact (§5.6). Runtime proof: relay a `write_file` of a >4KB payload through a desktop session and byte-compare on the client machine. Silent truncation is the failure mode; a passing test that writes 50 bytes will not catch it.

### 6.4 Gateway runtime validation (Phase 6)

On a **non-production tenant** container:
1. Two employees connect concurrently; each `session.list` returns only their own sessions. (This is the §6.1 regression, observable end-to-end.)
2. A scheduled cron job fires for a `wb-<uid>` profile (proves `profile_cron`'s sweeper thread still ticks — per-user children never tick their own).
3. Desktop session with a relay: `terminal` runs on the user's machine; mobile session with no relay: falls through to the sandboxed cloud path.
4. Browser tool lands on the correct user's Chrome via CDP; a discovery failure yields `""` (fail-closed) rather than a shared endpoint.
5. Telegram: post a media file into a forum **General** topic; it must appear threaded, not detached.
6. Confirm `<profile_dir>/state.db` (per-user) gets fresh rows after a proxied write — **not** the shared launch store's.

### 6.5 ⚠️ The test harness changed — plan for false alarms

Upstream rebuilt the test infrastructure. `tests/conftest.py` gained **+545 lines** (we never modified it, so the merge takes upstream's wholesale) and CI switched to `scripts/run_tests.sh` → `scripts/run_tests_parallel.py`, which runs **each test file in its own freshly-spawned `python -m pytest <file>` subprocess**. There is a new `no_isolate` marker for files that share module-level state, and new machinery that **blocks `subprocess.run`/`Popen`** in tests.

Implications:
- **Run `scripts/run_tests.sh`, not `pytest tests/`.** They are no longer equivalent.
- Our **71 fork test files** have never run under this harness. Several manipulate module-level state (`_active_environments`, `_task_cdp_urls`, `_task_env_overrides`) or set `WHEELBASE_*`/`HERMES_HOME` env vars — `tests/test_profile_router.py`, `tests/test_profile_cron.py`, `tests/test_wheelbase_inject.py`, `tests/gateway/test_profile_router_tenant_keying.py`, `tests/gateway/test_tenant_auth_fallback.py`, `tests/test_profile_isolation_runtime.py`, `tests/plugins/wheelbase_desktop_exec/test_file_ops_relay.py`.
- Two fork tests spawn real subprocesses (`tests/test_profile_router.py`, `tests/plugins/wheelbase_desktop_exec/test_file_ops_relay.py`) and may trip the new subprocess blocker.
- Upstream also added a `tui_gateway.server` shared-state fixture that calls `mod._close_session_by_id(sid, end_reason="test_cleanup")` on `mod._sessions` — confirming those internals survive, but also meaning our sessions now get torn down between tests.
- **The `server.py` split breaks a second class of fork test.** Anything that monkeypatches `server._request_profile_db`, or reaches a handler as a `server.py` module attribute rather than through `server._methods["session.create"]`, no longer resolves — the handler objects now live in `methods_*.py` and are rebound at install time. Audit `tests/test_wheelbase_multiuser.py`, `tests/test_wheelbase_identity.py`, `tests/test_profile_router.py`, and `tests/gateway/test_wheelbase_upstream_contracts.py` for this pattern first.

**Budget a distinct sub-task for harness adaptation, and do not read these failures as merge damage.** Prior merges each needed a "reconcile fork tests" commit; this one needs a substantially larger one — it is the most likely source of overrun in the whole plan.

---

## 7. Test-file specifics (`tests/tools/test_browser_*.py`)

Upstream ran a repo-wide test prune (`39975613b`, "prune wave 2"), which is the dominant source of the large "theirs" counts — **delete/modify collisions, not feature rewrites**:

- `test_browser_cdp_override.py` — upstream deleted 3 tests; one (`test_falls_back_to_raw_url_when_discovery_fails`) we had independently rewritten into `test_returns_empty_when_discovery_fails` → 1 real conflict. Our 5 new tests survive. **Add** coverage for the raw/resolved split and task_id registry precedence.
- `test_browser_cdp_tool.py` — heaviest. Upstream both pruned *and* rewrote `test_check_fn_*` into `test_check_fn_does_not_probe_network`, which asserts `bt.requests.get` is never called and drives the gate via `BROWSER_CDP_URL` instead of patching `_get_cdp_override`. Genuine modify/modify conflict.
- `test_browser_cloud_fallback.py` — 5 of our 8 patched tests were deleted upstream → 5 delete/modify conflicts; 3 land clean.
- `test_browser_hybrid_routing.py` — nearly the whole file was pruned. Our `_reset_routing_state` fixture hunk applies clean **but is semantically stale**: `_navigation_session_key` now calls `_get_cdp_override_raw()`, so the mock is a no-op that only passes because the real `_raw` returns `""` in a clean environment. Retarget it.

**Expect a repeat of `028061bd7`, but harder.** That commit merely widened lambdas to `lambda *a, **k` after we added `task_id`. This time the fix is to **retarget the patched symbol** (`_get_cdp_override` → `_get_cdp_override_raw`) — a correctness change, not cosmetic.

---

## 8. Rollback plan

This branch is production gateway code for every dealership tenant, so rollback must work at three levels.

**Level 1 — pre-push (free).** Nothing is pushed until Phase 5 is green. `git merge --abort` during Phase 1; `git reset --hard backup/wheelbase-premerge-82f5eff5f` after. **Never `git stash`.**

**Level 2 — pushed but not deployed.** `origin/wheelbase` and local are both `82f5eff5f`, and the umbrella gitlink pins the same SHA. Deployment is triggered separately (`bun deploy:*` / Dokploy), so a pushed branch is still inert. Revert with `git revert -m 1 <merge-sha>` (preserve history — do not force-push a branch six merges deep). Do **not** bump the umbrella gitlink until Phase 6 passes.

**Level 3 — deployed and wrong.** Redeploy the Dokploy gateway app from `backup/wheelbase-premerge-82f5eff5f`. Per-tenant `/data/hermes` volumes are not migrated by this merge, so no data rollback is required. Note `scripts/gateway-entrypoint.sh` seeds each `config.yaml` only `if [ ! -f ]` — a rollback does **not** restore a hand-edited config, and changing a seeded config in git does nothing to an existing container.

**Preconditions before Phase 6:** `backup/wheelbase-premerge-82f5eff5f` pushed to origin (precedent: `backup/pre-upstream-merge-361`, `backup/wheelbase-premerge-20260616`, `backup/wheelbase-premerge-be0b45bac`); one tenant deployed and watched for 24h before the rest.

**Deploy surface verified compatible** — every CLI flag and env var `Dockerfile.gateway` + `gateway-entrypoint.sh` depend on still exists upstream: `--skip-build`, `--isolated`, `--insecure`, `--no-open`, `HERMES_DASHBOARD_SESSION_TOKEN`, `X-Hermes-Session-Token`, `HERMES_SERVE_HEADLESS`, `HERMES_WEB_DIST`, `hermes cron tick`. `pyproject.toml` is untouched by us; `requires-python = ">=3.11,<3.14"` is unchanged (so `python:3.11-slim` still works) and all four extras (`all`, `messaging`, `anthropic`, `daytona`) still exist.

---

## 9. Effort estimate

| Phase | Work | Hours | Confidence |
|---|---|---:|---|
| 0 | Prep, backup branch, re-derive conflict set, **verify the "mechanical split" claim** | 1–2 | high |
| 1 | Merge + `base.py` + `hermes_state.py` + `gateway/run.py` + `terminal_tool.py` | 4–5 | high |
| 2 | Verify 9 auto-merges; cron-route relocation; `ws.py` test + log line | 3–4 | high |
| 3 | `browser_tool.py` redesign + `browser_cdp_tool.py` + 4 test files | 12–16 | **medium (65%)** |
| 4 | **`tui_gateway/server.py`** — delete ~16 converged hunks, re-apply the rest across 3 modules | **6–9** | medium-high |
| 5 | `tests/test_tui_gateway_server.py` (~2h) + **fork-test harness adaptation (§6.5)** | **8–14** | **medium-low** |
| 6 | Runtime proofs, single-tenant deploy, 24h watch, umbrella bump | 3–4 | medium |
| | **Total** | **40–55** | **medium** |

≈ **5–7 working days.** Compare: the last comparable catch-up (`0d8089290`, +830 commits) was a single merge plus a reconcile commit. This is ~4× the commit count, but upstream's mechanical split and its independent implementation of per-profile store resolution work *in our favour*.

**Note the shape of the estimate has changed.** `server.py` — the file the fork doc calls "by far the worst" — is no longer the largest line item. The two biggest are now **the browser/CDP redesign (Phase 3)** and **adapting 71 fork test files to a test harness that did not exist at the merge base (Phase 5)**.

**What could blow this up, ranked:**

1. **`methods_*.py` bodies are not actually byte-identical.** The whole §5.1 recipe rests on `method_ctx.py`'s claim that the split was mechanical (`f67ca220a`'s message says "registry set-equality verified"). If upstream also changed handler logic while moving it, Phase 4 goes from 6–9h to 25–40h. **Mitigate in Phase 0:** diff two or three moved handler bodies against their MB `server.py` versions.
2. **§6.1 not caught.** If `_db_for_profile` ships without the fail-closed identity fallback, you get silent cross-user session bleed across four handlers in production. The cost is not hours; it is an incident.
3. **Fork-test harness adaptation** (§6.5) balloons — 71 files under a brand-new per-file-subprocess isolation regime, several of which monkeypatch `server.py` internals that the split moved. Worst case 2–3× the Phase 5 estimate; this is the most likely overrun.
4. **`browser_tool.py` redesign** — a subtle interaction between upstream's no-I/O-during-gating contract and our per-task registry that only surfaces under real CDP load.
5. **Upstream velocity.** ~250 commits/day. A merge that takes 4 weeks needs re-merging. Re-run Phase 0 if more than 2 weeks elapse.

---

## 10. Corrections to `docs/wheelbase-fork.md`

Apply these after the merge so the next person starts from measured facts:

| §  | Says | Should say |
|---|---|---|
| §2.6 / §3 rank 2 | `tools/environments/daytona.py` is the #2 most expensive file | **Upstream has not touched it since MB — it is free.** Remove from the ranking. |
| §3.1 | `gateway/run.py` and `hermes_state.py` are 🔴 "re-apply by hand" | 🟡 — anchors byte-identical; 2 conflict hunks each, ~1h combined |
| §3.1 | `hermes_cli/web_server.py` is 🔴 | ✅ auto-merges cleanly, guard survives; only the cron-route relocation is owed |
| §2.6 / §3 rank 1 | `tui_gateway/server.py` is "by far the worst", budget the bulk of the merge here | Still the largest conflict, but **no longer the largest line item** (6–9h). Upstream split it (`f67ca220a`, 123 → 9 handlers) *mechanically*, and independently implemented ~a third of our per-profile work. |
| §3.1 | `tui_gateway/server.py`: "~30 hunks" | **72 hunks**, ~16 of which are now **deletable** (§4.1), and upstream has **split the file into 7 modules** |
| §2.6 | Fork owns per-profile store resolution for every `session.*` RPC | Upstream now ships `_db_for_profile` / `_profile_db`. Our surface shrinks to **one identity fallback in one function** — but it must fail closed (§6.1) |
| §3.1 | `gateway/platforms/base.py`: "never resolve manually" | It does not conflict — you must **explicitly** `git checkout upstream/main --` it, or our whitespace persists |
| §3/§6 | Losing the `mount_spa` guard causes a *silent* shared-store fallback | The router's path is **loud** (502 / WS 1011 / logged). The genuinely silent failure is §6.1. |
| §2.9 | Merge canary is `test_wheelbase_upstream_contracts.py` | Still true and still intact — **but add** a `ws.py` identity wiring test and a `mount_spa`-without-`assets` test; neither exists today |
| — | (absent) | Add: upstream's `_profile_scoped` / `_profile_home` read a **client-supplied** `params["profile"]`; the router's `f767ee762` guard covers only the REST query string, not the WS JSON-RPC body |
