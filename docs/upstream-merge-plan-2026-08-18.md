# Upstream merge implementation plan — 2026-08-18

This plan merges Hermes upstream `main` at `8911e2e0edf750b104edbdc106d63d6cdac88524`
into Wheelbase Agent at `923a24a798976dc98d0cb34474124549d931677b`.
It follows the real-merge workflow in `docs/wheelbase-fork.md`; this is not a
rebase, squash, or re-fork.

## 1. Fixed baseline

| Item | Value |
|---|---|
| Wheelbase head | `923a24a798976dc98d0cb34474124549d931677b` |
| Upstream target | `8911e2e0edf750b104edbdc106d63d6cdac88524` |
| Merge base | `6564f319a647b47de391cab2f608660323804a2b` |
| Wheelbase-only commits | 131 |
| Upstream-only commits | 2,814 |
| Upstream files changed since the base | 3,265 (`+409,677/-82,806`) |
| Wheelbase files changed since the base | 227 (`+30,075/-1,242`) |
| Files changed by both sides | 24 |
| Predicted content conflicts | 7 files, 22 conflict hunks |

The upstream target includes the changes through the August 18 commit and the
v0.20.1–v0.20.4 release range. Pin the merge to the SHA above. If upstream
`main` advances and a later SHA is desired, rerun the comparison and
`git merge-tree` preflight rather than silently substituting the new tip.

The seven predicted conflict files are:

1. `agent/title_generator.py` — 1 hunk
2. `hermes_state.py` — 2 hunks
3. `tests/agent/test_credential_pool_routing.py` — 1 hunk
4. `tools/terminal_tool.py` — 2 hunks
5. `tui_gateway/methods_prompt.py` — 5 hunks
6. `tui_gateway/methods_session.py` — 9 hunks
7. `tui_gateway/server.py` — 2 hunks

## 2. Merge decisions and invariants

The merge should adopt upstream behavior unless it violates one of these
Wheelbase invariants:

- An authenticated connection derives its `wb-<user_id>` profile from its
  immutable transport identity. A request cannot select another user's profile.
- Session list, resume, workspace move, and transcript writes stay scoped to the
  identified user and that user's profile database. Foreign and legacy `NULL`
  owner rows fail closed with the same response as a missing row.
- Public/runtime session IDs remain the durable Hermes session key on the
  Wheelbase surface; do not restore short random UI-only IDs.
- Session titles are deliberately non-unique. Do not recreate a unique title
  index, append `#N` in the normal Wheelbase store, or steal a title from a
  compression ancestor.
- `register_task_env_overrides` merges slices owned by different callers.
  `sandbox_key` remains the highest-priority, stable per-user sandbox key.
- Per-task Docker volumes append to global volumes; per-task Docker environment
  values override global values. Daytona `always_on` environments are not reaped.
- Prompt idempotency is checked before a duplicate can mutate/queue another turn,
  and the exact first success acknowledgement is replayed. Failed submissions are
  never cached.
- Wheelbase prompt/session injection remains present on direct, background, and
  preview paths, with cleanup in `finally` blocks.
- Browser CDP discovery remains fail-closed, the frontend-less SPA guard remains,
  identity remains attached in `tui_gateway/ws.py`, and the Telegram General-topic
  reply fallback remains intact.

At the same time, retain these upstream additions:

- title provenance (`derived < llm < user`), instant titles, and turn-prologue
  title generation;
- hidden sessions/Bot Mode and hidden-session RPCs;
- bounded/incremental resume, row IDs, safe database-handle ownership, and
  context-manager cleanup;
- archive-preserving, durable row-ID truncation;
- per-session non-persistent Docker isolation and delegate container aliases;
- terminal heredoc hardening, error redaction, and graceful remote failures;
- `setup_mcp` lifecycle events and the rest of upstream's new TUI/gateway work.

## 3. Preparation and reproducible merge

The nested repository is currently detached at the Wheelbase head. It also has
an existing untracked `bun.lock`; this plan is a second intentional untracked
file until it is staged with the merge. The umbrella repository has an
unrelated, already-staged `wheelbase-mobile` pointer update. Preserve those
pre-existing changes; never use `git add -A`, never stash, and do not include
`bun.lock` or `wheelbase-mobile` in this merge.

From `wheelbase-agent/`:

