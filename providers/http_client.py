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
