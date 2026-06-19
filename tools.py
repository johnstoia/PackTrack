"""Hermes tool handlers for PackTrak.

Each handler takes a dict of LLM-supplied args, returns a JSON string, and never
raises — errors are returned as {"error": "..."}. The store path is resolved through
`_get_store`, a single injectable hook that tests override to use a temp file.
"""
from __future__ import annotations

import json
from pathlib import Path

from .providers import get_provider, ProviderError
from .store import ShipmentStore

# Default runtime store: data/shipments.json next to this module.
_DEFAULT_STORE_PATH = Path(__file__).parent / "data" / "shipments.json"


def _get_store() -> ShipmentStore:
    """Return the active store. Overridden in tests to point at a temp file."""
    return ShipmentStore(_DEFAULT_STORE_PATH)


def _public(record: dict) -> dict:
    return {
        "tracking_number": record["tracking_number"],
        "carrier": record["carrier"],
        "label": record["label"],
        "added_at": record["added_at"],
    }


def shipment_add_tracking(args: dict, **kwargs) -> str:
    try:
        raw = args.get("tracking_number")
        tracking_number = (raw or "").strip()
        if not tracking_number:
            return json.dumps({"error": "tracking_number is required"})

        store = _get_store()
        if store.find(tracking_number) is not None:
            return json.dumps({"error": "tracking_number already tracked"})

        carrier = args.get("carrier")
        label = args.get("label")
        record = store.add(tracking_number, carrier=carrier, label=label)
        return json.dumps({"success": True, "shipment": _public(record)})
    except Exception as exc:  # never raise out of a handler
        return json.dumps({"error": str(exc)})


def shipment_list_tracked(args: dict, **kwargs) -> str:
    try:
        records = _get_store().list()
        return json.dumps({
            "success": True,
            "count": len(records),
            "shipments": [_public(r) for r in records],
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def shipment_get_status(args: dict, **kwargs) -> str:
    try:
        raw = args.get("tracking_number")
        tracking_number = (raw or "").strip()
        if not tracking_number:
            return json.dumps({"error": "tracking_number is required"})

        record = _get_store().find(tracking_number)
        if record is None:
            return json.dumps({"error": "tracking_number not tracked"})

        try:
            result = get_provider(record["carrier"]).fetch_status(
                tracking_number, record["carrier"]
            )
        except ProviderError as exc:
            return json.dumps({"error": str(exc)})

        return json.dumps({
            "success": True,
            "tracking_number": tracking_number,
            "carrier": record["carrier"],
            "status": result.status,
            "raw_status": result.raw_status,
            "provider": result.provider,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def shipment_remove_tracking(args: dict, **kwargs) -> str:
    try:
        raw = args.get("tracking_number")
        tracking_number = (raw or "").strip()
        if not tracking_number:
            return json.dumps({"error": "tracking_number is required"})

        if _get_store().remove(tracking_number):
            return json.dumps({"success": True, "removed": tracking_number})
        return json.dumps({"error": "tracking_number not tracked"})
    except Exception as exc:
        return json.dumps({"error": str(exc)})
