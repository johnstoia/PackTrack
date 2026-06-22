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
from .prune import select_prunable
from .providers import get_provider, ProviderError, TrackingNotFoundError, CarrierAPIError
from .store import ShipmentStore

# Default runtime store: data/shipments.json next to this module.
_DEFAULT_STORE_PATH = Path(__file__).parent / "data" / "shipments.json"

_GET_STATUS_RETRIES = int(os.environ.get("PACKTRACK_GET_STATUS_RETRIES", "1"))
_GET_STATUS_RETRY_DELAY = float(os.environ.get("PACKTRACK_GET_STATUS_RETRY_DELAY", "3.0"))
_CHECK_REFRESH_WAIT = float(os.environ.get("PACKTRACK_CHECK_REFRESH_WAIT", "12.0"))
_DELIVERED_PRUNE_DAYS = float(os.environ.get("PACKTRACK_DELIVERED_PRUNE_DAYS", "3"))
_STALE_PRUNE_DAYS = float(os.environ.get("PACKTRACK_STALE_PRUNE_DAYS", "30"))


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
            now = _utcnow()
            patch = {"last_status": res.status, "last_events_hash": res.events_hash,
                     "last_checked_at": now}
            if res.carrier and not record.get("carrier"):
                patch["carrier"] = res.carrier
            if res.status == "delivered":
                patch["monitor"] = False
            has_data = res.status not in (None, "", "unknown")
            if has_data:
                # Real status captured at add time — start the activity clock now
                # (store.add seeded it to added_at for the no-data case).
                patch["last_change_at"] = now
            record = store.update(tracking_number, **patch) or record
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
            now = _utcnow()
            patch = {"last_status": result.status, "last_events_hash": result.events_hash,
                     "last_checked_at": now}
            if result.carrier and not record.get("carrier"):
                patch["carrier"] = result.carrier
            if result.status == "delivered":
                patch["monitor"] = False
            # Bump the activity clock only when the status actually moved, mirroring
            # detect_change so the lifecycle timer measures change, not poll, time.
            prev_hash = record.get("last_events_hash")
            if result.events_hash is not None:
                status_changed = result.events_hash != prev_hash
            else:
                status_changed = result.status != prev
            if status_changed:
                patch["last_change_at"] = now
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


def _run_prune(store, *, delivered_days: float, stale_days: float) -> list:
    """Backfill records missing an activity clock and hard-delete finished ones.
    Returns the list of pruned tracking numbers. Pure selection lives in prune.py."""
    to_backfill, to_prune = select_prunable(
        store.list(), datetime.now(timezone.utc),
        delivered_days=delivered_days, stale_days=stale_days)
    if to_backfill:
        now = _utcnow()
        for rec in to_backfill:
            store.update(rec["tracking_number"], last_change_at=now)
    pruned = []
    for rec in to_prune:
        if store.remove(rec["tracking_number"]):
            pruned.append(rec["tracking_number"])
    return pruned


def shipment_check_updates(args: dict, **kwargs) -> str:
    try:
        store = _get_store()
        monitored = [r for r in store.list() if r.get("monitor", True)]
        changes, delivered = [], []
        note = None

        if monitored:
            numbers = [r["tracking_number"] for r in monitored]
            try:
                results = get_provider().fetch_many(numbers)

                # The endpoint returns a cold snapshot and re-syncs async; re-read the
                # blanks once after a wait so monitoring sees fresh data (not perpetual
                # "no changes").
                blanks = [n for n in numbers if is_blank(results.get(n))]
                if blanks:
                    _sleep(_CHECK_REFRESH_WAIT)
                    try:
                        results.update(get_provider().fetch_many(blanks))
                    except ProviderError:
                        pass  # transient — keep what we have

                for record in monitored:
                    result = results.get(record["tracking_number"])
                    if result is None or is_blank(result):
                        continue  # no usable data this round — keep state
                    change = detect_change(record, result)
                    store.update(record["tracking_number"], **change.new_state)
                    if change.changed and change.summary:
                        changes.append(change.summary)
                    if change.new_state.get("monitor") is False:
                        delivered.append(record["tracking_number"])
            except ProviderError as exc:
                note = str(exc)  # whole-batch failure — still run the prune sweep

        # Prune sweep runs over the FULL store (delivered packages have monitor=False),
        # regardless of whether anything was monitored or the fetch succeeded.
        pruned = _run_prune(store, delivered_days=_DELIVERED_PRUNE_DAYS,
                            stale_days=_STALE_PRUNE_DAYS)

        resp = {"success": True, "checked": len(monitored), "changes": changes,
                "delivered": delivered, "pruned": pruned}
        if note:
            resp["note"] = note
        return json.dumps(resp)
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


def shipment_prune(args: dict, **kwargs) -> str:
    try:
        store = _get_store()
        delivered_now = bool(args.get("delivered_now", False))
        delivered_days = 0 if delivered_now else _DELIVERED_PRUNE_DAYS
        removed = _run_prune(store, delivered_days=delivered_days,
                             stale_days=_STALE_PRUNE_DAYS)
        return json.dumps({"success": True, "removed": removed,
                           "removed_count": len(removed)})
    except Exception as exc:
        return json.dumps({"error": str(exc)})
