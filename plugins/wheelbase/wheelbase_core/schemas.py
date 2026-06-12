"""Tool schemas for wheelbase-core — what the LLM reads to decide when to call."""

GET_CAR = {
    "name": "get_car",
    "description": (
        "Retrieve the full record for a single vehicle from the dealership's "
        "inventory by its UUID. Use inventory_search first to find the car ID, "
        "then call this to inspect all fields (VIN, year, make, model, status, "
        "asking price, odometer, seller source, acquisition date, notes, etc.). "
        "The record includes the human-readable `status` label (e.g. 'Frontline Ready') "
        "and `status_code` alongside the numeric `status_id` — report the label."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "carId": {
                "type": "string",
                "description": "UUID of the vehicle to retrieve.",
            },
        },
        "required": ["carId"],
    },
}

INVENTORY_SEARCH = {
    "name": "inventory_search",
    "description": (
        "Search the dealership's vehicle inventory. Use this to find cars by keyword "
        "(VIN, make, model, stock number), optionally narrowed by make, status ID, or "
        "year range. Returns a summary list of matching vehicles with their IDs, year, "
        "make, model, stock number, asking price, and status — both the human-readable "
        "label (`status`, e.g. 'Frontline Ready') and machine code (`statusCode`, e.g. "
        "'frontline') alongside the numeric `statusId`. Report the label to users, not the "
        "number. Use `get_car` to retrieve the full record for a specific vehicle."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Full-text search string matched against VIN, make, model, and "
                    "stock number (case-insensitive)."
                ),
            },
            "make": {
                "type": "string",
                "description": (
                    "Optional: filter by vehicle make (e.g. 'Honda'). "
                    "Case-insensitive partial match."
                ),
            },
            "statusId": {
                "type": "number",
                "description": "Optional: filter by inventory status ID.",
            },
            "yearRange": {
                "type": "object",
                "properties": {
                    "min": {"type": "number", "description": "Minimum model year (inclusive)."},
                    "max": {"type": "number", "description": "Maximum model year (inclusive)."},
                },
                "required": ["min", "max"],
                "additionalProperties": False,
                "description": "Optional: restrict results to a model year range.",
            },
            "limit": {
                "type": "number",
                "description": "Maximum number of results to return. Default 50, max 200.",
                "minimum": 1,
                "maximum": 200,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

UPDATE_INVENTORY_STATUS = {
    "name": "update_inventory_status",
    "description": (
        "Update the status of a single inventory vehicle. Accepts a numeric status ID "
        "and an optional note that is recorded in the status change history. "
        "Executes immediately without requiring confirmation. Safe-write operation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "carId": {
                "type": "string",
                "description": "UUID of the inventory car whose status to update.",
            },
            "newStatusId": {
                "type": "number",
                "description": (
                    "Integer ID of the new inventory status "
                    "(from inventory_status_definition table)."
                ),
            },
            "note": {
                "type": "string",
                "description": (
                    "Optional note explaining the status change; "
                    "recorded in status history."
                ),
            },
        },
        "required": ["carId", "newStatusId"],
        "additionalProperties": False,
    },
}

LIST_RUNLISTS = {
    "name": "list_runlists",
    "description": (
        "List all runlists available for the current dealership. Runlists are ordered "
        "lists of vehicles compiled for an auction. Returns runlist summaries (ID, name, "
        "auction ID, auction date, created date). Use `get_runlist_cars` to browse the "
        "vehicles in a specific runlist."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "number",
                "description": "Maximum number of runlists to return. Default 25.",
                "minimum": 1,
                "maximum": 500,
            },
        },
        "additionalProperties": False,
    },
}

GET_RUNLIST_CARS = {
    "name": "get_runlist_cars",
    "description": (
        "Retrieve the list of vehicles in a specific runlist. Excludes archived cars. "
        "Optionally filter by make. Returns vehicle summaries with IMX data (year, make, "
        "model, VIN, stock number). Use `list_runlists` to find a runlist ID."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "runlistId": {
                "type": "string",
                "description": "UUID of the runlist whose cars to retrieve.",
            },
            "make": {
                "type": "string",
                "description": (
                    "Optional: filter cars by make (e.g. 'Toyota'). "
                    "Case-insensitive partial match."
                ),
            },
            "limit": {
                "type": "number",
                "description": "Maximum number of cars to return. Default 100.",
                "minimum": 1,
                "maximum": 5000,
            },
        },
        "required": ["runlistId"],
        "additionalProperties": False,
    },
}