```bash
git status --short --branch
git switch -c merge/hermes-main-2026-08-18 origin/wheelbase
git branch backup/wheelbase-pre-hermes-2026-08-18 923a24a798976dc98d0cb34474124549d931677b

git remote add upstream https://github.com/NousResearch/hermes-agent.git
git fetch --no-tags upstream main

test "$(git rev-parse HEAD)" = "923a24a798976dc98d0cb34474124549d931677b"
test "$(git rev-parse upstream/main)" = "8911e2e0edf750b104edbdc106d63d6cdac88524"
test "$(git merge-base HEAD upstream/main)" = "6564f319a647b47de391cab2f608660323804a2b"
git rev-list --left-right --count HEAD...upstream/main
git merge-tree --write-tree HEAD upstream/main
```

If an `upstream` remote has been added by the time this is executed, verify its
URL instead of adding it again. The existing local `refs/remotes/upstream/main`
does not currently have a corresponding configured remote.

The untracked `bun.lock` does not collide with the pinned upstream tree and may
remain in place, but it must remain untracked. Push the backup branch before
changing `wheelbase` if a remote rollback point is required.

Start the real merge without committing:

```bash
git merge --no-ff --no-commit 8911e2e0edf750b104edbdc106d63d6cdac88524
git diff --name-only --diff-filter=U
```

The unresolved list must be exactly the seven files above. A different list
means the inputs changed; abort and regenerate the preflight.

## 4. Conflict resolution, file by file

### 4.1 `agent/title_generator.py`

Use upstream's implementation and resolve the single docstring conflict to
describe the merged policy accurately.

Implementation:

1. Keep upstream's `_persist_session_title(..., source, dedupe=True)` and its
   call to `set_auto_title`. This is the authority-aware write that prevents a
   late automatic title from replacing a user rename.
2. Keep the legacy fallback order: `set_auto_title`, then
   `set_auto_title_if_empty`, then `set_session_title` for older store adapters.
3. Keep instant `derived` titles, asynchronous `llm` upgrades, the
   answer-shaped output rejection, runtime validation, and two-argument
   `title_callback(title, source)` contract.
4. Rewrite the conflicted prose so it says that duplicate titles are legal in
   Wheelbase schema v26. A `ValueError` collision/retry path exists only for a
   legacy or read-only store that still enforces uniqueness; it is not normal
   Wheelbase behavior.
5. Preserve `dedupe=False` for the inline derived title. The normal Wheelbase
   store will persist duplicate derived titles directly; no widening `#N` scan
   should run on the turn's critical path.
6. Do not reintroduce a unique-index assumption in comments or tests.

Acceptance tests:

- Two unrelated sessions can persist the same derived or LLM title unchanged.
- `derived` upgrades once to `llm`; neither can overwrite `user`.
- A manual rename racing the background title wins.
- An answer-shaped title is rejected and can be retried on the next eligible
  exchange.
- A mock legacy store that raises a collision `ValueError` still exercises the
  `#N` fallback; the real `SessionDB` does not.

### 4.2 `hermes_state.py`

Use upstream's provenance and lifecycle implementation, then restore
Wheelbase's non-unique-title and per-user-list policies.

Title hunk:

1. Keep upstream's read of both `title` and `title_source`, rank comparison, and
   null-safe compare-and-swap update:

   ```sql
   UPDATE sessions
      SET title = ?, title_source = ?
    WHERE id = ? AND title IS ? AND title_source IS ?
   ```

2. Delete the entire upstream uniqueness lookup, its `ValueError` for unrelated
   sessions, and the compression-ancestor title-clearing transfer.
3. Delete Wheelbase's now-stale `predicate = ... only_if_empty` line. The merged
   function no longer has `only_if_empty`; precedence plus the exact-value CAS
   is the concurrency guard.
4. When clearing a title, write `title_source = NULL`. For a non-empty write,
   persist the supplied source.
5. Keep `set_session_title` as authoritative `user` provenance and
   `set_auto_title` restricted to `derived`/`llm`.
6. Merge the shim docstrings: `set_auto_title_if_empty` remains available for
   old callers, now means “write an LLM title only if no equal/higher-authority
   title exists,” and does not enforce uniqueness.

Session listing/count integration:

1. Preserve Wheelbase's `user_id` argument and exact-owner predicate on
   `list_sessions_rich` and `session_count`. Supplying a user must not include
   legacy `NULL user_id` rows.
2. Preserve upstream's `include_hidden` argument on `list_sessions_rich`.
3. Add a matching `include_hidden: bool = False` argument to `session_count`.
   Unless true, add `s.hidden = 0` so `total` and `has_more` match the actual
   page. This is a semantic follow-up required because upstream added hidden
   filtering only to the list function while Wheelbase added list totals.
