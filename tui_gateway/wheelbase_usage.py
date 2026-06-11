"""Post-turn usage snapshot -> backend internal sink (spec §9).

Reads cumulative token/cost numbers from the local sessions table and
POSTs an upsert row. Fire-and-forget: metering must never break a turn.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request

log = logging.getLogger(__name__)


def report_session_usage(db, session_key: str, identity) -> None:
    base = os.environ.get("WHEELBASE_INTERNAL_API", "").rstrip("/")
    token = os.environ.get("WHEELBASE_GATEWAY_TOKEN", "")
    if not base or identity is None or db is None:
        return

    def _send() -> None:
        try:
            row = db.get_session(session_key)
            if not row:
                return
            payload = {
                "session_id": session_key,
                "user_id": identity.user_id,
                "tenant_id": identity.tenant_id or None,
                "dealership_id": identity.dealership_id or None,
                "model": row.get("model"),
                "input_tokens": int(row.get("input_tokens") or 0),
                "output_tokens": int(row.get("output_tokens") or 0),
                "cache_read_tokens": int(row.get("cache_read_tokens") or 0),
                "cache_write_tokens": int(row.get("cache_write_tokens") or 0),
                "reasoning_tokens": int(row.get("reasoning_tokens") or 0),
                "cost_usd": row.get("estimated_cost_usd"),
                "message_count": int(row.get("message_count") or 0),
            }
            req = urllib.request.Request(
                base + "/internal/agent/usage",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "X-Gateway-Token": token},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as exc:
            log.warning("usage report failed for %s: %s", session_key, exc)

    threading.Thread(target=_send, name="wb-usage-report", daemon=True).start()
