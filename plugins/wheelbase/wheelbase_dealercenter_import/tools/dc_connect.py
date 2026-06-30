"""dc_connect — confirm browser CDP readiness for DealerCenter automation.

Checks whether BROWSER_CDP_URL is configured.  Returns a status dict
plus human-readable setup instructions so the agent can guide the user
through enabling CDP before calling dc_export_historic.
"""

import os

from wheelbase_sdk import ok


def dc_connect(args: dict, **kwargs) -> str:  # noqa: ARG001
    cdp_url = os.environ.get("BROWSER_CDP_URL", "")
    configured = bool(cdp_url)
    instructions = (
        "Launch Chrome or Chromium with --remote-debugging-port=9222, "
        "navigate to DealerCenter and log in, then set "
        "BROWSER_CDP_URL=http://localhost:9222 in your environment and call "
        "dc_connect again to confirm readiness."
    )
    return ok({
        "cdpConfigured": configured,
        "cdpUrl": cdp_url or None,
        "instructions": instructions,
    })