4. Keep deterministic duplicate-title lookup: exact title, newest
   `started_at`, then newest `rowid`.

Lifecycle and schema behavior:

1. Retain upstream's `SessionDB.__enter__`, `__exit__`, close ownership, resume
   guard, repair, pruning, and compression changes that merge automatically.
2. Inspect the auto-merged `hermes_state_common.py` and
   `hermes_state_schema.py`. The synthetic merge currently keeps
   `SCHEMA_VERSION = 26`, adds upstream's `title_source`, `hidden`,
   `git_metadata_generation`, and new tables, and retains Wheelbase's always-run
   `DROP INDEX idx_sessions_title_unique` plus non-unique `idx_sessions_title`.
3. Do not bump the schema version merely because both branches used v26. The
   upstream additions are reconciled from `SCHEMA_SQL` on every writable open.
   Prove that with a real Wheelbase-v26 migration test instead.
4. Never retain both the upstream unique-index block and Wheelbase's non-unique
   block. The final tree must contain no code that creates
   `idx_sessions_title_unique`.

Acceptance tests:

- Open a populated Wheelbase-v26 fixture containing duplicate titles. Verify
  upstream's new columns/tables are added, both titles survive, the unique index
  is absent, and the plain title index exists.
- Duplicate user and automatic titles remain valid; a compression child does
  not clear its ancestor's title.
- Provenance precedence and a concurrent CAS race behave correctly.
- `list_sessions_rich(user_id=..., include_hidden=...)` and
  `session_count(user_id=..., include_hidden=...)` return matching scopes.
- Context-manager exit closes owned handles, including exception paths.

### 4.3 `tests/agent/test_credential_pool_routing.py`

This is a test-fixture conflict; combine both isolation strategies rather than
choosing one side.

Implementation in `TestFailureAttribution._make_pool`:

1. Create `hermes_home`, set `HERMES_HOME` once, and write the supplied entries
   to its `auth.json` exactly as upstream does.
2. Delete `ANTHROPIC_API_KEY`, `ANTHROPIC_TOKEN`, and
   `CLAUDE_CODE_OAUTH_TOKEN` from the fixture environment.
3. Patch `hermes_cli.auth.is_provider_explicitly_configured` to return `False`.
4. Also retain Wheelbase's direct patches of
   `agent.anthropic_adapter.read_claude_code_credentials` and
   `read_hermes_oauth_credentials` to return `None`. Those readers can inspect
   host Claude credentials independently of `HERMES_HOME`.
5. Keep upstream's exact post-load ID assertion. The fixture must fail loudly if
   any host credential leaks into the pool.
6. Remove the duplicate early `HERMES_HOME` assignment from the conflict block.

Acceptance test: the whole file passes on a machine that has real Anthropic env
variables, Hermes auth, and `~/.claude` credentials, while the constructed pool
contains exactly the requested IDs.

### 4.4 `tools/terminal_tool.py`

Resolve around upstream's new helper/alias structure, then layer the Wheelbase
stable sandbox and container-config extensions into those helpers.

Container key hunk:

1. Keep upstream's `_container_aliases`, cycle-safe
   `_resolve_container_alias`, `_docker_session_isolation_enabled`,
   `_has_isolation_overrides`, and alias cleanup.
2. Move Wheelbase's override key set into the module-level
   `_ISOLATION_OVERRIDE_KEYS`. It must include the upstream backend/image keys
   and Wheelbase's `docker_volumes`. A bare `cwd` remains non-isolating.
3. Resolve `_resolve_container_task_id` in this exact priority order:

   ```python
   if task_id and task_id in _task_env_overrides:
       sandbox_key = _task_env_overrides[task_id].get("sandbox_key")
       if sandbox_key:
           return str(sandbox_key)
   if task_id and _has_isolation_overrides(task_id):
       return task_id
   if task_id and _docker_session_isolation_enabled():
       return _resolve_container_alias(task_id)
   return "default"
   ```

   Stable authenticated-user sandbox reuse therefore wins; explicit benchmark
   isolation is next; ordinary non-persistent Docker sessions use upstream's
   per-session/parent-alias behavior; persistent/default sessions still share.
4. Keep Wheelbase's merge-not-replace implementation of
   `register_task_env_overrides` and upstream's `ensure_cwd` refresh.

