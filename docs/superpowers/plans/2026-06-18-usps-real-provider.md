# USPS Real Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mock provider with real USPS tracking behind the existing provider seam, and build the shared OAuth/HTTP base + carrier router that UPS/FedEx will reuse.

**Architecture:** `get_provider(carrier)` routes a shipment's stored carrier slug to a provider. A stdlib-`urllib` HTTP helper (`http_client.py`) and an `OAuthCarrierProvider` base (token fetch + module-level cache + 401-retry) carry the shared logic; `USPSProvider` supplies USPS endpoints, response parsing, and status mapping. Credentials come from env vars, gated per-carrier at call time. Status stays computed-on-demand (no store change).

**Tech Stack:** Python 3 (stdlib only — `urllib`, `json`, `time`, `os`), pytest. Branch `feature/real-carrier-providers`.

---

## File Structure

- `providers/__init__.py` — **modify**: add `ProviderError` hierarchy; turn `get_provider` into the carrier router. (Keep `CANONICAL_STATUSES`, `StatusResult`, `TrackingProvider`.)
- `providers/http_client.py` — **create**: `post_form()` + `get_json()`, stdlib HTTP, HTTP-status → typed errors.
- `providers/oauth_carrier.py` — **create**: `OAuthCarrierProvider(TrackingProvider)` base — token fetch + module-level cache w/ expiry, `fetch_status` template with one-time 401 refresh.
- `providers/usps.py` — **create**: `USPSProvider(OAuthCarrierProvider)` — endpoints, credentials, response→raw-status, status keyword map.
- `tools.py` — **modify**: route `shipment_get_status` by carrier; catch `ProviderError`.
- `plugin.yaml` — **modify**: version → `0.2.0`.
- `README.md` — **modify**: add "Carriers & credentials" section.
- `conftest.py` — **modify**: register the `integration` pytest marker.
- `tests/test_tools.py` — **modify**: append unit tests + one opt-in integration test.

> **Import rule (already established):** intra-plugin imports are **relative** (`from . import ...`). The package is bootstrapped as `packtrack` by `conftest.py`, so tests import `from packtrack.providers import ...`.

> **No circular imports:** `providers/__init__.py` does NOT import the new submodules at top level (only `get_provider` lazily imports them). `http_client` imports error classes from `.`; `oauth_carrier` imports `http_client` and error classes; `usps` imports `oauth_carrier`. The chain only loads when `get_provider("usps")` runs.

---

## Task 1: ProviderError hierarchy + carrier router

**Files:**
- Modify: `providers/__init__.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:
```python
from packtrack.providers import (
    ProviderError,
    CredentialsMissingError,
    TrackingNotFoundError,
    CarrierAPIError,
)
from packtrack.mock import MockProvider


def test_provider_error_hierarchy():
    assert issubclass(CredentialsMissingError, ProviderError)
    assert issubclass(TrackingNotFoundError, ProviderError)
    assert issubclass(CarrierAPIError, ProviderError)


def test_carrier_api_error_carries_status_code():
    err = CarrierAPIError("boom", status_code=503)
    assert err.status_code == 503
    assert str(err) == "boom"


def test_router_routes_usps_to_usps_provider():
    from packtrack.providers.usps import USPSProvider
    assert isinstance(get_provider("usps"), USPSProvider)
    assert isinstance(get_provider("USPS"), USPSProvider)  # case-insensitive


def test_router_falls_back_to_mock():
    assert isinstance(get_provider("mock"), MockProvider)
    assert isinstance(get_provider(None), MockProvider)
    assert isinstance(get_provider(""), MockProvider)
    assert isinstance(get_provider("ups"), MockProvider)  # not implemented yet -> mock
```

> Note: the existing top-of-file import line `from packtrack.providers import CANONICAL_STATUSES, StatusResult, TrackingProvider, get_provider` stays. Add the new imports above near the other imports; do not duplicate `get_provider`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py -k "provider_error or carrier_api_error or router" -v`
Expected: FAIL — `ImportError: cannot import name 'ProviderError'` (and `usps` module missing).

- [ ] **Step 3: Modify `providers/__init__.py`**

Replace the existing `get_provider` function (the `def get_provider(...)` block at the bottom) with the error classes and the router below. Keep everything above it (`CANONICAL_STATUSES`, `StatusResult`, `TrackingProvider`) unchanged.

