"""Hermes tool handlers for PackTrak.

Each handler takes a dict of LLM-supplied args, returns a JSON string, and never
raises — errors are returned as {"error": "..."}. The store path is resolved through
`_get_store`, a single injectable hook that tests override to use a temp file.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .changes import detect_change
from .freshness import is_blank, fetch_latest
from .providers import get_provider, ProviderError, TrackingNotFoundError, CarrierAPIError
from .store import ShipmentStore

# Default runtime store: data/shipments.json next to this module.
_DEFAULT_STORE_PATH = Path(__file__).parent / "data" / "shipments.json"

_GET_STATUS_RETRIES = int(os.environ.get("PACKTRACK_GET_STATUS_RETRIES", "1"))
_GET_STATUS_RETRY_DELAY = float(os.environ.get("PACKTRACK_GET_STATUS_RETRY_DELAY", "3.0"))
_CHECK_REFRESH_WAIT = float(os.environ.get("PACKTRACK_CHECK_REFRESH_WAIT", "12.0"))


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _get_store() -> ShipmentStore:
    """Return the active store. Overridden in tests to point at a temp file."""
    return ShipmentStore(_DEFAULT_STORE_PATH)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _public(record: dict) -> dict:
    return {
        "tracking_number": record["tracking_number"],
        "carrier": record.get("carrier"),
        "label": record.get("label"),
        "added_at": record.get("added_at"),
        "monitor": record.get("monitor", True),
        "last_status": record.get("last_status"),
    }


def _live_response(tracking_number: str, record: dict, result) -> dict:
    return {
        "success": True,
        "tracking_number": tracking_number,
        "carrier": result.carrier or record.get("carrier"),
        "status": result.status,
        "sub_status": result.sub_status,
        "raw_status": result.raw_status,
        "detail": result.detail,
        "provider": result.provider,
    }


def _stored_response(tracking_number: str, record: dict, note: str) -> dict:
    return {
        "success": True,
        "tracking_number": tracking_number,
        "carrier": record.get("carrier"),
        "status": record.get("last_status"),
        "last_checked_at": record.get("last_checked_at"),
        "provider": "17track",
        "stale": True,
        "message": f"Status as of last check; {note}.",
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

        record = store.add(tracking_number, carrier=args.get("carrier"),
                           label=args.get("label"))

        # Best-effort warm-up: register the number with the carrier backend and
        # capture initial data if it's already available. Never fails the add.
        has_data = False
        try:
            res = get_provider(record["carrier"]).fetch_status(
                tracking_number, record["carrier"])
            patch = {"last_status": res.status, "last_events_hash": res.events_hash,
                     "last_checked_at": _utcnow()}
            if res.carrier and not record.get("carrier"):
                patch["carrier"] = res.carrier
            if res.status == "delivered":
                patch["monitor"] = False
            record = store.update(tracking_number, **patch) or record
            has_data = res.status not in (None, "", "unknown")
        except ProviderError:
            pass  # transient/no-data — warm-up is best-effort

        message = ("tracking started" if has_data else
                   "tracking started — initial data may take a few minutes to appear")
        return json.dumps({"success": True, "shipment": _public(record),
                           "message": message})
    except Exception as exc:
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

        prev = record.get("last_status")
        prev_real = bool(prev) and prev != "unknown"

        result = None
        try:
            result = fetch_latest(
                get_provider(record["carrier"]), tracking_number, record["carrier"],
                retries=_GET_STATUS_RETRIES, delay=_GET_STATUS_RETRY_DELAY, sleep=_sleep,
            )
        except TrackingNotFoundError:
            result = None
        except CarrierAPIError as exc:
            if prev_real:
                return json.dumps(_stored_response(
                    tracking_number, record,
                    f"live refresh failed ({exc}); showing last known status"))
            return json.dumps({"error": str(exc)})

        if not is_blank(result):
            patch = {"last_status": result.status, "last_events_hash": result.events_hash,
                     "last_checked_at": _utcnow()}
            if result.carrier and not record.get("carrier"):
                patch["carrier"] = result.carrier
            if result.status == "delivered":
                patch["monitor"] = False
            _get_store().update(tracking_number, **patch)
            return json.dumps(_live_response(tracking_number, record, result))

        if prev_real:  # Rule 1 — never show a spurious "unknown"
            return json.dumps(_stored_response(
                tracking_number, record,
                "a fresh re-sync is in progress — ask again in a few seconds for the latest"))

        # Rule 2 — genuinely no data yet
        return json.dumps({
            "success": True,
            "tracking_number": tracking_number,
            "carrier": record.get("carrier"),
            "status": "unknown",
            "pending": True,
            "message": ("No data yet — 17track is fetching this from the carrier "
                        "(it may be brand-new, or just needs a moment). Ask again in ~10 seconds."),
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


def shipment_check_updates(args: dict, **kwargs) -> str:
    try:
        store = _get_store()
        monitored = [r for r in store.list() if r.get("monitor", True)]
        if not monitored:
            return json.dumps({"success": True, "checked": 0, "changes": [],
                               "delivered": []})

        numbers = [r["tracking_number"] for r in monitored]
        try:
            results = get_provider().fetch_many(numbers)
        except ProviderError as exc:
            return json.dumps({"success": True, "checked": 0, "changes": [],
                               "delivered": [], "note": str(exc)})

        changes, delivered = [], []
        for record in monitored:
            result = results.get(record["tracking_number"])
            if result is None:
                continue  # no data this round — keep state
            change = detect_change(record, result)
            store.update(record["tracking_number"], **change.new_state)
            if change.changed and change.summary:
                changes.append(change.summary)
            if change.new_state.get("monitor") is False:
                delivered.append(record["tracking_number"])

        return json.dumps({"success": True, "checked": len(monitored),
                           "changes": changes, "delivered": delivered})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def shipment_set_monitoring(args: dict, **kwargs) -> str:
    try:
        tracking_number = (args.get("tracking_number") or "").strip()
        if not tracking_number:
            return json.dumps({"error": "tracking_number is required"})
        enabled = bool(args.get("enabled", True))
        store = _get_store()
        if store.find(tracking_number) is None:
            return json.dumps({"error": "tracking_number not tracked"})
        store.update(tracking_number, monitor=enabled)
        return json.dumps({"success": True, "tracking_number": tracking_number,
                           "monitor": enabled})
    except Exception as exc:
        return json.dumps({"error": str(exc)})
