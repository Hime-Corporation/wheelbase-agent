"""wheelbase-core plugin — registration.

Tools are always registered (visible to the model); each handler returns a clear
"sign in" result when there is no session, so the agent can relay it.
"""

from . import schemas
from .tools import get_car as get_car_tool
from .tools import inventory_search as inventory_search_tool
from .tools import update_inventory_status as update_inventory_status_tool
from .tools import list_runlists as list_runlists_tool
from .tools import get_runlist_cars as get_runlist_cars_tool
from .tools import assess_runlist as assess_runlist_tool
from .tools import archive_runlist_cars as archive_runlist_cars_tool
from .tools import create_work_order as create_work_order_tool
from .tools import get_work_order as get_work_order_tool
from .tools import delete_work_order as delete_work_order_tool
from .tools import create_inspection_note as create_inspection_note_tool
from .tools import bulk_inspect as bulk_inspect_tool
from .tools import list_vendors as list_vendors_tool
from .tools import get_vendor as get_vendor_tool
from .tools import send_to_vendor as send_to_vendor_tool
from .tools import generate_demand_score as generate_demand_score_tool


def register(ctx):
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
        name="create_work_order",
        toolset="wheelbase",
        schema=schemas.CREATE_WORK_ORDER,
        handler=create_work_order_tool.create_work_order,
    )
    ctx.register_tool(
        name="get_work_order",
        toolset="wheelbase",
        schema=schemas.GET_WORK_ORDER,
        handler=get_work_order_tool.get_work_order,
    )
    ctx.register_tool(
        name="delete_work_order",
        toolset="wheelbase",
        schema=schemas.DELETE_WORK_ORDER,
        handler=delete_work_order_tool.delete_work_order,
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