Environment-creation hunk:

1. Keep upstream's `_ssh_config_from_config` and
   `_container_config_from_config` to avoid two diverging construction paths.
2. Extend `_container_config_from_config` with an optional `overrides` mapping.
   It must append `overrides["docker_volumes"]` to global volumes and merge
   Docker env as `{**global_env, **task_env}`.
3. Include Wheelbase's `daytona_always_on` in the returned config, while keeping
   every new upstream Docker setting (`run_as_host_user`, extra args, shm size,
   network, cross-process persistence, orphan reaper, workspace mounts).
4. Pass the resolved per-task overrides through this helper at every creation
   call site, including the lazy `ensure_task_env` path and the normal terminal
   get-or-create path. Do not leave one path using globals only.
5. Use upstream's `_CONTAINER_BACKENDS` constant instead of restoring an inline
   backend set.
6. Retain upstream's per-session workspace mount behavior, delegate aliases,
   heredoc guard, output/error redaction, signal handling, and degraded remote
   failure responses.
7. Retain Wheelbase's always-on reaper exemption and desktop-exec assumptions.

Acceptance tests:

- Two turns for one authenticated user with different task IDs resolve to one
  `sandbox_key`; two users never share a key.
- A benchmark image/volume override without a sandbox key gets its own task ID.
- Non-persistent Docker sessions get separate containers; a delegate child
  resolves to its parent's alias; persistent Docker keeps the shared contract.
- Per-task volumes append and per-task env values win in both creation paths.
- Daytona `always_on` reaches the environment and survives the idle reaper.
- Terminal redaction, heredoc, Docker isolation, and Wheelbase desktop-exec relay
  suites all pass.

### 4.5 `tui_gateway/methods_prompt.py`

The five conflicts combine Wheelbase's idempotency/profile writes with
upstream's durable truncation protocol.

Submit setup hunk:

1. Parse and replay `idempotency_key` immediately after session lookup/active
   slot validation and before `client_surface`, transport, history, queue, or
   running-state mutation. A replay returns the original result payload.
2. After the replay check, adopt upstream's `client_surface` refresh.
3. Compute `has_truncation` from all three address forms:
   `truncate_before_user_ordinal`, `truncate_before_row_id`, and
   `truncate_before_message_id`.
4. Re-expand a skill invocation for any of those truncation forms, not just the
   legacy ordinal form.
5. Preserve Wheelbase's accepted-turn transport behavior: a competing device
   must not steal async event delivery before its submit actually owns a turn.
6. Keep `_remember_prompt_idempotency` on the busy/queued acknowledgement path
   so a duplicate key cannot enqueue the same follow-up twice.

Durable truncation hunk:

1. Resolve the store as
   `agent._session_db` first and `_get_db()` only as a local fallback. Name it
   `_edit_db` consistently; do not accidentally use upstream's process-global
   `db` later in the block.
2. Resolve the durable key as `session.get("session_key") or sid`.
3. Call:

   ```python
   _edit_db.replace_messages(
       truncation_key,
       truncated,
       active_only=True,
       archive_dropped=True,
   )
   ```

4. Preserve write-before-memory and fail closed. Only after the database write
   succeeds may `session["history"]` and `history_version` change.
5. Compute `survivor_user_row_ids` from the rewritten messages only when
   `_edit_db` exists. The replace call stamps fresh row IDs into the surviving
   dictionaries; these IDs are what the client must cache for a second rewind.

Response hunks:

1. On compute-host success, add `survivor_user_row_ids` to the success result
   first, then remember that complete response under the idempotency key, then
   return it. A replay must include the same row-ID rebind payload.
2. On the inline path, build one response containing `status: streaming` and the
   optional survivor IDs. Remember that exact response before starting the run
   thread, then start the thread and return the same object.
3. Never remember an error envelope. A failed submit remains retryable.

Registration hunk:

1. Publish `_PROMPT_IDEMPOTENCY_TTL_SECS` and
   `_PROMPT_IDEMPOTENCY_MAX_KEYS` onto `server` before rebinding helpers.
2. Rebind the union of both helper sets into `vars(server)`:

   - `_history_user_indices`
   - `_message_row_id`
   - `_mem_db_pair_agrees`
   - `_find_user_turn_by_row_id`
   - `_load_durable_truncation_history`
   - `_resolve_truncate_row_id`
   - `_coerce_truncate_int`
   - `_reconcile_client_ordinal`
   - `_pending_reaction_notes`
   - `_prompt_idempotency_replay`
   - `_remember_prompt_idempotency`

