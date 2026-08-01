"""Token-safe timing trace for the executable Wheelbase gateway fixture.

The production gateway is silent unless ``WHEELBASE_GATEWAY_FIXTURE_TRACE_FILE``
names an absolute path. The fixture harness can opt in during an integration
diagnostic without logging envelopes, tokens, request parameters, or raw
tenant/user identifiers.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

_TRACE_ENV = "WHEELBASE_GATEWAY_FIXTURE_TRACE_FILE"
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def emit_fixture_trace(event: str, **fields: Any) -> None:
    """Append one bounded JSONL event when the fixture trace is enabled."""
    raw_path = os.environ.get(_TRACE_ENV, "")
    path = Path(raw_path) if raw_path else None
    if path is None or not path.is_absolute() or not _FIELD_RE.fullmatch(event):
        return

    record: dict[str, Any] = {
        "event": event,
        "pid": os.getpid(),
        "time_unix_ms": int(time.time() * 1000),
    }
    for key, value in fields.items():
        if not _FIELD_RE.fullmatch(str(key)):
            continue
        if isinstance(value, bool) or value is None:
            record[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            record[key] = value
        elif isinstance(value, str):
            record[key] = value[:96]

    data = (json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n").encode()
    if len(data) > 4096:
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError:
        return
    try:
        os.write(fd, data)
    except OSError:
        pass
    finally:
        os.close(fd)


__all__ = ["emit_fixture_trace"]
