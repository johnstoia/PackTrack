"""Shared base for OAuth client-credentials carrier providers.

Subclasses supply endpoints, credentials, response parsing, and status mapping;
this base handles token fetch + caching (keyed by carrier name, with an expiry
margin) and a single token refresh on a 401.
"""
from __future__ import annotations

import time
from typing import Optional

from . import CarrierAPIError, StatusResult, TrackingProvider
from . import http_client

# Module-level token cache: {carrier_name: (access_token, expiry_epoch_seconds)}
_TOKEN_CACHE: dict = {}
_EXPIRY_MARGIN_SECONDS = 60


class OAuthCarrierProvider(TrackingProvider):
    # Subclasses set these:
    name: str = ""               # also the token-cache key
    token_path: str = ""         # e.g. "/oauth2/v3/token"
    oauth_scope: Optional[str] = None

    # --- subclass hooks ---
    def _base_url(self) -> str:
        raise NotImplementedError

    def _credentials(self) -> tuple:
        """Return (client_id, client_secret) or raise CredentialsMissingError."""
        raise NotImplementedError

    def _tracking_url(self, tracking_number: str) -> str:
        raise NotImplementedError

    def _extract_raw_status(self, payload: dict) -> str:
        raise NotImplementedError

    # normalize_status remains abstract (from TrackingProvider) — subclass implements.

    # --- shared machinery ---
    def _fetch_token(self) -> str:
        client_id, client_secret = self._credentials()
        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if self.oauth_scope:
            data["scope"] = self.oauth_scope
        payload = http_client.post_form(self._base_url() + self.token_path, data)
        token = payload.get("access_token")
        if not token:
            raise CarrierAPIError("no access_token in OAuth token response")
        expires_in = int(payload.get("expires_in", 3600))
        expiry = time.time() + max(0, expires_in - _EXPIRY_MARGIN_SECONDS)
        _TOKEN_CACHE[self.name] = (token, expiry)
        return token

    def _get_token(self, force_refresh: bool = False) -> str:
        if not force_refresh:
            cached = _TOKEN_CACHE.get(self.name)
            if cached and cached[1] > time.time():
                return cached[0]
        return self._fetch_token()

    def fetch_status(self, tracking_number: str, carrier: Optional[str]) -> StatusResult:
        try:
            return self._do_fetch(tracking_number, force_refresh=False)
        except CarrierAPIError as exc:
            if getattr(exc, "status_code", None) == 401:
                return self._do_fetch(tracking_number, force_refresh=True)
            raise

    def _do_fetch(self, tracking_number: str, force_refresh: bool) -> StatusResult:
        token = self._get_token(force_refresh=force_refresh)
        headers = {"Authorization": f"Bearer {token}"}
        payload = http_client.get_json(self._tracking_url(tracking_number), headers=headers)
        raw = self._extract_raw_status(payload)
        return StatusResult(
            status=self.normalize_status(raw),
            raw_status=raw,
            provider=self.name,
        )