3. Use one `types.FunctionType` loop and do not duplicate helper names.

Acceptance tests:

- Same-key duplicates replay on direct, busy/queued, and isolated compute-host
  paths and produce exactly one turn/queue item.
- Different keys with identical text remain distinct turns; keyless upstream
  clients remain unchanged; failed responses are not cached.
- Ordinal, row-ID, and message-ID truncation all re-expand skills.
- Two consecutive rewinds use the returned fresh survivor IDs.
- A profile-owned session writes only to its agent/profile DB and preserves
  compacted/archive rows.
- Direct and compute-host acknowledgements, including idempotent replays, carry
  identical survivor-ID fields.
- Wheelbase injection/cleanup still passes for direct, background, and preview
  execution.

### 4.6 `tui_gateway/methods_session.py`

Use upstream's current `session.resume` as the structural base because its outer
`try/finally`, hydration worker, resume-size guard, and handle transfers are
interdependent. Reapply Wheelbase identity/profile and durable-ID policy inside
that structure. The nine textual hunks map to the following logical changes.

#### Hunk 1: session list pagination plus hidden sessions

1. Keep `limit` and Wheelbase's `offset`.
2. Add upstream's `include_hidden` flag.
3. Pass all of `offset`, `user_id`, and `include_hidden` to
   `list_sessions_rich`.
4. Pass `user_id` and `include_hidden` to the matching `session_count` call.
5. Keep Wheelbase's `total`, `has_more`, and `lineage_root_id` response fields.
   Hidden rows must not inflate a default page's total.

#### Hunks 2–4: profile selection, lookup, ownership, tip, and size guard

1. Keep `_request_profile(params)`; do not restore raw client-controlled
   `params.profile` selection. Resolve `profile_home` from that validated result.
2. Keep upstream's `owns_db` flag and outer `try/finally`. A profile DB is a
   dedicated handle; the launch DB is process-owned and must not be closed here.
3. Read `resume_ident = _transport_identity()` before row fallback decisions.
4. Look up the requested ID directly. Only when `resume_ident is None` may the
   legacy local path fall back to `get_session_by_title`. For an identified
   connection, never perform title fallback; return generic code 4007 “session
   unavailable” for an absent or foreign row.
5. Ownership-gate the requested durable row before compression-tip resolution.
   Identified users cannot resume a foreign or `NULL`-owner row, and the error
   must not reveal whether it exists.
6. Resolve a compression tip only after that gate. Adopt it only if the tip row
   exists and has the same owner; otherwise continue with the already-authorized
   requested row. Lazy child-watch attaches to the exact child and skips tip
   projection as upstream intends.
7. Run upstream's `assert_resume_safe(target)` after the accepted tip is known.
   Keep the metadata fallback, 4130 over-limit response, and fail-open logging
   for transient guard errors. The safety check must never bypass ownership.

#### Hunk 5: live reuse, lazy watch, and incremental hydration

1. Keep upstream's `defer_history` response behavior, hydration markers,
   `include_row_ids=True`, verbatim display projection, and child liveness.
2. Preserve Wheelbase's durable runtime ID: use `sid = target` in lazy,
   defer-history, normal deferred, and eager branches instead of a new eight-char
   UUID.
3. For every `_deferred_session_record`, set
   `record["wheelbase_identity"] = resume_ident` before claiming it. The current
   fork only stamps this on the eager path; the new hydration path makes the
   invariant explicit for all branches.
4. Keep `profile_home`, stored model/provider/reasoning/tier overrides,
   `display_history_prefix`, and active-session lease on every record.
5. In the `defer_history` branch, call
   `_schedule_resume_hydration(..., close_db=owns_db)`, then set
   `owns_db = False` when it was true. The outer finalizer cannot race/close the
   transferred dedicated handle; the worker closes it after reading. A shared
   launch DB is passed with `close_db=False` and remains process-owned.
6. In lazy/normal deferred returns, let the outer finalizer close the temporary
   profile DB. Deferred agent build must reopen/use the profile through the
   stored `profile_home` rather than retaining an abandoned handle.

#### Hunks 6–8: cold/eager build, context cleanup, and DB transfer

1. Keep the default cold-resume branch and upstream's off-response-path build;
   add the new defer-history branch before it. Preserve durable `sid = target`.
