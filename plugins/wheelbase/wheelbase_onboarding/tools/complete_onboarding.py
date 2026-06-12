"""complete_onboarding — signal that onboarding is complete.

Handler contract:
  - signature `def fn(args: dict, **kwargs) -> str`
  - ALWAYS return a JSON string; NEVER raise (catch → err(...))
  - No WheelbaseClient needed — this tool is purely a UI signal.

Ported verbatim from legacy-plugins/openclaw-onboarding/src/tools.ts:
createCompleteOnboardingTool() handler logic.
"""

from wheelbase_sdk import err, ok


def complete_onboarding(args: dict, **kwargs) -> str:  # noqa: ARG001
    """Signal that onboarding is complete."""
    try:
        return ok({"kind": "complete_onboarding"})
    except Exception as exc:  # noqa: BLE001 — tools must never raise
        return err(f"complete_onboarding failed: {exc}")
