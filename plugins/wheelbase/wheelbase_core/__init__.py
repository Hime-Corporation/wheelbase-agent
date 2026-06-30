"""wheelbase-core plugin — registration.

Tools are always registered (visible to the model); each handler returns a clear
"sign in" result when there is no session, so the agent can relay it.
"""

from . import schemas
from . import hooks as _hooks
from .tools import get_car as get_car_tool
from .tools import inventory_search as inventory_search_tool
from .tools import update_inventory_status as update_inventory_status_tool
from .tools import list_runlists as list_runlists_tool
from .tools import get_runlist_cars as get_runlist_cars_tool
from .tools import assess_runlist as assess_runlist_tool
from .tools import archive_runlist_cars as archive_runlist_cars_tool
from .tools import create_work_item as create_work_item_tool
from .tools import get_work_item as get_work_item_tool
from .tools import delete_work_item as delete_work_item_tool
from .tools import list_inventory_statuses as list_inventory_statuses_tool
from .tools import create_inspection_note as create_inspection_note_tool
from .tools import bulk_inspect as bulk_inspect_tool
from .tools import list_vendors as list_vendors_tool
from .tools import get_vendor as get_vendor_tool
from .tools import send_to_vendor as send_to_vendor_tool
from .tools import generate_demand_score as generate_demand_score_tool
from .tools import get_recon_board as get_recon_board_tool
from .tools import start_recon as start_recon_tool
from .tools import recon_stage_tools as recon_stage_tools_tool
from .tools import add_work_item_comment as add_work_item_comment_tool
from .tools import query_work as query_work_tool
from .tools import inventory_stats as inventory_stats_tool


def register(ctx):
    # Approval-gating hook for destructive wheelbase tools.
    # When WHEELBASE_APPROVAL_GATE=1 this intercepts send_to_vendor and
    # delete_work_order and routes them through the gateway approval flow.
    # When the flag is OFF (default) the hook is a no-op and prod tool
    # execution is completely unaffected.
    ctx.register_hook("pre_tool_call", _hooks._pre_tool_call)

    ctx.register_tool(
        name="get_car",
        toolset="wheelbase",
        schema=schemas.GET_CAR,
        handler=get_car_tool.get_car,
    )
    ctx.register_tool(
        name="inventory_search",
        toolset="wheelbase",
        schema=schemas.INVENTORY_SEARCH,
        handler=inventory_search_tool.inventory_search,
    )
    ctx.register_tool(
        name="update_inventory_status",
        toolset="wheelbase",
        schema=schemas.UPDATE_INVENTORY_STATUS,
        handler=update_inventory_status_tool.update_inventory_status,
    )
    ctx.register_tool(
        name="list_runlists",
        toolset="wheelbase",
        schema=schemas.LIST_RUNLISTS,
        handler=list_runlists_tool.list_runlists,
    )
    ctx.register_tool(
        name="get_runlist_cars",
        toolset="wheelbase",
        schema=schemas.GET_RUNLIST_CARS,
        handler=get_runlist_cars_tool.get_runlist_cars,
    )
    ctx.register_tool(
        name="assess_runlist",
        toolset="wheelbase",
        schema=schemas.ASSESS_RUNLIST,
        handler=assess_runlist_tool.assess_runlist,
    )
    ctx.register_tool(
        name="archive_runlist_cars",
        toolset="wheelbase",
        schema=schemas.ARCHIVE_RUNLIST_CARS,
        handler=archive_runlist_cars_tool.archive_runlist_cars,
    )
    ctx.register_tool(
        name="create_work_item",
        toolset="wheelbase",
        schema=schemas.CREATE_WORK_ITEM,
        handler=create_work_item_tool.create_work_item,
    )
    ctx.register_tool(
        name="get_work_item",
        toolset="wheelbase",
        schema=schemas.GET_WORK_ITEM,
        handler=get_work_item_tool.get_work_item,
    )
    ctx.register_tool(
        name="delete_work_item",
        toolset="wheelbase",
        schema=schemas.DELETE_WORK_ITEM,
        handler=delete_work_item_tool.delete_work_item,
    )
    ctx.register_tool(
        name="list_inventory_statuses",
        toolset="wheelbase",
        schema=schemas.LIST_INVENTORY_STATUSES,
        handler=list_inventory_statuses_tool.list_inventory_statuses,
    )
    ctx.register_tool(
        name="create_inspection_note",
        toolset="wheelbase",
        schema=schemas.CREATE_INSPECTION_NOTE,
        handler=create_inspection_note_tool.create_inspection_note,
    )
    ctx.register_tool(
        name="bulk_inspect",
        toolset="wheelbase",
        schema=schemas.BULK_INSPECT,
        handler=bulk_inspect_tool.bulk_inspect,
    )
    ctx.register_tool(
        name="list_vendors",
        toolset="wheelbase",
        schema=schemas.LIST_VENDORS,
        handler=list_vendors_tool.list_vendors,
    )
    ctx.register_tool(
        name="get_vendor",
        toolset="wheelbase",
        schema=schemas.GET_VENDOR,
        handler=get_vendor_tool.get_vendor,
    )
    ctx.register_tool(
        name="send_to_vendor",
        toolset="wheelbase",
        schema=schemas.SEND_TO_VENDOR,
        handler=send_to_vendor_tool.send_to_vendor,
    )
    ctx.register_tool(
        name="generate_demand_score",
        toolset="wheelbase",
        schema=schemas.GENERATE_DEMAND_SCORE,
        handler=generate_demand_score_tool.generate_demand_score,
    )
    ctx.register_tool(
        name="get_recon_board",
        toolset="wheelbase",
        schema=schemas.GET_RECON_BOARD,
        handler=get_recon_board_tool.get_recon_board,
    )
    ctx.register_tool(
        name="start_recon",
        toolset="wheelbase",
        schema=schemas.START_RECON,
        handler=start_recon_tool.start_recon,
    )
    ctx.register_tool(
        name="complete_stage",
        toolset="wheelbase",
        schema=schemas.COMPLETE_STAGE,
        handler=recon_stage_tools_tool.complete_stage,
    )
    ctx.register_tool(
        name="update_stage",
        toolset="wheelbase",
        schema=schemas.UPDATE_STAGE,
        handler=recon_stage_tools_tool.update_stage,
    )
    ctx.register_tool(
        name="create_finding",
        toolset="wheelbase",
        schema=schemas.CREATE_FINDING,
        handler=recon_stage_tools_tool.create_finding,
    )
    ctx.register_tool(
        name="add_work_item_comment",
        toolset="wheelbase",
        schema=schemas.ADD_WORK_ITEM_COMMENT,
        handler=add_work_item_comment_tool.add_work_item_comment,
    )
    ctx.register_tool(
        name="query_work",
        toolset="wheelbase",
        schema=schemas.QUERY_WORK,
        handler=query_work_tool.query_work,
    )
    ctx.register_tool(
        name="get_inventory_stats",
        toolset="wheelbase",
        schema=schemas.GET_INVENTORY_STATS,
        handler=inventory_stats_tool.get_inventory_stats,
    )
    ctx.register_tool(
        name="get_inventory_filter_options",
        toolset="wheelbase",
        schema=schemas.GET_INVENTORY_FILTER_OPTIONS,
        handler=inventory_stats_tool.get_inventory_filter_options,
    )
