import asyncio
import json
from types import SimpleNamespace

import pytest

from packtrack import schemas as schemas_module
from packtrack import tools
from packtrack.providers import CANONICAL_STATUSES, StatusResult, TrackingProvider, get_provider
from packtrack.providers import (
    ProviderError,
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
    """Temp store + a deterministic mock provider, so handler tests stay offline."""
    test_store = ShipmentStore(tmp_path / "shipments.json")
    monkeypatch.setattr(tools, "_get_store", lambda: test_store)
    monkeypatch.setattr(tools, "get_provider", lambda carrier=None: MockProvider())
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
    tools.shipment_add_tracking({"tracking_number": "STATUS1", "carrier": "mock"})
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
    assert issubclass(TrackingNotFoundError, ProviderError)
    assert issubclass(CarrierAPIError, ProviderError)


def test_get_provider_mock_slug_returns_mock():
    assert isinstance(get_provider("mock"), MockProvider)


from packtrack.providers.seventeentrack import (
    SeventeenTrackProvider,
    _run_async,
    _import_track_api,
)
import packtrack.providers.seventeentrack as st_mod


def test_get_provider_defaults_to_seventeentrack():
    assert isinstance(get_provider("usps"), SeventeenTrackProvider)
    assert isinstance(get_provider(None), SeventeenTrackProvider)
    assert isinstance(get_provider("mock"), MockProvider)


def test_import_track_api_respects_optout(monkeypatch):
    monkeypatch.setattr(st_mod, "_install_attempted", False)
    monkeypatch.setattr(st_mod, "_do_import", lambda: (_ for _ in ()).throw(ImportError("nope")))
    installs = []
    monkeypatch.setattr(st_mod, "_pip_install", lambda spec: installs.append(spec))
    monkeypatch.setenv("PACKTRACK_NO_AUTOINSTALL", "1")
    with pytest.raises(ImportError):
        _import_track_api()
    assert installs == []


def test_import_track_api_installs_once_then_reraises(monkeypatch):
    monkeypatch.setattr(st_mod, "_install_attempted", False)
    monkeypatch.setattr(st_mod, "_do_import", lambda: (_ for _ in ()).throw(ImportError("nope")))
    installs = []
    monkeypatch.setattr(st_mod, "_pip_install", lambda spec: installs.append(spec))
    monkeypatch.delenv("PACKTRACK_NO_AUTOINSTALL", raising=False)
    with pytest.raises(ImportError):
        _import_track_api()
    assert len(installs) == 1


def _fake_pkg(status="InTransit", sub_status="InTransit_Other", carrier="USPS",
              latest="Departed USPS Facility"):
    return SimpleNamespace(
        status=status, sub_status=sub_status, carrier=carrier,
        events=[SimpleNamespace(description=latest)],
    )


def test_run_async_without_running_loop():
    async def coro():
        return 42
    assert _run_async(coro) == 42


def test_run_async_inside_running_loop():
    async def outer():
        async def inner():
            return 7
        return _run_async(inner)
    assert asyncio.run(outer()) == 7


@pytest.mark.parametrize("status,sub,expected", [
    ("InfoReceived", None, "info_received"),
    ("InTransit", "InTransit_PickedUp", "in_transit"),
    ("OutForDelivery", None, "out_for_delivery"),
    ("AvailableForPickup", None, "available_for_pickup"),
    ("Delivered", "Delivered_Other", "delivered"),
    ("DeliveryFailure", None, "delivery_attempted"),
    ("Exception", None, "exception"),
    ("Expired", None, "exception"),
    ("NotFound", None, "unknown"),
    ("InTransit", "Exception_Returning", "returned"),
    ("SomethingNew", None, "unknown"),
    (None, None, "unknown"),
])
def test_seventeentrack_normalize(status, sub, expected):
    assert SeventeenTrackProvider().normalize_status(status, sub) == expected


def test_seventeentrack_fetch_status_maps_package(monkeypatch):
    prov = SeventeenTrackProvider()
    monkeypatch.setattr(prov, "_find", lambda num: _fake_pkg(
        status="Delivered", sub_status="Delivered_Other", carrier="USPS",
        latest="Delivered, Front Porch"))
    r = prov.fetch_status("X1")
    assert r.status == "delivered"
    assert r.raw_status == "Delivered"
    assert r.provider == "17track"
    assert r.carrier == "USPS"
    assert r.sub_status == "Delivered_Other"
    assert r.detail == "Delivered, Front Porch"


def test_seventeentrack_propagates_not_found(monkeypatch):
    prov = SeventeenTrackProvider()
    def boom(num):
        raise TrackingNotFoundError("no data")
    monkeypatch.setattr(prov, "_find", boom)
    with pytest.raises(TrackingNotFoundError):
        prov.fetch_status("BOGUS")


def test_get_status_uses_real_provider(wired_store, monkeypatch):
    prov = SeventeenTrackProvider()
    monkeypatch.setattr(prov, "_find", lambda num: _fake_pkg(
        status="InTransit", sub_status="InTransit_PickedUp", carrier="USPS",
        latest="Picked Up"))
    monkeypatch.setattr(tools, "get_provider", lambda carrier=None: prov)
    tools.shipment_add_tracking({"tracking_number": "RP1", "carrier": "usps"})
    out = json.loads(tools.shipment_get_status({"tracking_number": "RP1"}))
    assert out["success"] is True
    assert out["status"] == "in_transit"
    assert out["provider"] == "17track"
    assert out["carrier"] == "USPS"
    assert out["sub_status"] == "InTransit_PickedUp"
    assert out["detail"] == "Picked Up"


def test_get_status_real_provider_error_returns_json(wired_store, monkeypatch):
    prov = SeventeenTrackProvider()
    def boom(num):
        raise CarrierAPIError("tracking temporarily unavailable")
    monkeypatch.setattr(prov, "_find", boom)
    monkeypatch.setattr(tools, "get_provider", lambda carrier=None: prov)
    tools.shipment_add_tracking({"tracking_number": "RP2", "carrier": "usps"})
    out = json.loads(tools.shipment_get_status({"tracking_number": "RP2"}))
    assert "error" in out
    assert "temporarily unavailable" in out["error"]


import os


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("PACKTRACK_LIVE_TRACKING_NUMBER"),
    reason="PACKTRACK_LIVE_TRACKING_NUMBER not set",
)
def test_seventeentrack_live_returns_canonical_status():
    """Hits the real 17track public endpoint. Run with: pytest -m integration
    (requires pyseventeentrack installed)."""
    result = SeventeenTrackProvider().fetch_status(
        os.environ["PACKTRACK_LIVE_TRACKING_NUMBER"]
    )
    assert result.provider == "17track"
    assert result.status in CANONICAL_STATUSES


