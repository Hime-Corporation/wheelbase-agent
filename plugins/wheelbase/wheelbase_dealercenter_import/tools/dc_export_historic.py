"""dc_export_historic — returns a browser procedure to export DealerCenter historic data.

The tool does NOT operate the browser itself.  Instead it emits a
structured procedure dict that the agent executes step-by-step using its
own browser/CDP tools (navigate_page, click, etc.).  This keeps the tool
side-effect-free and fully testable.
"""

from __future__ import annotations

from wheelbase_sdk import ok


# Base URL for DealerCenter's sold-vehicle report.  The agent will navigate
# here after confirming login via dc_connect.
_DC_REPORT_URL = "https://www.dealercenter.net/reporting/sold"


def dc_export_historic(args: dict, **kwargs) -> str:  # noqa: ARG001
    date_from: str | None = (args.get("dateFrom") or "").strip() or None
    date_to: str | None = (args.get("dateTo") or "").strip() or None

    steps = [
        {
            "step": 1,
            "action": "navigate",
            "description": "Navigate to the DealerCenter sold-vehicle report.",
            "url": _DC_REPORT_URL,
        },
        {
            "step": 2,
            "action": "wait",
            "description": "Wait for the report page to fully load.",
            "selector": "table, .report-container, #reportTable",
            "timeoutMs": 10000,
        },
    ]

    if date_from or date_to:
        steps.append({
            "step": 3,
            "action": "set_date_range",
            "description": "Set the report date range using the From/To date fields.",
            "dateFrom": date_from,
            "dateTo": date_to,
            "selectors": {
                "from": "input[name='dateFrom'], #dateFrom, [placeholder*='From']",
                "to": "input[name='dateTo'], #dateTo, [placeholder*='To']",
                "apply": "button[type='submit'], .apply-filter, #applyFilter",
            },
        })

    next_step = len(steps) + 1

    steps += [
        {
            "step": next_step,
            "action": "set_download_behavior",
            "description": (
                "Configure CDP Page.setDownloadBehavior so the export file "
                "is saved to a known directory rather than triggering a save dialog."
            ),
            "cdpCommand": "Page.setDownloadBehavior",
            "cdpParams": {
                "behavior": "allow",
                "downloadPath": "/tmp/dealercenter-export",
            },
        },
        {
            "step": next_step + 1,
            "action": "click_export",
            "description": "Click the CSV/Excel export button.",
            "selectors": [
                "button[title*='Export'], a[title*='Export']",
                ".export-btn, #exportBtn, [aria-label*='export' i]",
                "button:contains('Export'), a:contains('CSV'), a:contains('Excel')",
            ],
            "fallback": {
                "description": (
                    "If no export button is found, scrape the table via JavaScript "
                    "and save the result manually."
                ),
                "action": "js_scrape",
                "script": (
                    "(() => {"
                    "  const t = document.querySelector('table, #reportTable');"
                    "  if (!t) return null;"
                    "  const rows = [];"
                    "  t.querySelectorAll('tr').forEach(tr => {"
                    "    rows.push(Array.from(tr.querySelectorAll('th,td')).map(c => c.innerText.trim()));"
                    "  });"
                    "  return rows;"
                    "})()"
                ),
                "savePath": "/tmp/dealercenter-export/dc_export_scraped.json",
            },
        },
        {
            "step": next_step + 2,
            "action": "wait_for_download",
            "description": "Wait for the download to complete (up to 60 s).",
            "downloadDir": "/tmp/dealercenter-export",
            "timeoutMs": 60000,
            "note": (
                "After the download completes, pass the file path to dc_ingest "
                "with dryRun=true to preview the parsed rows before committing."
            ),
        },
    ]

    return ok({
        "kind": "dc_export_procedure",
        "reportUrl": _DC_REPORT_URL,
        "dateFrom": date_from,
        "dateTo": date_to,
        "steps": steps,
    })
