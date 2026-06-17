"""wheelbase-inspection plugin — registration.

Render tools are pure validators: they validate the AI-generated payload and
return a normalized DB-ready JSON string via ok()/err(). The Go backend reads
the function_call_output and persists the result with service-role Supabase.
No WheelbaseClient or DB access is performed here.
"""

from wheelbase_sdk import ok, err  # noqa: F401 — exported so tests can monkeypatch

from . import schemas
from .tools import inspection_render_risk_review as _risk_review_mod
from .tools import inspection_render_checklist as _checklist_mod


def register(ctx) -> None:
    ctx.register_tool(
        name="inspection_render_risk_review",
        toolset="wheelbase_inspection",
        schema=schemas.INSPECTION_RENDER_RISK_REVIEW,
        handler=_risk_review_mod.inspection_render_risk_review,
    )
    ctx.register_tool(
        name="inspection_render_checklist",
        toolset="wheelbase_inspection",
        schema=schemas.INSPECTION_RENDER_CHECKLIST,
        handler=_checklist_mod.inspection_render_checklist,
    )