def test_statusresult_has_events_hash_default_none():
    r = StatusResult(status="in_transit", raw_status="InTransit", provider="x")
    assert r.events_hash is None


def test_fetch_many_default_loops_and_skips_not_found():
    from packtrack.providers import TrackingProvider, StatusResult, TrackingNotFoundError

    class _P(TrackingProvider):
        name = "p"
        def normalize_status(self, raw, sub_status=None):
            return "in_transit"
        def fetch_status(self, tracking_number, carrier=None):
            if tracking_number == "BAD":
                raise TrackingNotFoundError("nope")
            return StatusResult(status="in_transit", raw_status="x", provider="p")

    out = _P().fetch_many(["A", "BAD", "B"])
    assert set(out.keys()) == {"A", "B"}
    assert out["A"].provider == "p"


def test_seventeentrack_fetch_many_maps_by_number(monkeypatch):
    from types import SimpleNamespace
    prov = SeventeenTrackProvider()
    pkgs = [
        SimpleNamespace(tracking_number="A", status="InTransit", sub_status="InTransit_Other",
                        carrier="USPS", events=[SimpleNamespace(description="moved")], events_hash=111),
        SimpleNamespace(tracking_number="B", status="Delivered", sub_status="Delivered_Other",
                        carrier="USPS", events=[SimpleNamespace(description="done")], events_hash=222),
    ]
    monkeypatch.setattr(prov, "_find_many", lambda numbers: pkgs)
    out = prov.fetch_many(["A", "B"])
    assert out["A"].status == "in_transit" and out["A"].events_hash == 111
    assert out["B"].status == "delivered" and out["B"].events_hash == 222


def test_seventeentrack_fetch_status_includes_events_hash(monkeypatch):
    from types import SimpleNamespace
    prov = SeventeenTrackProvider()
    monkeypatch.setattr(prov, "_find", lambda num: SimpleNamespace(
        status="InTransit", sub_status="InTransit_Other", carrier="USPS",
        events=[SimpleNamespace(description="moved")], events_hash=999))
    r = prov.fetch_status("A")
    assert r.events_hash == 999


def test_store_add_sets_monitoring_defaults(store):
    record = store.add("M1")
    assert record["monitor"] is True
    assert record["last_status"] is None
    assert record["last_events_hash"] is None
    assert record["last_checked_at"] is None


