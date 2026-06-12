"""Tool schemas for wheelbase-demand-matrix — what the LLM reads to decide when to call."""

READ_DEMAND_MATRIX = {
    "name": "read_demand_matrix",
    "description": (
        "Read the dealership's current demand matrix from the agent workspace "
        "(.wheelbase/demand-matrix.json). Returns categories with target counts, "
        "current inventory counts, and keywords. Use this for context before "
        "proposing target changes."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}

READ_INVENTORY_SUMMARY = {
    "name": "read_inventory_summary",
    "description": (
        "Read the inventory summary by demand category from the agent workspace "
        "(.wheelbase/inventory-summary.json). Returns current vs target counts, "
        "aging metrics, and recent sales per category."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}

READ_UNLABELED_CARS = {
    "name": "read_unlabeled_cars",
    "description": (
        "Read the next batch of inventory cars without a demand category label "
        "from the agent workspace (.wheelbase/unlabeled-cars.json). Each car "
        "includes suggested category keys. Use with save_inventory_demand_labels "
        "to bulk-assign categories."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Maximum number of cars to return (default 25).",
            },
        },
        "additionalProperties": False,
    },
}

PROPOSE_DEMAND_TARGETS = {
    "name": "propose_demand_targets",
    "description": (
        "Propose target inventory counts for one or more demand categories. "
        "Present a structured proposal to the user; once accepted, call "
        "save_demand_overrides to persist the changes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "proposals": {
                "type": "array",
                "description": "List of category target proposals.",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Demand category key.",
                        },
                        "target": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 200,
                            "description": "Proposed target inventory count.",
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Explanation for the proposed target.",
                        },
                    },
                    "required": ["key", "target"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["proposals"],
        "additionalProperties": False,
    },
}

SAVE_DEMAND_OVERRIDES = {
    "name": "save_demand_overrides",
    "description": (
        "Persist the dealer's overrides for one or more demand categories via "
        "the Go API. Call this after the dealer accepts a proposal from "
        "propose_demand_targets."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "overrides": {
                "type": "array",
                "description": "List of category overrides to save.",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Demand category key.",
                        },
                        "target": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 200,
                            "description": "Target inventory count.",
                        },
                        "keywords": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional keyword list for the category.",
                        },
                    },
                    "required": ["key", "target"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["overrides"],
        "additionalProperties": False,
    },
}

SAVE_INVENTORY_DEMAND_LABELS = {
    "name": "save_inventory_demand_labels",
    "description": (
        "Assign demand category labels to inventory cars that are currently "
        "unlabeled. Use read_unlabeled_cars to get the batch, then call this "
        "to persist the labels via PostgREST."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "labels": {
                "type": "array",
                "description": "List of car-to-category label assignments.",
                "items": {
                    "type": "object",
                    "properties": {
                        "inventoryCarId": {
                            "type": "string",
                            "format": "uuid",
                            "description": "UUID of the inventory car to label.",
                        },
                        "key": {
                            "type": "string",
                            "description": "Demand category key to assign.",
                        },
                    },
                    "required": ["inventoryCarId", "key"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["labels"],
        "additionalProperties": False,
    },
}

COMPLETE_DEMAND_MATRIX_SETUP = {
    "name": "complete_demand_matrix_setup",
    "description": (
        "Signal that the demand-matrix setup session is complete. Call this "
        "after all categories have been confirmed and unlabeled cars have been "
        "labeled. The workspace transitions out of demand-matrix setup mode."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}
