"""Tool schemas for wheelbase-inspection — what the LLM reads to decide when to call."""

INSPECTION_RENDER_RISK_REVIEW = {
    "name": "inspection_render_risk_review",
    "description": (
        "Render a structured risk-review card for a completed vehicle inspection. "
        "Call this after researching the inspection results (web search, VIN data, "
        "item statuses) to produce a ranked list of risks the dealership should address. "
        "The payload is validated and returned as a DB-ready JSON string — the Go backend "
        "persists it. Do not call this until you have gathered sufficient evidence. "
        "Each risk must have a unique risk_id (snake_case slug), a title, the reason it "
        "was flagged, the evidence basis (list of item_ids or text snippets), a recommended "
        "action, and your confidence level."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "inspection_id": {
                "type": "string",
                "description": "UUID of the inspection being reviewed. Must match the inspection provided in context.",
            },
            "state_hash": {
                "type": "string",
                "description": "Deterministic hash of the inspection state computed by the Go backend and passed in context. Echo it back verbatim.",
            },
            "risks": {
                "type": "array",
                "description": "Ordered list of risks, most critical first.",
                "items": {
                    "type": "object",
                    "properties": {
                        "risk_id": {
                            "type": "string",
                            "description": "Unique snake_case slug for this risk within the payload (e.g. 'engine_oil_leak').",
                        },
                        "title": {
                            "type": "string",
                            "description": "Short human-readable title (e.g. 'Engine Oil Leak at Valve Cover').",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why this is flagged as a risk — what the inspection data shows.",
                        },
                        "evidence_basis": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of item_ids or descriptive evidence snippets that support this risk.",
                        },
                        "recommended_action": {
                            "type": "string",
                            "description": "Concrete next step for the dealership (e.g. 'Inspect valve cover gasket; estimate repair cost').",
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                            "description": "Confidence level in this risk assessment.",
                        },
                    },
                    "required": ["risk_id", "title", "reason", "evidence_basis", "recommended_action", "confidence"],
                    "additionalProperties": False,
                },
                "minItems": 1,
            },
        },
        "required": ["inspection_id", "risks"],
        "additionalProperties": False,
    },
}

INSPECTION_RENDER_CHECKLIST = {
    "name": "inspection_render_checklist",
    "description": (
        "Render a set of follow-up diagnostic questions for a specific inspection risk. "
        "Called after the dealership staff marks a risk as 'added to inspection'. "
        "Each question has a type (multiple_choice, multi_select, free_response, yes_no, "
        "measurement, or checklist), a prompt, and a summary_label used in the report. "
        "Options MUST be provided for multiple_choice and multi_select; omit options for "
        "all other types. The payload is validated and returned as DB-ready JSON."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "inspection_id": {
                "type": "string",
                "description": "UUID of the parent inspection.",
            },
            "state_hash": {
                "type": "string",
                "description": "Deterministic state hash from context. Echo verbatim if provided.",
            },
            "triggered_by_risk_id": {
                "type": "string",
                "description": "The risk_id of the risk that triggered this checklist (from inspection_render_risk_review).",
            },
            "questions": {
                "type": "array",
                "description": "Ordered list of diagnostic questions for the tech to answer.",
                "items": {
                    "type": "object",
                    "properties": {
                        "question_type": {
                            "type": "string",
                            "enum": [
                                "multiple_choice",
                                "multi_select",
                                "free_response",
                                "yes_no",
                                "measurement",
                                "checklist",
                            ],
                            "description": "Type of answer input expected.",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "The question text shown to the technician.",
                        },
                        "summary_label": {
                            "type": "string",
                            "description": "Short label used in the inspection report (e.g. 'Oil leak location').",
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Answer choices. Required for multiple_choice and multi_select; omit for all other types.",
                        },
                        "step_id": {
                            "type": "string",
                            "description": "Optional: catalog step_id this question relates to.",
                        },
                        "item_id": {
                            "type": "string",
                            "description": "Optional: catalog item_id this question relates to.",
                        },
                        "help_text": {
                            "type": "string",
                            "description": "Optional: supplementary guidance shown below the prompt.",
                        },
                        "required": {
                            "type": "boolean",
                            "description": "Whether this question must be answered before the inspection can be completed.",
                        },
                        "validation": {
                            "type": "object",
                            "description": "Optional: type-specific validation hints (e.g. {min: 0, max: 100, unit: 'mm'} for measurement).",
                        },
                    },
                    "required": ["question_type", "prompt", "summary_label"],
                    "additionalProperties": False,
                },
                "minItems": 1,
            },
        },
        "required": ["inspection_id", "questions"],
        "additionalProperties": False,
    },
}