def test_store_update_patches_and_persists(store, tmp_path):
    store.add("U1")
    updated = store.update("U1", last_status="in_transit", last_events_hash=42, monitor=False)
    assert updated["last_status"] == "in_transit"
    assert updated["last_events_hash"] == 42
    assert updated["monitor"] is False
    reloaded = ShipmentStore(tmp_path / "shipments.json")
    assert reloaded.find("U1")["last_status"] == "in_transit"


def test_store_update_unknown_returns_none(store):
    assert store.update("NOPE", last_status="x") is None


from packtrack.changes import detect_change, ChangeResult


def _rec(num="C1", carrier=None, last_status=None, last_hash=None):
    return {"tracking_number": num, "carrier": carrier,
            "last_status": last_status, "last_events_hash": last_hash}


def test_detect_change_hash_unchanged_not_changed():
    rec = _rec(last_status="in_transit", last_hash=100)
    res = StatusResult(status="in_transit", raw_status="InTransit", provider="17track",
                       events_hash=100, detail="moved")
    cr = detect_change(rec, res, now="NOW")
    assert cr.changed is False
    assert cr.new_state["last_events_hash"] == 100
    assert cr.new_state["last_checked_at"] == "NOW"


def test_detect_change_hash_changed_reports():
    rec = _rec(num="C2", last_status="in_transit", last_hash=100)
    res = StatusResult(status="out_for_delivery", raw_status="OutForDelivery",
                       provider="17track", events_hash=200, detail="Out for delivery")
    cr = detect_change(rec, res, now="NOW")
    assert cr.changed is True
    assert "C2" in cr.summary and "out_for_delivery" in cr.summary
    assert cr.new_state["last_status"] == "out_for_delivery"


def test_detect_change_first_populate_is_change():
    rec = _rec(last_status=None, last_hash=None)
    res = StatusResult(status="in_transit", raw_status="InTransit", provider="17track",
                       events_hash=None, detail="moved")
    cr = detect_change(rec, res, now="NOW")
    assert cr.changed is True


def test_detect_change_delivered_stops_monitoring():
    rec = _rec(last_status="out_for_delivery", last_hash=200)
    res = StatusResult(status="delivered", raw_status="Delivered", provider="17track",
                       events_hash=300, detail="Delivered")
    cr = detect_change(rec, res, now="NOW")
    assert cr.new_state["monitor"] is False


def test_detect_change_fills_carrier_when_missing():
    rec = _rec(carrier=None, last_status="in_transit", last_hash=100)
    res = StatusResult(status="in_transit", raw_status="InTransit", provider="17track",
                       carrier="USPS", events_hash=100)
    cr = detect_change(rec, res, now="NOW")
    assert cr.new_state["carrier"] == "USPS"


def test_detect_change_hash_absent_falls_back_to_status():
    rec = _rec(last_status="in_transit", last_hash=None)
    same = StatusResult(status="in_transit", raw_status="x", provider="mock", events_hash=None)
    moved = StatusResult(status="delivered", raw_status="x", provider="mock", events_hash=None)
    assert detect_change(rec, same, now="NOW").changed is False
    assert detect_change(rec, moved, now="NOW").changed is True


from types import SimpleNamespace as _NS


class _FakeProvider:
    """Provider stub for monitoring tests: returns canned StatusResults by number."""
    def __init__(self, results):
        self._results = results
    def fetch_status(self, number, carrier=None):
        from packtrack.providers import TrackingNotFoundError
        if number not in self._results:
            raise TrackingNotFoundError("no data")
        return self._results[number]
    def fetch_many(self, numbers, carrier=None):
        return {n: self._results[n] for n in numbers if n in self._results}


def _sr(status="in_transit", ehash=100, carrier="USPS", detail="moved"):
    return StatusResult(status=status, raw_status=status, provider="17track",
                        carrier=carrier, events_hash=ehash, detail=detail)


def test_add_warmup_persists_real_data(wired_store, monkeypatch):
    monkeypatch.setattr(tools, "get_provider",
                        lambda carrier=None: _FakeProvider({"W1": _sr(status="in_transit", ehash=5)}))
    out = json.loads(tools.shipment_add_tracking({"tracking_number": "W1"}))
    assert out["success"] is True
    rec = wired_store.find("W1")
    assert rec["last_status"] == "in_transit"
    assert rec["last_events_hash"] == 5
    assert rec["carrier"] == "USPS"


