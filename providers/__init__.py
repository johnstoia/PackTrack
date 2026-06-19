"""Provider layer for PackTrak.

This module defines the swappable seam for shipment-tracking backends. The MVP
ships only `MockProvider`; real providers (AfterShip, EasyPost, 17TRACK) are added
later by subclassing `TrackingProvider` and registering them in `get_provider`.
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


class TrackingProvider(ABC):
    """Contract every tracking backend must satisfy."""

    name: str

    @abstractmethod
    def normalize_status(self, raw: str) -> str:
        """Map a provider-specific status string to one of CANONICAL_STATUSES.

        Unrecognized input must map to "unknown".
        """

    @abstractmethod
    def fetch_status(self, tracking_number: str, carrier: Optional[str]) -> StatusResult:
        """Return the current StatusResult for a tracking number."""


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
