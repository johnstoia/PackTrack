"""USPS Tracking v3 provider.

Endpoints (host root configurable via USPS_API_BASE):
- token:    POST {base}/oauth2/v3/token   (client_credentials, scope "tracking")
- tracking: GET  {base}/tracking/v3/tracking/{number}?expand=DETAIL  (Bearer token)

``expand=DETAIL`` is required — the default SUMMARY response has no status field.
Status is read from statusCategory / status / statusSummary / latest event, then
mapped to a canonical value by case-insensitive keyword match.
"""
from __future__ import annotations

import os

from . import CredentialsMissingError
from .oauth_carrier import OAuthCarrierProvider

_DEFAULT_BASE = "https://apis.usps.com"

# (keyword, canonical) — ordered so more-specific phrases match before generic ones.
_STATUS_KEYWORDS = (
    ("out for delivery", "out_for_delivery"),
    ("available for pickup", "available_for_pickup"),
    ("delivery attempt", "delivery_attempted"),
    ("notice left", "delivery_attempted"),
    ("attempted", "delivery_attempted"),
    ("return to sender", "returned"),
    ("returned", "returned"),
    ("delivered", "delivered"),
    ("pre-shipment", "pending"),
    ("shipping label created", "pending"),
    ("awaiting item", "pending"),
    ("in transit", "in_transit"),
    ("arrived", "in_transit"),
    ("departed", "in_transit"),
    ("accepted", "in_transit"),
    ("picked up", "in_transit"),
    ("alert", "exception"),
    ("exception", "exception"),
    ("received", "info_received"),
)


class USPSProvider(OAuthCarrierProvider):
    name = "usps"
    token_path = "/oauth2/v3/token"
    oauth_scope = "tracking"

    def _base_url(self) -> str:
        return os.environ.get("USPS_API_BASE", _DEFAULT_BASE).rstrip("/")

    def _credentials(self) -> tuple:
        key = os.environ.get("USPS_CONSUMER_KEY")
        secret = os.environ.get("USPS_CONSUMER_SECRET")
        if not key or not secret:
            raise CredentialsMissingError(
                "USPS credentials not configured: set USPS_CONSUMER_KEY and "
                "USPS_CONSUMER_SECRET"
            )
        return key, secret

    def _tracking_url(self, tracking_number: str) -> str:
        return f"{self._base_url()}/tracking/v3/tracking/{tracking_number}?expand=DETAIL"

    def _extract_raw_status(self, payload: dict) -> str:
        for field in ("statusCategory", "status", "statusSummary"):
            value = payload.get(field)
            if value:
                return str(value)
        events = payload.get("trackingEvents") or []
        if isinstance(events, list) and events:
            first = events[0]
            if isinstance(first, dict) and first.get("eventType"):
                return str(first["eventType"])
        return ""

    def normalize_status(self, raw: str) -> str:
        text = (raw or "").strip().lower()
        if not text:
            return "unknown"
        for keyword, canonical in _STATUS_KEYWORDS:
            if keyword in text:
                return canonical
        return "unknown"
