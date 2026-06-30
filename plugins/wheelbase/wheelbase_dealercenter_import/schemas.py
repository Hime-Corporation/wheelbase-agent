"""Tool schemas for wheelbase-dealercenter-import — what the LLM reads."""

DC_CONNECT = {
    "name": "dc_connect",
    "description": (
        "Check whether the browser CDP (Chrome DevTools Protocol) endpoint is "
        "configured and reachable. Returns cdpConfigured=true when BROWSER_CDP_URL "
        "is set. If not configured, returns setup instructions for launching Chrome "
        "with --remote-debugging-port=9222 and logging in to DealerCenter before "
        "calling dc_export_historic."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

DC_EXPORT_HISTORIC = {
    "name": "dc_export_historic",
    "description": (
        "Drive a DealerCenter browser session to export the historic sold-vehicle "
        "report as a CSV file. Returns a step-by-step procedure dict the agent "
        "executes using its browser tools (navigate, click, set download). "
        "Optionally scoped to a date range."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "dateFrom": {
                "type": "string",
                "description": "Start of date range in ISO 8601 format (YYYY-MM-DD). "
                               "If omitted the export defaults to DealerCenter's "
                               "maximum history window.",
            },
            "dateTo": {
                "type": "string",
                "description": "End of date range in ISO 8601 format (YYYY-MM-DD). "
                               "Defaults to today when omitted.",
            },
        },
    },
}

DC_INGEST = {
    "name": "dc_ingest",
    "description": (
        "Parse a DealerCenter CSV or Excel export and import the rows into the "
        "Wheelbase inventory as historic vehicles. By default runs in dry-run mode "
        "and returns a preview of the first 10 rows plus counts — set dryRun=false "
        "to commit. Requires user approval before committing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute or workspace-relative path to the exported "
                               "CSV or Excel file (.csv, .xlsx, .xls).",
            },
            "dryRun": {
                "type": "boolean",
                "description": "When true (default), parse and preview only — do not "
                               "write to the backend. Set to false to commit the import.",
            },
        },
        "required": ["path"],
    },
}
