"""Helpers for getting fresh data from a read + async-refresh backend.

17track's public endpoint returns an empty snapshot and triggers a background
re-sync; the synced result is readable only on a later read. `fetch_latest` re-reads
a blank result a bounded number of times. The `sleep` dependency is injected so tests
never actually wait.
"""
from __future__ import annotations

import time
from typing import Optional

from .providers import StatusResult, TrackingProvider


def is_blank(result: Optional[StatusResult]) -> bool:
    """True when a result carries no usable status yet."""
    if result is None:
        return True
    return (not result.status) or result.status == "unknown"


def fetch_latest(provider: TrackingProvider, tracking_number: str,
                 carrier: Optional[str], *, retries: int, delay: float,
                 sleep=time.sleep) -> StatusResult:
    """Read live status; if blank, re-read up to ``retries`` times, ``delay`` apart.

    Each read also primes the backend's re-sync. Returns the first non-blank result,
    or the last (blank) one if every attempt is blank. Provider exceptions propagate.
    """
    result = provider.fetch_status(tracking_number, carrier)
    attempts = 0
    while is_blank(result) and attempts < retries:
        sleep(delay)
        result = provider.fetch_status(tracking_number, carrier)
        attempts += 1
    return result
