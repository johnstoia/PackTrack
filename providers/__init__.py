"""Provider layer for PackTrak.

This module defines the swappable seam for shipment-tracking backends.
`SeventeenTrackProvider` (no-auth 17track) is the default backend; `MockProvider`
is the deterministic offline/test backend selected via the `"mock"` carrier slug.
New backends are added by subclassing `TrackingProvider` and wiring them into
`get_provider`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

# Order matters: the mock provider uses the index for its deterministic mapping.
CANONICAL_STATUSES = (
    "pending",
    "info_received",
    "in_transit",
    "out_for_delivery",
    "delivered",
    "available_for_pickup",
    "delivery_attempted",
    "exception",
    "returned",
    "unknown",
)


@dataclass(frozen=True)
class StatusResult:
    """A normalized status answer from a provider."""

    status: str                      # one of CANONICAL_STATUSES
    raw_status: str                  # what the provider originally reported
    provider: str                    # provider name, e.g. "17track" or "mock"
    carrier: Optional[str] = None    # carrier the provider detected (if any)
    sub_status: Optional[str] = None # finer-grained provider status (if any)
    detail: Optional[str] = None     # latest human-readable event text (if any)
    events_hash: Optional[int] = None  # hash of the events list for change detection


class TrackingProvider(ABC):
    """Contract every tracking backend must satisfy."""

    name: str

    @abstractmethod
    def normalize_status(self, raw: str, sub_status: Optional[str] = None) -> str:
        """Map a provider-specific status string to one of CANONICAL_STATUSES.

        ``sub_status`` is an optional finer-grained provider status some backends
        supply. Unrecognized input must map to "unknown".
        """

    @abstractmethod
    def fetch_status(self, tracking_number: str, carrier: Optional[str]) -> StatusResult:
        """Return the current StatusResult for a tracking number."""

    def fetch_many(self, tracking_numbers, carrier=None) -> "dict[str, StatusResult]":
        """Fetch several numbers. Default loops fetch_status; numbers with no
        tracking data are omitted. Subclasses may override with a batched call."""
        out = {}
        for number in tracking_numbers:
            try:
                out[number] = self.fetch_status(number, carrier)
            except TrackingNotFoundError:
                continue
        return out


class ProviderError(Exception):
    """Base class for provider failures. Handlers convert these to {"error": ...}."""


class TrackingNotFoundError(ProviderError):
    """The tracking number is invalid or has no tracking data."""


class CarrierAPIError(ProviderError):
    """A tracking lookup failed (transport/service error). Treat as transient."""


def get_provider(carrier: Optional[str] = None) -> TrackingProvider:
    """Return the active tracking provider.

    The no-auth 17track backend is the default for every carrier (it auto-detects
    the carrier). The deterministic mock provider is selected ONLY by the explicit
    ``"mock"`` slug (offline/testing). The ``carrier`` argument is otherwise
    metadata only. A missing ``pyseventeentrack`` dependency is handled inside the
    provider (lazy auto-install, then a clear error) — never a silent mock fallback.
    """
    if (carrier or "").strip().lower() == "mock":
        from .mock import MockProvider

        return MockProvider()
    from .seventeentrack import SeventeenTrackProvider

    return SeventeenTrackProvider()
