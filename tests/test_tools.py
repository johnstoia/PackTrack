import json

import pytest

from packtrack import schemas as schemas_module
from packtrack import tools
from packtrack.providers import CANONICAL_STATUSES, StatusResult, TrackingProvider, get_provider
from packtrack.providers import (
    ProviderError,
    CredentialsMissingError,
    TrackingNotFoundError,
    CarrierAPIError,
)
from packtrack.providers.mock import MockProvider
from packtrack.store import ShipmentStore


def test_canonical_statuses_are_the_ten_expected():
    assert CANONICAL_STATUSES == (
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


def test_mock_normalize_known_and_unknown():
    provider = get_provider("mock")
    assert provider.normalize_status("in_transit") == "in_transit"
    assert provider.normalize_status("IN-TRANSIT") == "in_transit"
    assert provider.normalize_status("nonsense-status") == "unknown"


def test_mock_fetch_status_is_deterministic_and_canonical():
    provider = get_provider("mock")
    first = provider.fetch_status("1Z999AA10123456784", "ups")
    second = provider.fetch_status("1Z999AA10123456784", "ups")
    assert isinstance(first, StatusResult)
    assert first.status in CANONICAL_STATUSES
    assert first.provider == "mock"
    assert first.status == second.status  # same number -> same status


@pytest.fixture
def store(tmp_path):
    return ShipmentStore(tmp_path / "shipments.json")


def test_store_starts_empty(store):
    assert store.list() == []


def test_store_add_persists_and_returns_record(store, tmp_path):
    record = store.add("ABC123", carrier="ups", label="gift")
    assert record["tracking_number"] == "ABC123"
    assert record["carrier"] == "ups"
    assert record["label"] == "gift"
    assert "added_at" in record
    # Reloading from disk confirms persistence.
    reloaded = ShipmentStore(tmp_path / "shipments.json")
    assert len(reloaded.list()) == 1


def test_store_add_optional_fields_default_to_none(store):
    record = store.add("NO-EXTRAS")
    assert record["carrier"] is None
    assert record["label"] is None


def test_store_find_returns_record_or_none(store):
    store.add("FINDME")
    assert store.find("FINDME")["tracking_number"] == "FINDME"
    assert store.find("MISSING") is None


def test_store_remove_returns_true_then_false(store):
    store.add("DELETEME")
    assert store.remove("DELETEME") is True
    assert store.remove("DELETEME") is False
    assert store.list() == []


def test_each_schema_is_well_formed():
    expected = {
        "shipment_add_tracking": schemas_module.ADD_TRACKING,
        "shipment_get_status": schemas_module.GET_STATUS,
        "shipment_list_tracked": schemas_module.LIST_TRACKED,
        "shipment_remove_tracking": schemas_module.REMOVE_TRACKING,
    }
    for name, schema in expected.items():
        assert schema["name"] == name
        assert isinstance(schema["description"], str) and schema["description"]
        assert schema["parameters"]["type"] == "object"
        assert "properties" in schema["parameters"]


def test_add_tracking_requires_only_tracking_number():
    assert schemas_module.ADD_TRACKING["parameters"]["required"] == ["tracking_number"]


@pytest.fixture
def wired_store(tmp_path, monkeypatch):
    """Point the handlers' store at a temp file via the injectable hook."""
    test_store = ShipmentStore(tmp_path / "shipments.json")
    monkeypatch.setattr(tools, "_get_store", lambda: test_store)
    return test_store


def test_add_tracking_success(wired_store):
    out = json.loads(tools.shipment_add_tracking({"tracking_number": "ABC123",
                                                   "carrier": "ups", "label": "gift"}))
    assert out["success"] is True
    assert out["shipment"]["tracking_number"] == "ABC123"
    assert out["shipment"]["carrier"] == "ups"


def test_add_tracking_rejects_empty(wired_store):
    for bad in ("", "   ", None):
        out = json.loads(tools.shipment_add_tracking({"tracking_number": bad}))
        assert "error" in out
    assert wired_store.list() == []


def test_add_tracking_rejects_duplicate(wired_store):
    tools.shipment_add_tracking({"tracking_number": "DUP"})
    out = json.loads(tools.shipment_add_tracking({"tracking_number": "DUP"}))
    assert "error" in out
    assert len(wired_store.list()) == 1


def test_list_tracked_counts_and_contents(wired_store):
    assert json.loads(tools.shipment_list_tracked({}))["count"] == 0
    tools.shipment_add_tracking({"tracking_number": "A"})
    tools.shipment_add_tracking({"tracking_number": "B"})
    out = json.loads(tools.shipment_list_tracked({}))
    assert out["count"] == 2
    numbers = {s["tracking_number"] for s in out["shipments"]}
    assert numbers == {"A", "B"}


def test_get_status_success_and_deterministic(wired_store):
    tools.shipment_add_tracking({"tracking_number": "STATUS1", "carrier": "ups"})
    out1 = json.loads(tools.shipment_get_status({"tracking_number": "STATUS1"}))
    out2 = json.loads(tools.shipment_get_status({"tracking_number": "STATUS1"}))
    assert out1["success"] is True
    assert out1["status"] in CANONICAL_STATUSES
    assert out1["provider"] == "mock"
    assert out1["status"] == out2["status"]


def test_get_status_unknown(wired_store):
    out = json.loads(tools.shipment_get_status({"tracking_number": "NOPE"}))
    assert "error" in out


def test_remove_tracking_success_then_gone(wired_store):
    tools.shipment_add_tracking({"tracking_number": "DELETEME"})
    out = json.loads(tools.shipment_remove_tracking({"tracking_number": "DELETEME"}))
    assert out["success"] is True
    assert out["removed"] == "DELETEME"
    assert json.loads(tools.shipment_list_tracked({}))["count"] == 0


def test_remove_tracking_unknown(wired_store):
    out = json.loads(tools.shipment_remove_tracking({"tracking_number": "NOPE"}))
    assert "error" in out


class _RecordingCtx:
    def __init__(self):
        self.registered = {}

    def register_tool(self, name, toolset, schema, handler):
        self.registered[name] = {"toolset": toolset, "schema": schema, "handler": handler}


def test_register_wires_all_four_tools():
    import packtrack

    ctx = _RecordingCtx()
    packtrack.register(ctx)
    assert set(ctx.registered.keys()) == {
        "shipment_add_tracking",
        "shipment_get_status",
        "shipment_list_tracked",
        "shipment_remove_tracking",
    }
    # Each wired handler is callable.
    for entry in ctx.registered.values():
        assert callable(entry["handler"])


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
