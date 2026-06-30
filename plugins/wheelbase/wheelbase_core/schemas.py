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

CREATE_WORK_ITEM = {
    "name": "create_work_item",
    "description": (
        "Create a new work item in the unified work_item table for a vehicle. "
        "Supports tasks, reminders, findings, work orders, and work order lines. "
        "Root items (task, reminder) require carId; child items (finding, work_order, "
        "work_order_line) require parentId. Executes immediately without confirmation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "carId": {
                "type": "string",
                "description": (
                    "UUID of the inventory car. Required for root items (task, reminder). "
                    "Optional for child items that inherit car from the parent."
                ),
            },
            "title": {
                "type": "string",
                "description": "Short title describing the work item (required, non-empty).",
            },
            "type": {
                "type": "string",
                "enum": ["task", "work_order", "reminder", "finding", "work_order_line"],
                "description": (
                    "Work item type. Root types: task, reminder. "
                    "Child types (require parentId): finding, work_order, work_order_line. "
                    "Defaults to 'task'."
                ),
            },
            "parentId": {
                "type": "string",
                "description": (
                    "UUID of the parent work_item. Required for child types "
                    "(finding, work_order, work_order_line). Must not be set for root types."
                ),
            },
            "description": {
                "type": "string",
                "description": "Optional detailed description of the work item.",
            },
            "priority": {
                "type": "string",
                "enum": ["low", "medium", "high", "urgent"],
                "description": "Priority level. Defaults to 'medium'.",
            },
            "estCostCents": {
                "type": "integer",
                "minimum": 0,
                "description": "Optional estimated cost in cents (non-negative integer).",
            },
            "vendorId": {
                "type": "string",
                "description": "Optional UUID of the vendor assigned to this work item.",
            },
            "dueAt": {
                "type": "string",
                "description": (
                    "Optional ISO 8601 date-time string for when the item is due "
                    "(e.g. '2026-07-01T10:00:00Z')."
                ),
            },
            "stageDefinitionId": {
                "type": "string",
                "description": "Optional UUID of the stage definition to associate with this item.",
            },
        },
        "required": ["title"],
        "additionalProperties": False,
    },
}

GET_WORK_ITEM = {
    "name": "get_work_item",
    "description": (
        "Read work_item rows for a vehicle or fetch a specific item by ID. "
        "Optionally returns the nested tree view (tree=true) via the work_item_tree view. "
        "At least one of carId or workItemId is required."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "carId": {
                "type": "string",
                "description": "UUID of the inventory car whose work items to retrieve.",
            },
            "workItemId": {
                "type": "string",
                "description": "UUID of a specific work item to retrieve.",
            },
            "type": {
                "type": "string",
                "description": "Optional: filter results by work item type (e.g. 'task', 'finding').",
            },
            "status": {
                "type": "string",
                "description": "Optional: filter results by status (e.g. 'todo', 'done').",
            },
            "tree": {
                "type": "boolean",
                "description": (
                    "If true, query the work_item_tree view for nested/hierarchical results. "
                    "Defaults to false (flat work_item table)."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "description": "Maximum number of results to return. Default 100.",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "Pagination offset. Default 0.",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}

DELETE_WORK_ITEM = {
    "name": "delete_work_item",
    "description": (
        "Permanently delete a work item and its child items (cascade). "
        "Pre-fetches the item title and child count before deletion so the result "
        "describes what was removed. REQUIRES user confirmation before executing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workItemId": {
                "type": "string",
                "description": "UUID of the work item to delete.",
            },
        },
        "required": ["workItemId"],
        "additionalProperties": False,
    },
}

LIST_INVENTORY_STATUSES = {
    "name": "list_inventory_statuses",
    "description": (
        "Return all configured inventory status definitions for the dealership. "
        "Results are ordered by sort_order ascending and include id, code, label, "
        "and sort_order. Use this to resolve status codes/labels or to populate a "
        "status picker before calling update_inventory_status."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
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

GET_RECON_BOARD = {
    "name": "get_recon_board",
    "description": (
        "Retrieve the reconditioning board for a single vehicle as a nested tree. "
        "Returns the recon run root with its stages, and findings/work orders nested "
        "under each stage (from the work_item_tree view). Use this to review a car's "
        "recon progress, stage status, holds, and rolled-up cost estimates. If the car "
        "has no recon run yet, a flat list of its work items is returned instead — call "
        "start_recon to kick off reconditioning."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "carId": {
                "type": "string",
                "description": "UUID of the inventory car whose recon board to retrieve.",
            },
        },
        "required": ["carId"],
        "additionalProperties": False,
    },
}

START_RECON = {
    "name": "start_recon",
    "description": (
        "Begin reconditioning for a vehicle by setting its inventory status to 'recon'. "
        "The backend trigger automatically creates the recon run and its stages. Use this "
        "when a car needs to enter the recon workflow. The recon status ID is resolved "
        "automatically; only pass reconStatusId to override it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "carId": {
                "type": "string",
                "description": "UUID of the inventory car to move into recon.",
            },
            "reconStatusId": {
                "type": "number",
                "description": (
                    "Optional: explicit inventory status ID for 'recon'. "
                    "If omitted, it is resolved automatically from the status definitions."
                ),
            },
        },
        "required": ["carId"],
        "additionalProperties": False,
    },
}

COMPLETE_STAGE = {
    "name": "complete_stage",
    "description": (
        "Mark a recon stage as done by setting its status to 'done'. Use this when a "
        "reconditioning stage (e.g. mechanical, detail) has been finished. To set other "
        "statuses or place a hold, use update_stage instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "stageId": {
                "type": "string",
                "description": "UUID of the recon stage (work_item) to mark complete.",
            },
        },
        "required": ["stageId"],
        "additionalProperties": False,
    },
}

