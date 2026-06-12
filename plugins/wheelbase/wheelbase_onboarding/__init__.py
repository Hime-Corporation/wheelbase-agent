"""wheelbase-onboarding plugin — registration.

Tools (clutch_react, complete_onboarding) are gated on the onboarding marker
file: they are hidden from the model when onboarding mode is not active.

A pre_llm_call hook injects the onboarding system-prompt addendum plus
DEALERSHIP.md / TEAM.md context only when the marker is present.

Ported from legacy-plugins/openclaw-onboarding/src/{index,marker,build-context,
addendum,workspace-files}.ts.
"""

from __future__ import annotations

from wheelbase_sdk import workspace_dir

from . import schemas
from .tools import clutch_react as clutch_react_tool
from .tools import complete_onboarding as complete_onboarding_tool

# ---------------------------------------------------------------------------
# Mascot state list (mirrors schemas.py for the addendum string)
# ---------------------------------------------------------------------------
_MASCOT_STATES_STR = ", ".join(schemas.MASCOT_STATES)

# ---------------------------------------------------------------------------
# Onboarding interview prompt — verbatim from addendum.ts:ONBOARDING_INTERVIEW_PROMPT
# ---------------------------------------------------------------------------
_ONBOARDING_INTERVIEW_PROMPT = """# ONBOARDING_INTERVIEW Prompt

You are conducting Wheelbase AI onboarding. Your objective is to gather enough operational context to produce high-quality `USER.md`, `DEALERSHIP.md`, and `TEAM.md` documents.

## Interview Rules
1. Ask one focused question at a time.
2. Start with dealership basics, then team, then user preferences.
3. Keep questions short and specific.
4. If an answer is vague, ask one clarifying follow-up.
5. Do not ask for sensitive secrets.

## Completion Criteria
You may finish only when all are covered:
- Dealership location/market/vehicle mix/targets
- Team structure and ownership map
- User goals and workflow preferences

## Output Contract
At the end, produce:
1. Draft `DEALERSHIP.md`
2. Draft `TEAM.md`
3. Draft `USER.md`
4. Confidence notes and open questions

## Save Mode
- Onboarding mode: auto-save as active docs.
- Post-onboarding mode: save as draft and request approval.
"""

# ---------------------------------------------------------------------------
# Marker gating
# ---------------------------------------------------------------------------
_ONBOARDING_MARKER_FILENAME = ".wheelbase-onboarding-active"


def _marker_active() -> bool:
    """Return True iff the onboarding marker file exists in the agent workspace."""
    return (workspace_dir() / _ONBOARDING_MARKER_FILENAME).exists()


# ---------------------------------------------------------------------------
# Context injection helpers
# ---------------------------------------------------------------------------

def _read_workspace_file(name: str) -> str | None:
    """Read a file from the agent workspace; return stripped content or None."""
    path = workspace_dir() / name
    try:
        content = path.read_text(encoding="utf-8")
        return content.strip() if content.strip() else None
    except Exception:  # noqa: BLE001
        return None


def _build_onboarding_addendum() -> str:
    """Build the onboarding system-prompt addendum.

    Ported from addendum.ts:buildOnboardingAddendum (no per-user ctx variant —
    the hook has no access to display name / tenant at hook time).
    """
    lines = [
        "## Wheelbase Onboarding Mode",
        "You are Clutch, the Wheelbase onboarding concierge. The active session is a",
        "first-run conversation with a new dealership operator. Your job is to turn",
        "their answers into polished workspace documents and hand off cleanly.",
        "",
        _ONBOARDING_INTERVIEW_PROMPT.strip(),
        "",
        "## Workspace Files You Must Maintain",
        "Use the existing `write` and `edit` pi-tools to incrementally update:",
        "- `USER.md` — human preferences, working style, communication tone.",
        "- `DEALERSHIP.md` — business facts (location, franchises, inventory mix,",
        "  tooling, quick wins, targets).",
        "- `TEAM.md` — roster, ownership map, decision-makers, hand-off notes.",
        "- `IDENTITY.md` — the assistant's persona / name / voice as configured by",
        "  the operator.",
        "- `SOUL.md` — the assistant's tone, values, and conversational boundaries.",
        "Keep each file focused on its theme; never dump raw transcript into these",
        "files. Prefer short, structured markdown (headings, bullet lists).",
        "Write files incrementally as soon as you have enough to commit — do not",
        "wait for the whole interview to finish before calling `write`.",
        "",
        "## Clutch Mascot Reactions",
        "Call `clutch_react({ state, speech?, tip?, ttlMs? })` after significant user",
        "messages so the on-screen mascot stays responsive. Pick a `state` from:",
        f"  {_MASCOT_STATES_STR}.",
        "Use short, positive reinforcement or a concise useful tip. Rules:",
        "- At most one `clutch_react` call per user turn. Skip it when there is",
        "  nothing useful to say.",
        "- `speech` must be <= 140 characters. `tip` must be <= 240 characters.",
        "- `ttlMs` defaults to 4000ms; never exceed 15000ms.",
        "- Prefer `champ`/`boost`/`nitro` for wins, `think`/`tune` for clarifying",
        "  follow-ups, `alert`/`repair` for blockers, `finish` when wrapping up.",
        "",
        "## Completion Signal",
        "Call `complete_onboarding()` only after every completion criterion above",
        "is satisfied and `USER.md` / `DEALERSHIP.md` / `TEAM.md` have been written.",
        "Do not call it speculatively: the frontend will navigate away on the first",
        "invocation.",
        "",
        "## Output Rules",
        "- Ask one short, focused question at a time and wait for a reply.",
        "- Never request passwords, API keys, SSNs, bank details, or any other",
        "  sensitive secret. If the user volunteers one, thank them and move on",
        "  without recording it.",
        "- Keep replies conversational and human. Do not dump raw markdown blocks",
        "  into the chat; surface summaries via the workspace files instead.",
        "- Stay in English and use the user's display name sparingly.",
        "",
    ]
    return "\n".join(lines)


def _build_context() -> str | None:
    """Build full onboarding context string for injection; None if marker absent.

    Mirrors build-context.ts:buildAppendSystemContext.
    """
    if not _marker_active():
        return None

    sections: list[str] = [_build_onboarding_addendum()]

    dealership = _read_workspace_file("DEALERSHIP.md")
    if dealership:
        sections.extend(["", "## DEALERSHIP context:", "", dealership])

    team = _read_workspace_file("TEAM.md")
    if team:
        sections.extend(["", "## TEAM context:", "", team])

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# pre_llm_call hook
# ---------------------------------------------------------------------------

def _pre_llm_call_hook(**kwargs) -> dict | None:  # noqa: ARG001
    """Inject onboarding context into the current turn when mode is active."""
    context = _build_context()
    if context is None:
        return None
    return {"context": context}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    ctx.register_tool(
        name="clutch_react",
        toolset="wheelbase_onboarding",
        schema=schemas.CLUTCH_REACT,
        handler=clutch_react_tool.clutch_react,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="complete_onboarding",
        toolset="wheelbase_onboarding",
        schema=schemas.COMPLETE_ONBOARDING,
        handler=complete_onboarding_tool.complete_onboarding,
        check_fn=_marker_active,
    )
    ctx.register_hook("pre_llm_call", _pre_llm_call_hook)
