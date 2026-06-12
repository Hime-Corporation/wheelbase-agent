"""Tool schemas for wheelbase-onboarding — what the LLM reads to decide when to call."""

MASCOT_STATES = [
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
]

CLUTCH_REACT = {
    "name": "clutch_react",
    "description": (
        "Trigger a Clutch mascot reaction in the onboarding UI. Use sparingly "
        "— at most one call per user turn — to reinforce wins or surface a "
        "concise tip."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "enum": MASCOT_STATES,
                "description": (
                    "Mascot state to display. Choose the best match for the moment."
                ),
            },
            "speech": {
                "type": "string",
                "description": (
                    "Optional short speech bubble shown above Clutch (<= 140 chars)."
                ),
                "maxLength": 140,
            },
            "tip": {
                "type": "string",
                "description": (
                    "Optional longer tip shown alongside the mascot (<= 240 chars)."
                ),
                "maxLength": 240,
            },
            "ttlMs": {
                "type": "number",
                "description": (
                    "Optional bubble lifetime in ms. Default 4000, max 15000."
                ),
                "minimum": 0,
                "maximum": 15000,
            },
        },
        "required": ["state"],
        "additionalProperties": False,
    },
}

COMPLETE_ONBOARDING = {
    "name": "complete_onboarding",
    "description": (
        "Signal that onboarding is complete. Call exactly once, only when the "
        "interview completion criteria are satisfied and USER.md / DEALERSHIP.md "
        "/ TEAM.md have been written."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}