UPDATE_STAGE = {
    "name": "update_stage",
    "description": (
        "Update mutable fields on a recon stage: status, hold type/reason, assignee, "
        "vendor, or estimated cost. Use this to place a stage on hold (set holdType — the "
        "stage is automatically set to 'blocked' unless you also pass an explicit status), "
        "reassign work, attach a vendor, or revise a cost estimate."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "stageId": {
                "type": "string",
                "description": "UUID of the recon stage (work_item) to update.",
            },
            "status": {
                "type": "string",
                "enum": [
                    "todo",
                    "ready",
                    "in_progress",
                    "blocked",
                    "done",
                    "skipped",
                    "cancelled",
                ],
                "description": "Optional new status for the stage.",
            },
            "holdType": {
                "type": "string",
                "enum": ["parts", "vendor", "approval", "transport"],
                "description": (
                    "Optional hold reason category. If set without an explicit status, "
                    "the stage is forced to 'blocked'."
                ),
            },
            "holdReason": {
                "type": "string",
                "description": "Optional free-form explanation for the hold.",
            },
            "assignedToUserId": {
                "type": "string",
                "description": "Optional UUID of the user to assign this stage to.",
            },
            "vendorId": {
                "type": "string",
                "description": "Optional UUID of the vendor responsible for this stage.",
            },
            "estCostCents": {
                "type": "integer",
                "minimum": 0,
                "description": "Optional estimated cost in cents (non-negative integer).",
            },
        },
        "required": ["stageId"],
        "additionalProperties": False,
    },
}

CREATE_FINDING = {
    "name": "create_finding",
    "description": (
        "Record a finding discovered during reconditioning under a recon stage. A finding "
        "is a defect or item of work uncovered during inspection (e.g. 'worn brake pads'). "
        "Findings are child work items and require a parent stage. Use this to log issues "
        "as they are found so they roll up into the recon board."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "parentId": {
                "type": "string",
                "description": "UUID of the parent recon stage (work_item) for this finding.",
            },
            "title": {
                "type": "string",
                "description": "Short description of the finding (required, non-empty).",
            },
            "estCostCents": {
                "type": "integer",
                "minimum": 0,
                "description": "Optional estimated cost to remedy in cents (non-negative integer).",
            },
            "priority": {
                "type": "string",
                "enum": ["low", "medium", "high", "urgent"],
                "description": "Optional priority of the finding. Defaults to 'medium'.",
            },
            "carId": {
                "type": "string",
                "description": "Optional UUID of the inventory car (inherited from parent if omitted).",
            },
        },
        "required": ["parentId", "title"],
        "additionalProperties": False,
    },
}

ADD_WORK_ITEM_COMMENT = {
    "name": "add_work_item_comment",
    "description": (
        "Add a comment to a work item. Use this to record notes, status updates, or "
        "context on any work item (task, recon stage, finding, work order, etc.). The "
        "comment is attributed to the signed-in user."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "workItemId": {
                "type": "string",
                "description": "UUID of the work item to comment on.",
            },
            "content": {
                "type": "string",
                "description": "Comment text (required, non-empty).",
            },
        },
        "required": ["workItemId", "content"],
        "additionalProperties": False,
    },
}

QUERY_WORK = {
    "name": "query_work",
    "description": (
        "Query work items across the dealership with optional filters. Use this to find "
        "work by status, type, vendor, assignee, or due-date range — for example overdue "
        "tasks, all blocked stages, or every work order for a vendor. Results are ordered "
        "by due date and paginated. To read a single car's work items, use get_work_item."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": [
                    "todo",
                    "ready",
                    "in_progress",
                    "blocked",
                    "done",
                    "skipped",
                    "cancelled",
                ],
                "description": "Optional: filter by work item status.",
            },
            "type": {
                "type": "string",
                "enum": [
                    "recon_run",
                    "stage",
                    "task",
                    "work_order",
                    "work_order_line",
                    "finding",
                    "reminder",
                ],
                "description": "Optional: filter by work item type.",
            },
            "vendorId": {
                "type": "string",
                "description": "Optional: filter by assigned vendor UUID.",
            },
            "assignedToUserId": {
                "type": "string",
                "description": "Optional: filter by assigned user UUID.",
            },
            "dueBefore": {
                "type": "string",
                "description": "Optional: only items due on or before this ISO 8601 date-time.",
            },
            "dueAfter": {
                "type": "string",
                "description": "Optional: only items due on or after this ISO 8601 date-time.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "description": "Maximum number of results to return. Default 50.",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "Pagination offset. Default 0.",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}

GET_INVENTORY_STATS = {
    "name": "get_inventory_stats",
    "description": (
        "Get summary statistics for a dealership's inventory (counts, value, age, status "
        "breakdown, etc.) via the get_inventory_stats RPC. Use this for an at-a-glance "
        "overview of the lot. If the user belongs to exactly one dealership the context is "
        "resolved automatically; otherwise pass dealershipId."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "dealershipId": {
                "type": "string",
                "description": (
                    "Optional UUID of the dealership. Required only when the user belongs "
                    "to more than one dealership."
                ),
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}

GET_INVENTORY_FILTER_OPTIONS = {
    "name": "get_inventory_filter_options",
    "description": (
        "Get the available filter values for a dealership's inventory (makes, models, "
        "statuses, year range, etc.) via the get_inventory_filter_options RPC. Use this to "
        "discover valid filter options before searching or to populate a filter UI. If the "
        "user belongs to exactly one dealership the context is resolved automatically; "
        "otherwise pass dealershipId."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "dealershipId": {
                "type": "string",
                "description": (
                    "Optional UUID of the dealership. Required only when the user belongs "
                    "to more than one dealership."
                ),
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}