ASSESS_RUNLIST = {
    "name": "assess_runlist",
    "description": (
        "Assess all vehicles in a runlist with an agent-side scoring pass. "
        "Iterates each car in the runlist, applies a scoring heuristic (optionally guided "
        "by free-form criteria text), and returns a summary with top score and average. "
        "Long-running — supports cancellation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "runlistId": {
                "type": "string",
                "description": "UUID of the runlist to assess.",
            },
            "criteria": {
                "type": "string",
                "description": (
                    "Optional free-form scoring guidance (e.g. 'Prefer low-mileage SUVs'). "
                    "Applied as a make/model text match for bonus scoring in v1."
                ),
            },
        },
        "required": ["runlistId"],
        "additionalProperties": False,
    },
}

ARCHIVE_RUNLIST_CARS = {
    "name": "archive_runlist_cars",
    "description": (
        "Soft-archive one or more cars within a runlist by setting their archived_at "
        "timestamp. Archived cars are hidden from the default runlist view. "
        "REQUIRES user confirmation before executing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "runlistId": {
                "type": "string",
                "description": "UUID of the runlist containing the cars to archive.",
            },
            "carIds": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 1000,
                "description": "Array of car UUIDs (1–1000) to archive within the runlist.",
            },
            "reason": {
                "type": "string",
                "description": (
                    "Optional human-readable reason for archiving "
                    "(for audit/logging purposes)."
                ),
            },
        },
        "required": ["runlistId", "carIds"],
        "additionalProperties": False,
    },
}

CREATE_WORK_ORDER = {
    "name": "create_work_order",
    "description": (
        "Create a new work order for a specific vehicle. Returns the created work order "
        "id and summary. Executes immediately without requiring confirmation. "
        "Safe-write operation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "carId": {
                "type": "string",
                "description": "UUID of the inventory car to create the work order for.",
            },
            "title": {
                "type": "string",
                "description": "Short title describing the work to be done.",
            },
            "description": {
                "type": "string",
                "description": "Optional detailed description of the work.",
            },
            "vendorId": {
                "type": "string",
                "description": (
                    "Optional UUID of the vendor (shop) assigned to this work order."
                ),
            },
            "scheduledAt": {
                "type": "string",
                "description": (
                    "Optional ISO date string for when the work is scheduled "
                    "(e.g. '2026-06-15T10:00:00Z')."
                ),
            },
        },
        "required": ["carId", "title"],
        "additionalProperties": False,
    },
}

GET_WORK_ORDER = {
    "name": "get_work_order",
    "description": (
        "Retrieve work orders for a specific vehicle (car). Returns a list of work order "
        "summaries including id, title, status, vendor_id, and scheduled_at. "
        "Optionally filter by status."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "carId": {
                "type": "string",
                "description": "UUID of the inventory car whose work orders to retrieve.",
            },
            "status": {
                "type": "string",
                "enum": ["draft", "open", "scheduled", "in_progress", "completed", "cancelled"],
                "description": (
                    "Optional status filter. One of: draft, open, scheduled, "
                    "in_progress, completed, cancelled."
                ),
            },
        },
        "required": ["carId"],
        "additionalProperties": False,
    },
}

DELETE_WORK_ORDER = {
    "name": "delete_work_order",
    "description": (
        "Permanently delete a work order by ID. This action is irreversible. "
        "REQUIRES user confirmation before executing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workOrderId": {
                "type": "string",
                "description": "UUID of the work order to delete.",
            },
            "carId": {
                "type": "string",
                "description": (
                    "UUID of the inventory car the work order belongs to "
                    "(used for UI routing after deletion)."
                ),
            },
        },
        "required": ["workOrderId", "carId"],
        "additionalProperties": False,
    },
}

