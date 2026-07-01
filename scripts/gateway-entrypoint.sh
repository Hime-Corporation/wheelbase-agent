#!/bin/sh
# Gateway container entrypoint — runs up to THREE Hermes processes side by side:
#
#   1. tui_gateway.profile_router  — the per-user dashboard router. This is
#      the existing, load-bearing live-chat path (backend -> :9320 -> per-
#      user child dashboards). It is the PRIMARY process for this container.
#   2. gateway.run                 — the OpenAI-compatible API server
#      platform (POST /v1/responses on :8642), used by the Go backend for
#      one-shot "AI inspection review" calls. This is a SECONDARY process.
#   3. gateway.run (Telegram)      — the @hermesauto_bot messaging gateway,
#      the shared "main" Wheelbase agent on Telegram. Only started when
#      WB_TELEGRAM_BOT_TOKEN is set. Also a SECONDARY process.
#
# Failure-isolation contract (important — do not "simplify" this away):
#   - If a SECONDARY gateway.run process (API server OR Telegram) crashes or
#     misbehaves, profile_router (and therefore live chat) MUST keep running.
#     Each secondary loop below is started in its own backgrounded subshell
#     with its own retry loop, so a crash inside it can never propagate to,
#     or take down, this script or profile_router.
#   - If profile_router dies, the container MUST exit so the orchestrator
#     (Dokploy/docker) restarts it — that's the critical path. We capture
#     profile_router's PID, `wait` on that specific PID, and exit this
#     script with its exact exit status.
#
# Per-process secret scoping (important — do not move these to global env):
#   gateway.run auto-ENABLES a messaging platform whenever its trigger env
#   var is present (TELEGRAM_BOT_TOKEN -> Telegram auto-connects). Both the
#   API-server process AND the Telegram process are gateway.run, so a
#   GLOBAL TELEGRAM_BOT_TOKEN would make BOTH try to poll the same bot ->
#   Telegram 409 Conflict. We therefore inject the token into ONLY the
#   Telegram subshell (mapping WB_TELEGRAM_BOT_TOKEN -> TELEGRAM_BOT_TOKEN
#   there), and `env -u` the inherited API_SERVER_* vars in that subshell so
#   it never enables a second api_server adapter and double-binds :8642
#   (config.py enables api_server on API_SERVER_KEY OR API_SERVER_ENABLED).
#
# POSIX sh only (the python:3.11-slim base image's /bin/sh is dash, no
# bash installed) — avoid bashisms such as `wait -n`, arrays, or [[ ]].

set -eu

# ---------------------------------------------------------------------------
# 1. API server (gateway.run) — own HERMES_HOME so its config.yaml/.env/
#    state never collide with profile_router's per-user profile directories
#    under /data/hermes/profiles/*. Both live under the same mounted volume.
# ---------------------------------------------------------------------------
API_SERVER_HERMES_HOME="${API_SERVER_HERMES_HOME:-/data/hermes/api-server}"
mkdir -p "$API_SERVER_HERMES_HOME"

if [ ! -f "$API_SERVER_HERMES_HOME/config.yaml" ]; then
  # Idempotent: only written on first boot. A human can hand-edit this file
  # afterwards and it will never be clobbered by a container restart.
  cat > "$API_SERVER_HERMES_HOME/config.yaml" <<'EOF'
model: minimax/minimax-m3
provider: openrouter
plugins:
  enabled:
    - wheelbase-inspection
platform_toolsets:
  api_server:
    - web
    - wheelbase_inspection
web:
  search_backend: ddgs
  extract_backend: firecrawl
EOF
fi

# Background retry loop for the API server. Runs in its own subshell ( ... ) &
# so a crash (or even `set -e` triggering inside the loop) cannot kill the
# parent script — only this subshell's own logic decides whether to retry.
(
  while true; do
    HERMES_HOME="$API_SERVER_HERMES_HOME" python -m gateway.run || true
    echo "[gateway-entrypoint] gateway.run (API server) exited — retrying in 3s" >&2
    sleep 3
  done
) &

# ---------------------------------------------------------------------------
# 1b. Telegram gateway (gateway.run) — the shared "main" Wheelbase agent on
#     Telegram (@hermesauto_bot). Own HERMES_HOME so its config.yaml/state
#     never collide with profile_router's per-user profiles or the API
#     server. No-op unless WB_TELEGRAM_BOT_TOKEN is set, so this block is
#     inert on any deployment that hasn't opted into Telegram.
# ---------------------------------------------------------------------------
if [ -n "${WB_TELEGRAM_BOT_TOKEN:-}" ]; then
  TELEGRAM_HERMES_HOME="${TELEGRAM_HERMES_HOME:-/data/hermes/telegram}"
  mkdir -p "$TELEGRAM_HERMES_HOME"

  if [ ! -f "$TELEGRAM_HERMES_HOME/config.yaml" ]; then
    # Idempotent: only written on first boot; hand-edits survive restarts.
    # Model/persona/plugins mirror the per-user profile (provision_profile)
    # except the model — owner runs this on their personal ChatGPT
    # subscription via the openai-codex OAuth provider (2026-07-01). Auth
    # lives in $TELEGRAM_HERMES_HOME/auth.json, set up separately via
    # `hermes auth add openai-codex`; this file only pins the routing.
    # Hard-pinned (no DeepSeek/OpenRouter fallback) — if the ChatGPT-plan
    # Codex quota is exhausted, turns error rather than falling back and
    # incurring per-token API spend. Covers ALL Telegram users of this bot
    # (owner DMs + the free_response_topics teammates below), not just the
    # owner — accepted knowingly by the owner (2026-07-01).
    cat > "$TELEGRAM_HERMES_HOME/config.yaml" <<'EOF'