```python
class ProviderError(Exception):
    """Base class for provider failures. Handlers convert these to {"error": ...}."""


class CredentialsMissingError(ProviderError):
    """Required credentials (environment variables) are not configured."""


class TrackingNotFoundError(ProviderError):
    """The carrier reports the tracking number is unknown (HTTP 404)."""


class CarrierAPIError(ProviderError):
    """A carrier API call failed (transport, timeout, or HTTP error)."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def get_provider(carrier: Optional[str] = "mock") -> TrackingProvider:
    """Resolve a carrier slug to a provider instance.

    Known carriers route to their real provider; "mock", None, empty, or any
    unrecognized carrier routes to the mock provider so the plugin stays useful.
    """
    slug = (carrier or "").strip().lower()
    if slug == "usps":
        from .usps import USPSProvider

        return USPSProvider()
    # "mock", "", None, or any not-yet-supported carrier -> mock fallback.
    from .mock import MockProvider

    return MockProvider()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools.py -k "provider_error or carrier_api_error or router" -v`
Expected: PASS (the `usps` import resolves once Task 4 lands; if running this task in isolation before Task 4, the two router/usps tests will error on import — implement Tasks 1–4 in order, then this whole group passes. To keep Task 1 self-contained, run only `-k "provider_error or carrier_api_error"` here and expect PASS.)

Run now: `pytest tests/test_tools.py -k "provider_error or carrier_api_error" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add providers/__init__.py tests/test_tools.py
git commit -m "feat: add ProviderError hierarchy and carrier router"
```
End the commit message with:
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

---

## Task 2: stdlib HTTP client with typed-error mapping

**Files:**
- Create: `providers/http_client.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:
```python
import io
import urllib.error
from packtrack.providers import http_client as http_client_mod


def _fake_resp(payload_bytes):
    class _R:
        def read(self):
            return payload_bytes
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    return _R()


def _http_error(code, body=b""):
    return urllib.error.HTTPError(
        url="http://x", code=code, msg="err", hdrs=None, fp=io.BytesIO(body)
    )


def test_get_json_success(monkeypatch):
    monkeypatch.setattr(http_client_mod.urllib.request, "urlopen",
                        lambda req, timeout=10: _fake_resp(b'{"ok": true}'))
    assert http_client_mod.get_json("http://x", {}) == {"ok": True}


def test_get_json_404_raises_not_found(monkeypatch):
    def boom(req, timeout=10):
        raise _http_error(404)
    monkeypatch.setattr(http_client_mod.urllib.request, "urlopen", boom)
    with pytest.raises(TrackingNotFoundError):
        http_client_mod.get_json("http://x", {})


def test_get_json_500_raises_carrier_error_with_message(monkeypatch):
    body = b'{"error": {"message": "kaboom"}}'
    def boom(req, timeout=10):
        raise _http_error(500, body)
    monkeypatch.setattr(http_client_mod.urllib.request, "urlopen", boom)
    with pytest.raises(CarrierAPIError) as ei:
        http_client_mod.get_json("http://x", {})
    assert ei.value.status_code == 500
    assert "kaboom" in str(ei.value)


def test_get_json_401_sets_status_code(monkeypatch):
    def boom(req, timeout=10):
        raise _http_error(401)
    monkeypatch.setattr(http_client_mod.urllib.request, "urlopen", boom)
    with pytest.raises(CarrierAPIError) as ei:
        http_client_mod.get_json("http://x", {})
    assert ei.value.status_code == 401