2. Keep upstream's `include_row_ids=True` reads, replay sanitization, display
   prefix, auto-continue, and double-checked live-session locking.
3. Around eager `_make_agent`, keep all three context mechanisms:
   profile `HERMES_HOME`, profile secret scope, and `_set_session_context(target)`.
   Their matching reset/`_clear_session_context(tokens)` calls must all execute
   in nested `finally` blocks.
4. After `_init_session`, call `_transfer_db_to_agent(agent, db)` for a dedicated
   (`owns_db`) handle and set `owns_db = False` only after registration. Do not
   mark the shared launch DB as agent-owned. This prevents both a live closed
   handle and a permanent descriptor/token-writer leak.
5. On partial `_init_session` failure while `owns_db` is still true, remove the
   half-built `_sessions[sid]`, release the lease, and let the outer finalizer
   close the handle.
6. After successful registration, preserve all Wheelbase metadata:
   `wheelbase_identity`, `profile_home`, `active_session_lease`, model override,
   and display history prefix.
7. The outer `finally` closes only a still-owned dedicated DB; it never closes
   the shared launch DB or a handle transferred to hydration/agent ownership.

#### Hunk 9: workspace move

1. Keep Wheelbase's live-session identity comparison before selecting a runtime
   to re-home. A guessed session key must never move or emit information about a
   different user's live session.
2. Keep the `running`/busy refusal that upstream's side dropped. Do not change a
   live tool's CWD mid-turn.
3. Keep workspace path containment for remote identities and upstream's
   generation-safe git metadata update behavior from the auto-merged state code.
4. Apply the same per-profile persisted-row ownership check before writing the
   new CWD.

Acceptance tests:

- Default and hidden lists paginate with correct per-user totals.
- Explicit cross-profile requests are rejected; foreign IDs/titles and
  `NULL`-owner rows all return the same unavailable response.
- A same-owner compression parent resumes its tip; a foreign/`NULL` tip is never
  substituted.
- Resume limits apply to eager, deferred, omitted-message, and incremental
  hydration paths.
- Repeated/live, lazy, deferred, hydration, eager-success, eager-failure, and
  not-found paths each close or transfer exactly one dedicated DB handle.
- Deferred and eager sessions retain the current transport identity and profile
  secret/home scope on later turns.
- Runtime session IDs remain durable keys.
- Foreign workspace moves are invisible; busy sessions refuse moves; valid
  contained moves update both live and durable state.

### 4.7 `tui_gateway/server.py`

Tool lifecycle hunk:

1. Resolve `_tool_lifecycle_required_for_ui` to the union:

   ```python
   return name in {"clarify", "todo", "setup_mcp"}
   ```

2. Preserve the comments explaining that these tool events carry interactive
   UI state and therefore cannot be suppressed with ordinary progress chrome.

Auto-title hunk:

1. Take upstream's deletion of the old post-turn `maybe_auto_title` block. Title
   generation now runs once in `agent.turn_context` at turn start; retaining the
   old block would launch a second title attempt after every completed turn.
2. Keep the profile-aware `pending_title` finalizer immediately above it. Manual
   titles still write through `_session_db(session)`, not the launch store.
3. Keep upstream's pre-`run_conversation` assignment of
   `agent._on_session_title = lambda title, source: ...`. This preserves the live
   `session.title` event with the new two-argument callback contract.
4. Update stale nearby comments that call every `ValueError` a duplicate-title
   error. In Wheelbase, duplicate titles are valid; the guarded path now covers
   validation/legacy-store errors.
5. Preserve all auto-merged Wheelbase identity, profile DB, credential refresh,
   and three-path injection/cleanup code.

Acceptance tests:

- With tool progress off, lifecycle events still fire for `clarify`, `todo`, and
  `setup_mcp`, but ordinary tool chrome remains suppressed.
- One turn triggers one prologue title pipeline, not a second post-turn call.
- The instant and LLM title callbacks emit live sidebar updates with the durable
  session key.
- Manual pending titles persist to the session's profile DB.
- Direct, background, and preview injection cleanup tests remain green.

## 5. Audit the 17 auto-merged overlap files

An automatic textual merge is not a semantic approval. Review all 17 files in
the synthetic/real merge diff against both parents:

