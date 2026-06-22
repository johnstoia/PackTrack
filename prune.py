"""Pure selection logic for the delivered/stale lifecycle.

`select_prunable` decides which shipment records are finished and should be hard
-deleted: delivered packages past a grace period, and undelivered-but-monitored
packages that haven't changed status for a long time. No I/O — the caller persists
backfills and performs the deletions, so this is trivially testable with an injected
clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Tuple

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, _TS_FMT).replace(tzinfo=timezone.utc)


def select_prunable(
    records: List[dict],
    now: datetime,
    *,
    delivered_days: float,
    stale_days: float,
) -> Tuple[List[dict], List[dict]]:
    """Split records into (to_backfill, to_prune).

    - ``to_prune``: records eligible for hard deletion right now.
    - ``to_backfill``: records with a missing or unparseable ``last_change_at`` — the
      caller should persist ``last_change_at = now`` for these and must NOT delete
      them this round (gives every pre-existing record a fresh grace clock so nothing
      is surprise-deleted on the first sweep after this feature ships).

    Records that are neither are simply kept (no action).

    Rules (``age = now - last_change_at``):
      1. delivered and (``delivered_days`` <= 0 or age > ``delivered_days``) -> prune
         (regardless of the ``monitor`` flag). ``delivered_days = 0`` drives the
         manual "remove all delivered now" path.
      2. else if still monitored, undelivered, and age > ``stale_days`` -> prune.
      3. otherwise keep (includes monitor-off undelivered records the user is
         deliberately holding).
    """
    to_backfill: List[dict] = []
    to_prune: List[dict] = []

    for rec in records:
        ts = rec.get("last_change_at")
        if not ts:
            to_backfill.append(rec)
            continue
        try:
            changed_at = _parse(ts)
        except (ValueError, TypeError):
            to_backfill.append(rec)
            continue

        age = now - changed_at
        status = rec.get("last_status")

        if status == "delivered":
            if delivered_days <= 0 or age > timedelta(days=delivered_days):
                to_prune.append(rec)
        elif rec.get("monitor", True) and age > timedelta(days=stale_days):
            to_prune.append(rec)

    return to_backfill, to_prune