def test_post_form_success(monkeypatch):
    captured = {}
    def fake_urlopen(req, timeout=10):
        captured["body"] = req.data
        return _fake_resp(b'{"access_token": "T", "expires_in": 3600}')
    monkeypatch.setattr(http_client_mod.urllib.request, "urlopen", fake_urlopen)
    out = http_client_mod.post_form("http://x/token",
                                    {"grant_type": "client_credentials", "client_id": "k"})
    assert out["access_token"] == "T"
    assert b"grant_type=client_credentials" in captured["body"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py -k "get_json or post_form" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'packtrack.providers.http_client'`.

- [ ] **Step 3: Create `providers/http_client.py`**

```python
"""Standard-library JSON HTTP helpers for carrier providers.

All network I/O lives behind these two functions so providers are testable by
monkeypatching ``urllib.request.urlopen`` (or these functions directly). HTTP
failures are mapped to the typed errors in ``providers/__init__.py``.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from . import CarrierAPIError, TrackingNotFoundError

DEFAULT_TIMEOUT = 10


def post_form(url: str, data: dict, headers: dict = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """POST application/x-www-form-urlencoded; return parsed JSON dict."""
    body = urllib.parse.urlencode(data).encode("utf-8")
    req_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    return _send(req, timeout)


def get_json(url: str, headers: dict = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """GET; return parsed JSON dict."""
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers, method="GET")
    return _send(req, timeout)


def _send(req, timeout: int) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise TrackingNotFoundError("tracking number not found") from exc
        raise CarrierAPIError(_http_error_message(exc), status_code=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CarrierAPIError(f"request to {req.full_url} failed: {exc}") from exc


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    """Pull a human message from a USPS-style ErrorMessage body if present."""
    try:
        data = json.loads(exc.read().decode("utf-8"))
        msg = data.get("error", {}).get("message")
        if msg:
            return f"carrier API error {exc.code}: {msg}"
    except Exception:
        pass
    return f"carrier API error {exc.code}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools.py -k "get_json or post_form" -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add providers/http_client.py tests/test_tools.py
git commit -m "feat: add stdlib HTTP client with typed-error mapping"
```
End with the `Co-Authored-By` trailer.

---

## Task 3: OAuthCarrierProvider base (token cache + 401 retry)

**Files:**
- Create: `providers/oauth_carrier.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:
```python
from packtrack.providers.oauth_carrier import OAuthCarrierProvider, _TOKEN_CACHE


class _FakeCarrier(OAuthCarrierProvider):
    name = "fake"
    token_path = "/oauth/token"
    oauth_scope = "scope1"

    def _base_url(self):
        return "https://fake.test"

    def _credentials(self):
        return ("id", "secret")

    def _tracking_url(self, tracking_number):
        return f"https://fake.test/track/{tracking_number}"

    def _extract_raw_status(self, payload):
        return payload.get("s", "")

    def normalize_status(self, raw):
        return raw if raw in CANONICAL_STATUSES else "unknown"


@pytest.fixture(autouse=True)
def _clear_token_cache():
    _TOKEN_CACHE.clear()
    yield
    _TOKEN_CACHE.clear()


def test_oauth_fetch_status_happy_path(monkeypatch):
    calls = {"token": 0, "track": 0}
    def fake_post(url, data, headers=None, timeout=10):
        calls["token"] += 1
        assert data["grant_type"] == "client_credentials"
        assert data["scope"] == "scope1"
        return {"access_token": "TOK", "expires_in": 3600}
    def fake_get(url, headers=None, timeout=10):
        calls["track"] += 1
        assert headers["Authorization"] == "Bearer TOK"
        return {"s": "delivered"}
    monkeypatch.setattr(http_client_mod, "post_form", fake_post)
    monkeypatch.setattr(http_client_mod, "get_json", fake_get)

    result = _FakeCarrier().fetch_status("XYZ", "fake")
    assert result.status == "delivered"
    assert result.raw_status == "delivered"
    assert result.provider == "fake"
    assert calls == {"token": 1, "track": 1}


def test_oauth_token_is_cached_across_calls(monkeypatch):
    calls = {"token": 0}
    def fake_post(url, data, headers=None, timeout=10):
        calls["token"] += 1
        return {"access_token": "TOK", "expires_in": 3600}
    monkeypatch.setattr(http_client_mod, "post_form", fake_post)
    monkeypatch.setattr(http_client_mod, "get_json",
                        lambda url, headers=None, timeout=10: {"s": "in_transit"})
    c = _FakeCarrier()
    c.fetch_status("A", "fake")
    c.fetch_status("B", "fake")
    assert calls["token"] == 1  # token reused, not re-fetched


def test_oauth_refreshes_token_once_on_401(monkeypatch):
    calls = {"token": 0, "track": 0}
    def fake_post(url, data, headers=None, timeout=10):
        calls["token"] += 1
        return {"access_token": f"TOK{calls['token']}", "expires_in": 3600}
    def fake_get(url, headers=None, timeout=10):
        calls["track"] += 1
        if calls["track"] == 1:
            raise CarrierAPIError("unauthorized", status_code=401)
        return {"s": "delivered"}
    monkeypatch.setattr(http_client_mod, "post_form", fake_post)
    monkeypatch.setattr(http_client_mod, "get_json", fake_get)
    result = _FakeCarrier().fetch_status("A", "fake")
    assert result.status == "delivered"
    assert calls["token"] == 2  # refreshed once
    assert calls["track"] == 2  # retried once


def test_oauth_non_401_error_propagates(monkeypatch):
    monkeypatch.setattr(http_client_mod, "post_form",
                        lambda url, data, headers=None, timeout=10: {"access_token": "T", "expires_in": 3600})
    def fake_get(url, headers=None, timeout=10):
        raise CarrierAPIError("server error", status_code=500)
    monkeypatch.setattr(http_client_mod, "get_json", fake_get)
    with pytest.raises(CarrierAPIError):
        _FakeCarrier().fetch_status("A", "fake")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py -k "oauth" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'packtrack.providers.oauth_carrier'`.

- [ ] **Step 3: Create `providers/oauth_carrier.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools.py -k "oauth" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add providers/oauth_carrier.py tests/test_tools.py
git commit -m "feat: add OAuthCarrierProvider base with token cache and 401 retry"
```
End with the `Co-Authored-By` trailer.

---

## Task 4: USPSProvider

**Files:**
- Create: `providers/usps.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:
```python
from packtrack.providers.usps import USPSProvider


@pytest.fixture
def usps_creds(monkeypatch):
    monkeypatch.setenv("USPS_CONSUMER_KEY", "key123")
    monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret123")
    monkeypatch.delenv("USPS_API_BASE", raising=False)


@pytest.mark.parametrize("raw,expected", [
    ("Delivered", "delivered"),
    ("Out for Delivery", "out_for_delivery"),
    ("In Transit to Next Facility", "in_transit"),
    ("Accepted", "in_transit"),
    ("Arrived at USPS Facility", "in_transit"),
    ("Pre-Shipment", "pending"),
    ("Shipping Label Created, USPS Awaiting Item", "pending"),
    ("Available for Pickup", "available_for_pickup"),
    ("Delivery Attempt", "delivery_attempted"),
    ("Alert", "exception"),
    ("Return to Sender", "returned"),
    ("Some Brand New Status", "unknown"),
    ("", "unknown"),
])
def test_usps_normalize_status(raw, expected):
    assert USPSProvider().normalize_status(raw) == expected


def test_usps_extract_prefers_status_category():
    p = USPSProvider()
    assert p._extract_raw_status({"statusCategory": "Delivered", "status": "x"}) == "Delivered"
    assert p._extract_raw_status({"status": "Out for Delivery"}) == "Out for Delivery"
    assert p._extract_raw_status({"statusSummary": "Your item was delivered..."}) == "Your item was delivered..."
    assert p._extract_raw_status({"trackingEvents": [{"eventType": "Delivered"}]}) == "Delivered"
    assert p._extract_raw_status({}) == ""


def test_usps_credentials_missing_raises(monkeypatch):
    monkeypatch.delenv("USPS_CONSUMER_KEY", raising=False)
    monkeypatch.delenv("USPS_CONSUMER_SECRET", raising=False)
    with pytest.raises(CredentialsMissingError):
        USPSProvider()._credentials()


def test_usps_fetch_status_happy_path(monkeypatch, usps_creds):
    monkeypatch.setattr(http_client_mod, "post_form",
                        lambda url, data, headers=None, timeout=10: {"access_token": "T", "expires_in": 3600})
    captured = {}
    def fake_get(url, headers=None, timeout=10):
        captured["url"] = url
        return {"statusCategory": "Out for Delivery", "trackingEvents": []}
    monkeypatch.setattr(http_client_mod, "get_json", fake_get)
    result = USPSProvider().fetch_status("9400111899223817428490", "usps")
    assert result.status == "out_for_delivery"
    assert result.raw_status == "Out for Delivery"
    assert result.provider == "usps"
    assert captured["url"].endswith("/tracking/v3/tracking/9400111899223817428490?expand=DETAIL")
    assert captured["url"].startswith("https://apis.usps.com")


def test_usps_uses_api_base_override(monkeypatch, usps_creds):
    monkeypatch.setenv("USPS_API_BASE", "https://apis-tem.usps.com")
    monkeypatch.setattr(http_client_mod, "post_form",
                        lambda url, data, headers=None, timeout=10: {"access_token": "T", "expires_in": 3600})
    captured = {}
    def fake_get(url, headers=None, timeout=10):
        captured["url"] = url
        return {"statusCategory": "Delivered"}
    monkeypatch.setattr(http_client_mod, "get_json", fake_get)
    USPSProvider().fetch_status("XYZ", "usps")
    assert captured["url"].startswith("https://apis-tem.usps.com/tracking/v3/tracking/XYZ")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py -k "usps" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'packtrack.providers.usps'`.

- [ ] **Step 3: Create `providers/usps.py`**

```python
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
from typing import Optional

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools.py -k "usps or router" -v`
Expected: PASS (all USPS tests + the router tests from Task 1 that import `usps`).

- [ ] **Step 5: Commit**

```bash
git add providers/usps.py tests/test_tools.py
git commit -m "feat: add USPS Tracking v3 provider"
```
End with the `Co-Authored-By` trailer.

---

## Task 5: Route `shipment_get_status` by carrier

**Files:**
- Modify: `tools.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:
```python
def test_get_status_routes_to_usps_and_succeeds(wired_store, monkeypatch, usps_creds):
    monkeypatch.setattr(http_client_mod, "post_form",
                        lambda url, data, headers=None, timeout=10: {"access_token": "T", "expires_in": 3600})
    monkeypatch.setattr(http_client_mod, "get_json",
                        lambda url, headers=None, timeout=10: {"statusCategory": "Delivered"})
    tools.shipment_add_tracking({"tracking_number": "USPS1", "carrier": "usps"})
    out = json.loads(tools.shipment_get_status({"tracking_number": "USPS1"}))
    assert out["success"] is True
    assert out["status"] == "delivered"
    assert out["provider"] == "usps"


def test_get_status_usps_missing_creds_returns_error(wired_store, monkeypatch):
    monkeypatch.delenv("USPS_CONSUMER_KEY", raising=False)
    monkeypatch.delenv("USPS_CONSUMER_SECRET", raising=False)
    tools.shipment_add_tracking({"tracking_number": "USPS2", "carrier": "usps"})
    out = json.loads(tools.shipment_get_status({"tracking_number": "USPS2"}))
    assert "error" in out
    assert "USPS credentials not configured" in out["error"]


def test_get_status_not_found_from_carrier(wired_store, monkeypatch, usps_creds):
    monkeypatch.setattr(http_client_mod, "post_form",
                        lambda url, data, headers=None, timeout=10: {"access_token": "T", "expires_in": 3600})
    def boom(url, headers=None, timeout=10):
        raise TrackingNotFoundError("tracking number not found")
    monkeypatch.setattr(http_client_mod, "get_json", boom)
    tools.shipment_add_tracking({"tracking_number": "USPS3", "carrier": "usps"})
    out = json.loads(tools.shipment_get_status({"tracking_number": "USPS3"}))
    assert "error" in out
    assert "not found" in out["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py -k "get_status_routes or missing_creds or not_found_from_carrier" -v`
Expected: FAIL — currently `get_status` calls `get_provider()` (no carrier) → mock, so `provider` is `"mock"` not `"usps"`.

- [ ] **Step 3: Modify `tools.py`**

Change the import line:
```python
from .providers import get_provider
```
to:
```python
from .providers import get_provider, ProviderError
```

Replace the provider call inside `shipment_get_status` (the lines that currently read):
```python
        result = get_provider().fetch_status(tracking_number, record["carrier"])
        return json.dumps({
            "success": True,
            "tracking_number": tracking_number,
            "carrier": record["carrier"],
            "status": result.status,
            "raw_status": result.raw_status,
            "provider": result.provider,
        })
```
with:
```python
        try:
            result = get_provider(record["carrier"]).fetch_status(
                tracking_number, record["carrier"]
            )
        except ProviderError as exc:
            return json.dumps({"error": str(exc)})

        return json.dumps({
            "success": True,
            "tracking_number": tracking_number,
            "carrier": record["carrier"],
            "status": result.status,
            "raw_status": result.raw_status,
            "provider": result.provider,
        })
```

- [ ] **Step 4: Run the full suite to verify everything passes**

Run: `pytest -v`
Expected: PASS — all tests, including the existing mock-based `test_get_status_success_and_deterministic` (its shipment uses `carrier="ups"`, which routes to mock → `provider == "mock"`, unchanged).

- [ ] **Step 5: Commit**

```bash
git add tools.py tests/test_tools.py
git commit -m "feat: route shipment_get_status by carrier and surface provider errors"
```
End with the `Co-Authored-By` trailer.

---

## Task 6: Manifest version + README docs

**Files:**
- Modify: `plugin.yaml`
- Modify: `README.md`

- [ ] **Step 1: Bump `plugin.yaml` version**

Change:
```yaml
version: 0.1.0
```
to:
```yaml
version: 0.2.0
```

- [ ] **Step 2: Add a "Carriers & credentials" section to `README.md`**

Insert this section immediately before the existing `## Swapping the provider` section:

````markdown
## Carriers & credentials

`shipment_get_status` routes to a real carrier based on the `carrier` you set when
adding a shipment. Supported today:

| Carrier slug | Backend | Credentials (environment variables) |
|---|---|---|
| `usps` | USPS Tracking v3 (real) | `USPS_CONSUMER_KEY`, `USPS_CONSUMER_SECRET` |
| anything else / unset | mock (deterministic) | none |

Credentials are **yours** — the plugin never ships keys. Get a USPS consumer
key/secret from [developer.usps.com](https://developer.usps.com) (OAuth2
client-credentials, scope `tracking`), then set them in the environment Hermes runs in:

```bash
export USPS_CONSUMER_KEY='...'
export USPS_CONSUMER_SECRET='...'
# Optional: point at the USPS test environment instead of production
export USPS_API_BASE='https://apis-tem.usps.com'
```

The plugin loads even without credentials; USPS tracking simply returns a clear
"credentials not configured" error until they're set. To track a package against
USPS, add it with the carrier slug:

> "Track 9400111899223817428490, carrier usps"
````

- [ ] **Step 3: Commit**

```bash
git add plugin.yaml README.md
git commit -m "docs: bump version to 0.2.0 and document USPS carrier credentials"
```
End with the `Co-Authored-By` trailer.

---

## Task 7: Opt-in live integration test

**Files:**
- Modify: `conftest.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Register the `integration` marker in `conftest.py`**

Append to `conftest.py`:
```python


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: live tests that hit real carrier APIs; require credentials, "
        "skipped by default.",
    )
```

- [ ] **Step 2: Add the opt-in integration test**

Append to `tests/test_tools.py`:
```python
import os


@pytest.mark.integration
@pytest.mark.skipif(
    not (os.environ.get("USPS_CONSUMER_KEY")
         and os.environ.get("USPS_CONSUMER_SECRET")
         and os.environ.get("USPS_TEST_TRACKING_NUMBER")),
    reason="USPS_CONSUMER_KEY, USPS_CONSUMER_SECRET, USPS_TEST_TRACKING_NUMBER not set",
)
def test_usps_live_tracking_returns_canonical_status():
    """Hits the real USPS API. Run with: pytest -m integration"""
    result = USPSProvider().fetch_status(
        os.environ["USPS_TEST_TRACKING_NUMBER"], "usps"
    )
    assert result.provider == "usps"
    assert result.status in CANONICAL_STATUSES
    assert isinstance(result.raw_status, str)
```

- [ ] **Step 3: Verify it is skipped (no creds) and the suite stays green**

Run: `pytest -v`
Expected: PASS — the integration test shows as `SKIPPED` (credentials not set); all other tests pass.

Run: `pytest -m integration -v`
Expected: the integration test is selected and `SKIPPED` (no creds); 0 failures.

- [ ] **Step 4: Commit**

```bash
git add conftest.py tests/test_tools.py
git commit -m "test: add opt-in USPS live integration test and integration marker"
```
End with the `Co-Authored-By` trailer.

---

## Task 8: Final verification

- [ ] **Step 1: Full hermetic suite**

Run: `pytest -v`
Expected: ALL pass; the one integration test SKIPPED; no network calls made.

- [ ] **Step 2: Confirm the real data store is untouched**

Run: `git status --short data/shipments.json`
Expected: no output (tests use temp stores).

- [ ] **Step 3: Confirm no new runtime dependencies**

Run: `git grep -nE "^\s*import (requests|httpx|aiohttp)" -- providers/ tools.py`
Expected: no output (standard library only).

- [ ] **Step 4: Final commit if anything is outstanding**

```bash
git add -A
git commit -m "chore: USPS real provider sub-project complete" || echo "nothing to commit"
```
End with the `Co-Authored-By` trailer if a commit is made.
