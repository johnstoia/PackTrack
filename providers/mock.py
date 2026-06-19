"""Deterministic mock tracking provider for the MVP.

`fetch_status` derives a stable status from the tracking number so the same number
always returns the same status (repeatable for tests) while different numbers vary.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from . import CANONICAL_STATUSES, StatusResult, TrackingProvider


class MockProvider(TrackingProvider):
    name = "mock"

    def normalize_status(self, raw: str, sub_status: Optional[str] = None) -> str:
        candidate = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
        if candidate in CANONICAL_STATUSES:
            return candidate
        return "unknown"

    def fetch_status(self, tracking_number: str, carrier: Optional[str]) -> StatusResult:
        digest = hashlib.sha256(tracking_number.encode("utf-8")).hexdigest()
        index = int(digest, 16) % len(CANONICAL_STATUSES)
        raw = CANONICAL_STATUSES[index]
        return StatusResult(
            status=self.normalize_status(raw),
            raw_status=raw,
            provider=self.name,
        )
