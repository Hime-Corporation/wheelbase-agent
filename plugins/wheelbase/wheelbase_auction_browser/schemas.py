"""Tool schemas for wheelbase-auction-browser — what the LLM reads."""

LIST_AUCTIONS = {
    "name": "list_auctions",
    "description": (
        "List upcoming auctions tracked in the workspace snapshot "
        "(.wheelbase/auctions.json). Returns each auction's id, name, "
        "location, start time, and runlist count."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

LIST_RUNLISTS = {
    "name": "list_runlists",
    "description": (
        "List the runlists for a specific auction. Pass the auction `id` "
        "returned by `list_auctions`. Returns runlist stubs (id, name) when "
        "the snapshot includes them, or a note when only the count is available."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "auctionId": {
                "type": "string",
                "description": "The id of the auction whose runlists to retrieve.",
            },
        },
        "required": ["auctionId"],
    },
}

GET_RUNLIST = {
    "name": "get_runlist",
    "description": (
        "Get all cars in a runlist, with IMX scores merged per car when "
        "available. If the IMX snapshot is absent, cars are returned without "
        "scores and a note is included."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "runlistId": {
                "type": "string",
                "description": "The id of the runlist to retrieve.",
            },
        },
        "required": ["runlistId"],
    },
}

TOP_IMX_PICKS = {
    "name": "top_imx_picks",
    "description": (
        "Return the top N cars from a runlist ranked by IMX score. "
        "Optionally filter by minimum tier. Requires both the runlist snapshot "
        "and the IMX snapshot to be present in the workspace."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "runlistId": {
                "type": "string",
                "description": "The id of the runlist to rank.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Max picks to return (default 10).",
            },
            "minTier": {
                "type": "integer",
                "minimum": 0,
                "maximum": 4,
                "description": "Filter to cars with tier >= this value (default 0 = all).",
            },
        },
        "required": ["runlistId"],
    },
}

EXPLAIN_IMX = {
    "name": "explain_imx",
    "description": (
        "Return a human-readable breakdown of a car's IMX score, including "
        "tier label, per-component sub-scores, and category matches."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "runlistId": {
                "type": "string",
                "description": "The id of the runlist containing the car.",
            },
            "carId": {
                "type": "string",
                "description": "The id of the car to explain.",
            },
        },
        "required": ["runlistId", "carId"],
    },
}

REFRESH_RUNLIST = {
    "name": "refresh_runlist",
    "description": (
        "Re-score and refresh the IMX snapshot for a runlist. "
        "Invalidates the cached IMX scores, re-fetches them from the server, "
        "and writes updated snapshot files to the workspace."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "runlistId": {
                "type": "string",
                "description": "The id of the runlist to refresh.",
            },
        },
        "required": ["runlistId"],
    },
}

FLAG_CAR = {
    "name": "flag_car",
    "description": (
        "Flag a car for follow-up by appending a flag entry to its history. "
        "An optional note can be attached. Uses the natural key (runlistId, carId)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "runlistId": {
                "type": "string",
                "description": "The id of the runlist the car belongs to.",
            },
            "carId": {
                "type": "string",
                "description": "The id of the car to flag.",
            },
            "note": {
                "type": "string",
                "description": "Optional note to attach to the flag.",
            },
        },
        "required": ["runlistId", "carId"],
    },
}

VOTE_ON_CAR = {
    "name": "vote_on_car",
    "description": (
        "Cast a vote (+1 or -1) on a car in a runlist. "
        "Updates the car's vote count and appends a vote entry to its history. "
        "An optional note can explain the vote."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "runlistId": {
                "type": "string",
                "description": "The id of the runlist the car belongs to.",
            },
            "carId": {
                "type": "string",
                "description": "The id of the car to vote on.",
            },
            "vote": {
                "type": "integer",
                "enum": [-1, 1],
                "description": "+1 to upvote, -1 to downvote.",
            },
            "note": {
                "type": "string",
                "description": "Optional note explaining the vote.",
            },
        },
        "required": ["runlistId", "carId", "vote"],
    },
}
