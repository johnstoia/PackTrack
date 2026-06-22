"""PackTrak — Hermes shipment-tracker plugin entry point.

Hermes calls `register(ctx)` once at startup to wire each tool schema to its handler.
"""
from . import schemas, tools


def register(ctx):
    ctx.register_tool(
        name="shipment_add_tracking",
        toolset="shipment",
        schema=schemas.ADD_TRACKING,
        handler=tools.shipment_add_tracking,
    )
    ctx.register_tool(
        name="shipment_get_status",
        toolset="shipment",
        schema=schemas.GET_STATUS,
        handler=tools.shipment_get_status,
    )
    ctx.register_tool(
        name="shipment_list_tracked",
        toolset="shipment",
        schema=schemas.LIST_TRACKED,
        handler=tools.shipment_list_tracked,
    )
    ctx.register_tool(
        name="shipment_remove_tracking",
        toolset="shipment",
        schema=schemas.REMOVE_TRACKING,
        handler=tools.shipment_remove_tracking,
    )
    ctx.register_tool(
        name="shipment_check_updates",
        toolset="shipment",
        schema=schemas.CHECK_UPDATES,
        handler=tools.shipment_check_updates,
    )
    ctx.register_tool(
        name="shipment_set_monitoring",
        toolset="shipment",
        schema=schemas.SET_MONITORING,
        handler=tools.shipment_set_monitoring,
    )
    ctx.register_tool(
        name="shipment_prune",
        toolset="shipment",
        schema=schemas.PRUNE,
        handler=tools.shipment_prune,
    )
