"""Pure change-detection for tracked shipments.

`detect_change` compares a freshly fetched StatusResult against a shipment's stored
state and reports whether there's new activity, a human summary, and the state to
persist. No I/O — trivially testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .providers import StatusResult


@dataclass(frozen=True)
class ChangeResult:
    changed: bool
    summary: Optional[str]
    new_state: dict


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def detect_change(record: dict, result: StatusResult, now: Optional[str] = None) -> ChangeResult:
    now = now or _utcnow()
    prev_status = record.get("last_status")
    prev_hash = record.get("last_events_hash")

    if result.events_hash is not None:
        changed = result.events_hash != prev_hash
    else:
        changed = result.status != prev_status

    # First-populate: previously no usable data, now we have a real status.
    if prev_status in (None, "unknown") and result.status not in (None, "", "unknown"):
        changed = True

    new_state = {
        "last_status": result.status,
        "last_events_hash": result.events_hash,
        "last_checked_at": now,
    }
    if result.carrier and not record.get("carrier"):
        new_state["carrier"] = result.carrier
    if result.status == "delivered":
        new_state["monitor"] = False

    summary = None
    if changed:
        detail = result.detail or result.status
        summary = f"{record['tracking_number']} → {result.status}: {detail}"

    return ChangeResult(changed=changed, summary=summary, new_state=new_state)
