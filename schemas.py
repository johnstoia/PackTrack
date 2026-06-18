"""OpenAI-format tool schemas for the PackTrak Hermes plugin.

Descriptions are written for the LLM: they explain when each tool should be called.
"""

ADD_TRACKING = {
    "name": "shipment_add_tracking",
    "description": (
        "Start tracking a package by its tracking number. Optionally include the "
        "carrier (e.g. 'ups', 'fedex') and a human-friendly label. Rejects empty "
        "tracking numbers and numbers that are already tracked."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tracking_number": {
                "type": "string",
                "description": "The carrier tracking number to track.",
            },
            "carrier": {
                "type": "string",
                "description": "Optional carrier slug, e.g. 'ups', 'fedex', 'usps'.",
            },
            "label": {
                "type": "string",
                "description": "Optional human-friendly label for this shipment.",
            },
        },
        "required": ["tracking_number"],
    },
}

GET_STATUS = {
    "name": "shipment_get_status",
    "description": (
        "Get the current delivery status of a tracked shipment by its tracking "
        "number. Returns a normalized status such as in_transit or delivered."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tracking_number": {
                "type": "string",
                "description": "The tracking number of an already-tracked shipment.",
            },
        },
        "required": ["tracking_number"],
    },
}

LIST_TRACKED = {
    "name": "shipment_list_tracked",
    "description": "List all shipments currently being tracked.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

REMOVE_TRACKING = {
    "name": "shipment_remove_tracking",
    "description": (
        "Stop tracking a shipment and remove it from the tracked list, by its "
        "tracking number."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tracking_number": {
                "type": "string",
                "description": "The tracking number of the shipment to remove.",
            },
        },
        "required": ["tracking_number"],
    },
}
