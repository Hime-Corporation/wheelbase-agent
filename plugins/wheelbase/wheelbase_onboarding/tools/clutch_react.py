"""clutch_react — trigger a Clutch mascot reaction in the onboarding UI.

Handler contract:
  - signature `def fn(args: dict, **kwargs) -> str`
  - validate args → err(...) on bad input
  - ALWAYS return a JSON string; NEVER raise (catch → err(...))
  - No WheelbaseClient needed — this tool is purely UI signalling.

Ported verbatim from legacy-plugins/openclaw-onboarding/src/tools.ts:
createClutchReactTool() handler logic.
"""

from wheelbase_sdk import err, ok

_MASCOT_STATES = frozenset([
    "idle",
    "speed",
    "cry",
    "code",
    "nitro",
    "alert",
    "repair",
    "champ",
    "finish",
    "think",
    "greeting",
    "loading",
    "boost",
    "tune",
])

_DEFAULT_TTL_MS = 4000
_MAX_TTL_MS = 15000
_MAX_SPEECH_LENGTH = 140
_MAX_TIP_LENGTH = 240


def clutch_react(args: dict, **kwargs) -> str:
    """Trigger a Clutch mascot reaction in the onboarding UI."""
    try:
        params = args or {}

        # --- state ---
        raw_state = params.get("state")
        if not isinstance(raw_state, str) or not raw_state:
            return err("state is required and must be a non-empty string")
        state = raw_state if raw_state in _MASCOT_STATES else "idle"

        # --- speech (optional) ---
        speech_raw = params.get("speech")
        speech = None
        if isinstance(speech_raw, str) and speech_raw:
            speech = speech_raw[:_MAX_SPEECH_LENGTH]

        # --- tip (optional) ---
        tip_raw = params.get("tip")
        tip = None
        if isinstance(tip_raw, str) and tip_raw:
            tip = tip_raw[:_MAX_TIP_LENGTH]

        # --- ttlMs (optional, integer semantics) ---
        ttl_raw = params.get("ttlMs")
        if isinstance(ttl_raw, (int, float)) and ttl_raw == ttl_raw:  # not NaN
            ttl_ms = max(0, min(int(ttl_raw), _MAX_TTL_MS))
        else:
            ttl_ms = _DEFAULT_TTL_MS

        payload: dict = {"kind": "clutch_react", "state": state, "ttlMs": ttl_ms}
        if speech is not None:
            payload["speech"] = speech
        if tip is not None:
            payload["tip"] = tip

        return ok(payload)
    except Exception as exc:  # noqa: BLE001 — tools must never raise
        return err(f"clutch_react failed: {exc}")