model: gpt-5.5
provider: openai-codex
skin: wheelbase
delegation:
  # delegate_task subagents run on the cheap/fast Flash model instead of Pro.
  model: deepseek/deepseek-v4-flash
plugins:
  enabled:
    - wheelbase-core
    - wheelbase-onboarding
    - wheelbase-auction-browser
    - wheelbase-demand-matrix
    - wheelbase-inspection
    - wheelbase-dealercenter-import
auxiliary:
  # DeepSeek is text-only, so route image analysis (vision_analyze) to a
  # vision-capable model on the SAME OpenRouter key. Do NOT point this at
  # DeepSeek — inbound photos would error ("No LLM provider for task=vision").
  vision:
    provider: openrouter
    model: google/gemini-3-flash-preview
  # Keep the expensive per-turn side-forks OFF the main model: context
  # compression + the background self-improvement review run on cheap Flash,
  # pinned to the caching DeepSeek endpoint. (These were silently running on
  # the pricey main model before — part of the spend.)
  compression:
    provider: openrouter
    model: deepseek/deepseek-v4-flash
    extra_body:
      provider:
        only: ["DeepSeek"]
  background_review:
    provider: openrouter
    model: deepseek/deepseek-v4-flash
    extra_body:
      provider:
        only: ["DeepSeek"]
image_gen:
  # Text-to-image + image editing via the bundled OpenRouter backend plugin
  # (plugins/image_gen/openrouter, auto-loads; uses OPENROUTER_API_KEY already
  # in env). Gemini 3.1 Flash Image (~$0.07/image).
  provider: openrouter
  openrouter:
    model: google/gemini-3.1-flash-image
telegram:
  # Owner DMs the bot directly = free-flow (DMs never need an @mention).
  # Group "Hermes AI Integration" (-1004395037275): the bot stays silent
  # unless a member @mentions @hermesauto_bot or replies to it.
  require_mention: true
  exclusive_bot_mentions: true
tts:
  # Voice output via ElevenLabs (reads ELEVENLABS_API_KEY from env). Uses the
  # built-in defaults — Adam voice (pNInz6obpgDQGcFmaJgB) + eleven_multilingual_v2
  # — and is delivered as native Telegram voice bubbles (Opus, no ffmpeg needed).
  provider: elevenlabs
web:
  search_backend: ddgs
  extract_backend: firecrawl
gateway:
  streaming:
    # Progressive token-by-token replies via edit-based updates. `edit` (not
    # `auto`/`draft`) issues fewer Telegram API calls than rich draft frames,
    # which was tripping Telegram flood-control (38s stalls) under heavy
    # streaming. Works in both DMs and the group.
    enabled: true
    transport: edit
  platforms:
    telegram:
      extra:
        # Per-user topics chat the agent free-flow (no @mention needed); each
        # forum topic is its own isolated parallel session. The General topic
        # (thread 1) is NOT listed, so it stays mention-gated via
        # require_mention above. Simon=4, Yura=289, Mark=290, Igor=291.
        free_response_topics: ["4", "289", "290", "291"]
        # Native rendering for tables / task lists / <details> / block math
        # via sendRichMessage (Bot API 10.1). Transparently falls back to
        # MarkdownV2 if Telegram rejects the call or content exceeds the limit.
        rich_messages: true
        rich_drafts: false   # keep off: rich draft frames can overlay on desktop
EOF
  fi

  # Seed the shared "main" Wheelbase persona on first boot (matches
  # tui_gateway.profile_router.DEFAULT_SOUL).
  if [ ! -f "$TELEGRAM_HERMES_HOME/SOUL.md" ]; then
    cat > "$TELEGRAM_HERMES_HOME/SOUL.md" <<'EOF'
# Wheelbase Dealership Agent

You are the Wheelbase agent for a car dealership. Help dealership staff manage
inventory, source vehicles at auction, analyze market demand, and run daily
operations. Use Wheelbase tools whenever they apply. Be concise, accurate, and
concrete.
EOF
  fi

  # Background retry loop, failure-isolated like the API server above.
  # Secret scoping: TELEGRAM_BOT_TOKEN is injected HERE ONLY (never global).
  # We must UNSET the API-server trigger vars rather than just disable them:
  # config.py enables the api_server platform when API_SERVER_KEY *or*
  # API_SERVER_ENABLED is truthy, so the inherited API_SERVER_KEY would still
  # turn it on and collide with the real API-server process on :8642 (which in
  # turn aborts this process's startup before Telegram connects). `env -u`
  # strips them so Telegram is the only enabled platform here. Daytona is
  # dropped in favor of a local terminal backend.
  (
    while true; do
      env -u API_SERVER_KEY -u API_SERVER_ENABLED -u API_SERVER_PORT \
        HERMES_HOME="$TELEGRAM_HERMES_HOME" \
        TELEGRAM_BOT_TOKEN="$WB_TELEGRAM_BOT_TOKEN" \
        TERMINAL_ENV=local \
        python -m gateway.run || true
      echo "[gateway-entrypoint] gateway.run (Telegram) exited — retrying in 3s" >&2
      sleep 3
    done
  ) &
fi

# ---------------------------------------------------------------------------
# 2. profile_router — the primary/critical process. Started in the
#    foreground (backgrounded then waited on by PID, per POSIX-sh portable
#    pattern) so its exit status becomes this script's exit status and the
#    container dies with it, letting the orchestrator restart cleanly.
# ---------------------------------------------------------------------------
python -m tui_gateway.profile_router &
ROUTER_PID=$!

wait "$ROUTER_PID"
ROUTER_STATUS=$?

exit "$ROUTER_STATUS"