CREATE_INSPECTION_NOTE = {
    "name": "create_inspection_note",
    "description": (
        "Add a free-form inspection note to a vehicle's recon intake inspection record. "
        "Creates or updates the inspection entry for the given car. "
        "Safe-write operation — executes immediately without confirmation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "carId": {
                "type": "string",
                "description": "UUID of the inventory car to add the inspection note to.",
            },
            "note": {
                "type": "string",
                "description": "Free-form inspection note text.",
            },
            "category": {
                "type": "string",
                "description": "Optional category label for the note (e.g. 'mechanical', 'cosmetic').",
            },
        },
        "required": ["carId", "note"],
        "additionalProperties": False,
    },
}

BULK_INSPECT = {
    "name": "bulk_inspect",
    "description": (
        "Fetch and summarise inspection records for a list of vehicles. "
        "Streams progress per vehicle. Each car's inspection state is reported as "
        "'completed', 'in-progress', or 'pending' (no record found). "
        "Returns a summary in details.summary."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "carIds": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Array of inventory car UUIDs to inspect (min 1, max 200).",
                "minItems": 1,
                "maxItems": 200,
            },
        },
        "required": ["carIds"],
        "additionalProperties": False,
    },
}

LIST_VENDORS = {
    "name": "list_vendors",
    "description": (
        "List vendors available to the dealership. Optionally filter by vendor type "
        "(e.g. body_shop, transport) or search by name. Returns id, name, vendor_type, "
        "phone, and email for each vendor."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": [
                    "body_shop",
                    "detail",
                    "mechanical",
                    "glass",
                    "tires",
                    "reconditioning",
                    "auction",
                    "transport",
                    "other",
                ],
                "description": (
                    "Optional vendor type filter. One of: body_shop, detail, mechanical, "
                    "glass, tires, reconditioning, auction, transport, other."
                ),
            },
            "search": {
                "type": "string",
                "description": "Optional partial name search (case-insensitive).",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}

GET_VENDOR = {
    "name": "get_vendor",
    "description": (
        "Retrieve the full record for a specific vendor by its UUID. Returns all vendor "
        "fields including name, vendor_type, phone, email, city, state, and notes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vendorId": {
                "type": "string",
                "description": "UUID of the vendor to retrieve.",
            },
        },
        "required": ["vendorId"],
        "additionalProperties": False,
    },
}

SEND_TO_VENDOR = {
    "name": "send_to_vendor",
    "description": (
        "Assign a work order to a vendor and set its status to 'scheduled'. Updates "
        "vendor_id and optionally scheduled_at, then marks the work order as scheduled. "
        "REQUIRES user confirmation before executing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workOrderId": {
                "type": "string",
                "description": "UUID of the work order to send to a vendor.",
            },
            "vendorId": {
                "type": "string",
                "description": "UUID of the vendor to assign the work order to.",
            },
            "scheduledAt": {
                "type": "string",
                "description": (
                    "Optional ISO 8601 date-time string for the scheduled "
                    "service appointment."
                ),
            },
            "note": {
                "type": "string",
                "description": (
                    "Optional note to record with this vendor assignment "
                    "(for logging; not persisted to the work order)."
                ),
            },
        },
        "required": ["workOrderId", "vendorId"],
        "additionalProperties": False,
    },
}

GENERATE_DEMAND_SCORE = {
    "name": "generate_demand_score",
    "description": (
        "Generate demand scores for inventory vehicles based on their IMX score, "
        "mileage, and optional market context. "
        "If carIds is omitted, scores all active inventory (capped at 200 vehicles, "
        "ordered newest first). Returns final scored list in details.scores."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "carIds": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of inventory car UUIDs to score. If omitted, "
                    "all active inventory is scored (max 200)."
                ),
                "minItems": 1,
                "maxItems": 200,
            },
            "market": {
                "type": "string",
                "description": (
                    "Optional free-form market descriptor (e.g. 'Northeast', "
                    "'Dallas metro'). Used as context for scoring."
                ),
                "maxLength": 200,
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}