def test_add_warmup_blank_leaves_state_null_and_succeeds(wired_store, monkeypatch):
    monkeypatch.setattr(tools, "get_provider", lambda carrier=None: _FakeProvider({}))
    out = json.loads(tools.shipment_add_tracking({"tracking_number": "W2"}))
    assert out["success"] is True
    rec = wired_store.find("W2")
    assert rec["last_status"] is None and rec["monitor"] is True


def test_add_warmup_survives_provider_error(wired_store, monkeypatch):
    class _Boom:
        def fetch_status(self, n, carrier=None):
            from packtrack.providers import CarrierAPIError
            raise CarrierAPIError("down")
    monkeypatch.setattr(tools, "get_provider", lambda carrier=None: _Boom())
    out = json.loads(tools.shipment_add_tracking({"tracking_number": "W3"}))
    assert out["success"] is True
    assert wired_store.find("W3")["monitor"] is True


def test_list_includes_monitor_and_last_status(wired_store, monkeypatch):
    monkeypatch.setattr(tools, "get_provider", lambda carrier=None: _FakeProvider({}))
    tools.shipment_add_tracking({"tracking_number": "L1"})
    out = json.loads(tools.shipment_list_tracked({}))
    entry = out["shipments"][0]
    assert entry["monitor"] is True
    assert "last_status" in entry


def test_check_updates_reports_only_changes_and_persists(wired_store, monkeypatch):
    monkeypatch.setattr(tools, "get_provider", lambda carrier=None: _FakeProvider({}))
    tools.shipment_add_tracking({"tracking_number": "A"})
    tools.shipment_add_tracking({"tracking_number": "B"})
    wired_store.update("A", last_status="in_transit", last_events_hash=100)
    wired_store.update("B", last_status="in_transit", last_events_hash=200)
    results = {"A": _sr(status="out_for_delivery", ehash=101, detail="Out for delivery"),
               "B": _sr(status="in_transit", ehash=200, detail="still moving")}
    monkeypatch.setattr(tools, "get_provider", lambda carrier=None: _FakeProvider(results))
    out = json.loads(tools.shipment_check_updates({}))
    assert out["success"] is True
    assert out["checked"] == 2
    assert len(out["changes"]) == 1 and "A" in out["changes"][0]
    assert wired_store.find("A")["last_events_hash"] == 101


def test_check_updates_flips_delivered_off(wired_store, monkeypatch):
    monkeypatch.setattr(tools, "get_provider", lambda carrier=None: _FakeProvider({}))
    tools.shipment_add_tracking({"tracking_number": "D"})
    wired_store.update("D", last_status="out_for_delivery", last_events_hash=10)
    monkeypatch.setattr(tools, "get_provider",
                        lambda carrier=None: _FakeProvider({"D": _sr(status="delivered", ehash=11, detail="Delivered")}))
    out = json.loads(tools.shipment_check_updates({}))
    assert "D" in out["delivered"]
    assert wired_store.find("D")["monitor"] is False


def test_check_updates_skips_no_data_keeps_state(wired_store, monkeypatch):
    monkeypatch.setattr(tools, "get_provider", lambda carrier=None: _FakeProvider({}))
    tools.shipment_add_tracking({"tracking_number": "K"})
    wired_store.update("K", last_status="in_transit", last_events_hash=7)
    monkeypatch.setattr(tools, "get_provider", lambda carrier=None: _FakeProvider({}))
    out = json.loads(tools.shipment_check_updates({}))
    assert out["checked"] == 1 and out["changes"] == []
    assert wired_store.find("K")["last_events_hash"] == 7


def test_check_updates_no_monitored_is_clean(wired_store, monkeypatch):
    monkeypatch.setattr(tools, "get_provider", lambda carrier=None: _FakeProvider({}))
    out = json.loads(tools.shipment_check_updates({}))
    assert out["success"] is True and out["checked"] == 0 and out["changes"] == []


def test_set_monitoring_toggles(wired_store, monkeypatch):
    monkeypatch.setattr(tools, "get_provider", lambda carrier=None: _FakeProvider({}))
    tools.shipment_add_tracking({"tracking_number": "S1"})
    out = json.loads(tools.shipment_set_monitoring({"tracking_number": "S1", "enabled": False}))
    assert out["success"] is True and out["monitor"] is False
    assert wired_store.find("S1")["monitor"] is False


def test_set_monitoring_unknown_errors(wired_store):
    out = json.loads(tools.shipment_set_monitoring({"tracking_number": "NOPE", "enabled": True}))
    assert "error" in out
