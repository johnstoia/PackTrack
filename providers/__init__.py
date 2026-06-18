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

    status: str       # one of CANONICAL_STATUSES
    raw_status: str   # what the provider originally reported
    provider: str     # provider name, e.g. "mock"


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


def get_provider(name: str = "mock") -> TrackingProvider:
    """Return the active provider instance by name. Defaults to the mock provider."""
    if name == "mock":
        from .mock import MockProvider

        return MockProvider()
    raise ValueError(f"unknown provider: {name}")