- `agent/prompt_builder.py`
- `agent/system_prompt.py`
- `cli.py`
- `gateway/run.py`
- `hermes_cli/profiles.py`
- `hermes_cli/web_routers/cron.py`
- `hermes_cli/web_server.py`
- `hermes_state_common.py`
- `hermes_state_schema.py`
- `plugins/platforms/telegram/adapter.py`
- `scripts/run_tests.sh`
- `tests/agent/test_anthropic_output_field_leak.py`
- `tests/gateway/test_api_server.py`
- `tests/test_hermes_state.py`
- `tests/test_tui_gateway_server.py`
- `tools/browser_tool.py`
- `tui_gateway/methods_tools.py`

Required semantic checks:

- `WHEELBASE_CANVAS_PROTOCOL_HINT` is still injected only on the intended
  Wheelbase tool surface and remains in the stable prompt tier.
- `/api/cron/channels` still exists, and `mount_spa` accepts a missing
  `web_dist/assets` directory for `--skip-build` profile children.
- Browser task CDP lookup remains per-task and fail-closed; query strings and the
  bundled CLI override survive.
- Telegram General-topic replies still carry/consume the fallback reply anchor.
- The merged schema contains upstream's new columns/tables and Wheelbase's
  non-unique title index policy, with no unique-title creation path.
- Upstream's reset-child/session-hidden predicates and Wheelbase's `user_id`
  predicates both survive in state listing/count code.
- The canonical test runner still provides per-file process isolation and clean
  credential environment behavior.
- No deleted Wheelbase dead-weight subsystem from `docs/wheelbase-fork.md` §5 is
  resurrected.

Also spot-check Wheelbase-only load-bearing files even though they cannot
textually conflict: `tui_gateway/ws.py`, `tui_gateway/profile_router.py`,
`tui_gateway/wheelbase_inject.py`, `tools/environments/daytona.py`, and the
desktop-exec relay plugin.

## 6. Resolution and test gates

Resolve/stage the seven paths and this plan explicitly. Do not stage `bun.lock`:

```bash
git add agent/title_generator.py
git add hermes_state.py
git add tests/agent/test_credential_pool_routing.py
git add tools/terminal_tool.py
git add tui_gateway/methods_prompt.py
git add tui_gateway/methods_session.py
git add tui_gateway/server.py
git add docs/upstream-merge-plan-2026-08-18.md

test -z "$(git diff --name-only --diff-filter=U)"
git diff --cached --check
git status --short
```

Search changed source files for conflict markers before testing. Avoid a
repository-wide `=======` search because generated assets and documentation can
legitimately contain divider lines.

Run targeted tests through the canonical runner, never raw `pytest`:

```bash
scripts/run_tests.sh -j 4 \
  tests/agent/test_title_generator.py \
  tests/agent/test_turn_context.py \
  tests/agent/test_credential_pool_routing.py \
  tests/test_hermes_state.py \
  tests/hermes_state/test_session_hidden.py \
  tests/hermes_state/test_replace_messages_archive_siblings.py \
  tests/test_session_db_context_manager.py \
  tests/state/test_session_git_metadata_generation.py \
  tests/state/test_session_turn_lease.py \
  tests/tools/test_terminal_tool.py \
  tests/tools/test_docker_session_isolation.py \
  tests/tools/test_terminal_error_redaction.py \
  tests/tools/test_terminal_heredoc_background_guard.py \
  tests/test_tui_gateway_prompt_idempotency.py \
  tests/test_tui_gateway_queue_on_busy.py \
  tests/test_tui_gateway_server.py \
  tests/tui_gateway/test_protocol.py \
  tests/tui_gateway/test_session_hidden_rpc.py \
  tests/tui_gateway/test_session_resume_db_ownership.py \
  tests/tui_gateway/test_compute_host.py \
  tests/tui_gateway/test_compute_host_phase1.py \
  tests/test_wheelbase_identity.py \
  tests/test_wheelbase_multiuser.py \
  tests/test_wheelbase_inject.py \
  tests/gateway/test_title_command.py \
  tests/gateway/test_api_server.py \
  tests/plugins/wheelbase_desktop_exec/
```

Add/extend tests described in each conflict section before considering this
gate complete. Then run the full suite:

```bash
scripts/run_tests.sh
```

Build the actual deployment image, not upstream's root development image:

```bash
docker build \
  --file Dockerfile.gateway \
  --tag wheelbase-agent:hermes-8911e2e0 \
  .
```

Only after targeted tests, the full suite, and the gateway image build pass,
create the merge commit:

```bash
git commit -m "Merge upstream/main (hermes 8911e2e0e) into wheelbase — 2814-commit catch-up"
git merge-base --is-ancestor 8911e2e0edf750b104edbdc106d63d6cdac88524 HEAD
git rev-list --left-right --count HEAD...upstream/main
```

The final count must show zero commits behind the pinned upstream target. Put
test-only reconciliation or genuinely separate hardening in follow-up commits;
do not amend unrelated Wheelbase changes into the merge resolution.

## 7. Runtime validation and rollout

Run the candidate first against a snapshot/clone of a production-like
`/data/hermes` volume. Opening `state.db` is forward-mutating; an image rollback
is not a guaranteed data rollback.

Minimum non-production proofs:

1. Start `Dockerfile.gateway` with the private-network topology from
   `docs/cloud-gateway.md`; verify the profile router and both optional secondary
   processes obey their failure-isolation contract.
2. Connect as two synthetic users. Create, list, resume, compress/resume, hide,
   and move a workspace. Each user must see only their own rows and profile.
   Foreign IDs and explicit foreign profiles must fail closed.
3. Send the same Wheelbase queue item twice on direct, busy, and isolated paths.
   Observe one model turn and an identical replayed acknowledgement.
4. Perform two consecutive transcript rewinds and verify fresh survivor row IDs,
   preserved compacted archives, and correct profile DB writes.
5. Create two conversations with the same title, then exercise
   derived→LLM→manual precedence and a compression continuation. No suffix or
   ancestor-title theft is allowed.
6. Verify default session lists hide Bot/hidden rows and the owning hidden-session
   surface can include them without changing another user's totals.
7. Verify one user's Daytona sandbox persists across turns, another user gets a
   different sandbox, and a desktop-exec/browser relay never falls back to a
   host/global target on resolution failure.
8. Verify `/api/cron/channels`, per-profile cron tick, frontend-less profile
   child startup, and Telegram General-topic media replies.
9. Exercise graceful close/reconnect and watch database descriptors, token-writer
   threads, `database is locked` logs, child processes, and sandbox reaping.

Deploy to one tenant first and observe for at least 24 hours before broad
rollout. Watch gateway restarts, profile-child churn, SQLite lock/closed-handle
errors, title churn, duplicate turns, cross-profile denials, sandbox counts,
cron delivery, browser/desktop relay failures, and token/cost accounting.

## 8. Publishing and umbrella pointer

Push the candidate branch first. Update `origin/wheelbase` only if it has not
advanced; a rejected non-fast-forward push requires redoing the preflight.

After the nested merge is accepted, update the umbrella gitlink in a separate
root commit. The root currently has an unrelated staged `wheelbase-mobile`
change, so use an `--only` path commit:

```bash
cd /home/admin/projects/wheelbase-monorepo
git add wheelbase-agent
git diff --cached --submodule=short -- wheelbase-agent
git commit --only wheelbase-agent -m "chore: bump wheelbase-agent after Hermes upstream merge"
```

Verify that `wheelbase-mobile` remains staged and uncommitted exactly as it was.
Do not use `git add -A` or a root commit without `--only`.

## 9. Rollback

- Before the merge commit: `git merge --abort` returns the nested repository to
  the pre-merge branch. The backup branch is the durable reference.
- After the merge commit but before deployment: reset no shared branch. Drop the
  candidate branch or revert the merge on a new branch.
- After publishing: use `git revert -m 1 <merge-commit>` and ship the resulting
  image. Do not rewrite `wheelbase` history.
- After a canary deployment: pin the previous known-good image immediately.
  Restore the pre-canary database/volume snapshot only if data-level rollback is
  required; do not manually decrement `schema_version` or remove columns.
- Revert the umbrella gitlink separately if it was published.

## 10. Definition of done

The merge is complete only when:

- the pinned upstream SHA is an ancestor and the branch is zero commits behind;
- all seven conflicts follow the recipes above and no conflict markers remain;
- all 24 overlapping files have received semantic review;
- duplicate titles, per-user lists/counts, profile ownership, stable sandbox
  keys, prompt idempotency, and three-path injection have explicit passing tests;
- targeted and full canonical test suites pass;
- `Dockerfile.gateway` builds and the production-like runtime matrix passes;
- one-tenant canary observation completes without a stop condition;
- the nested merge is published before the umbrella pointer, and the unrelated
  `wheelbase-mobile`/`bun.lock` state is untouched;
- `docs/wheelbase-fork.md` is refreshed with the new baseline, conflict map, and
  any newly permanent upstream-owned edits discovered during implementation.
