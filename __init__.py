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
