"""wheelbase-dealercenter-import plugin — registration.

All tools are marker-gated: they are hidden from the model unless the file
`.wheelbase-dealercenter-import-active` exists in the agent workspace directory
(resolved from TERMINAL_CWD or cwd).
"""

from wheelbase_sdk import workspace_dir

from . import schemas
from .tools import dc_connect as dc_connect_mod
from .tools import dc_export_historic as dc_export_historic_mod
from .tools import dc_ingest as dc_ingest_mod

_MARKER_FILENAME = ".wheelbase-dealercenter-import-active"


def _marker_active() -> bool:
    """Return True when the dealercenter-import marker file is present in the workspace."""
    try:
        return (workspace_dir() / _MARKER_FILENAME).exists()
    except Exception:  # noqa: BLE001
        return False


def register(ctx) -> None:
    """Wire schemas to handlers."""
    ctx.register_tool(
        name="dc_connect",
        toolset="wheelbase_dealercenter_import",
        schema=schemas.DC_CONNECT,
        handler=dc_connect_mod.dc_connect,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="dc_export_historic",
        toolset="wheelbase_dealercenter_import",
        schema=schemas.DC_EXPORT_HISTORIC,
        handler=dc_export_historic_mod.dc_export_historic,
        check_fn=_marker_active,
    )
    ctx.register_tool(
        name="dc_ingest",
        toolset="wheelbase_dealercenter_import",
        schema=schemas.DC_INGEST,
        handler=dc_ingest_mod.dc_ingest,
        check_fn=_marker_active,
    )
