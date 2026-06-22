"""JSON-file persistence for tracked shipments.

The file path is injected (not hardcoded) so tests can point the store at a temp
file. The store holds only user-supplied data; status is computed on demand by the
provider layer and never persisted here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


class ShipmentStore:
    def __init__(self, path):
        self._path = Path(path)

    def _read(self) -> dict:
        if not self._path.exists():
            return {"shipments": []}
        with self._path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

    def list(self) -> List[dict]:
        return self._read()["shipments"]

    def find(self, tracking_number: str) -> Optional[dict]:
        for record in self.list():
            if record["tracking_number"] == tracking_number:
                return record
        return None

    def add(self, tracking_number: str, carrier: Optional[str] = None,
            label: Optional[str] = None) -> dict:
        data = self._read()
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        record = {
            "tracking_number": tracking_number,
            "carrier": carrier,
            "label": label,
            "added_at": created_at,
            "monitor": True,
            "last_status": None,
            "last_events_hash": None,
            "last_checked_at": None,
            # Activity clock for the lifecycle/prune logic; bumped only on real status
            # changes. Starts at creation so an undelivered package's stale timer runs
            # from when it was added.
            "last_change_at": created_at,
        }
        data["shipments"].append(record)
        self._write(data)
        return record

    def update(self, tracking_number: str, **fields) -> Optional[dict]:
        """Patch fields on a record and persist. Returns the record, or None if
        the tracking number isn't present."""
        data = self._read()
        for record in data["shipments"]:
            if record["tracking_number"] == tracking_number:
                record.update(fields)
                self._write(data)
                return record
        return None

    def remove(self, tracking_number: str) -> bool:
        data = self._read()
        before = len(data["shipments"])
        data["shipments"] = [
            r for r in data["shipments"] if r["tracking_number"] != tracking_number
        ]
        if len(data["shipments"]) == before:
            return False
        self._write(data)
        return True
