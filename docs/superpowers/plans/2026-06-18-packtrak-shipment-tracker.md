# PackTrak Shipment-Tracker Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Hermes Agent plugin (PackTrak) that tracks shipments via four tools, backed by a swappable mock provider and a JSON file store.

**Architecture:** Thin Hermes-facing handlers (`tools.py`) call a JSON-file persistence layer (`store.py`) and a provider obtained from a factory (`providers/`). The provider is an ABC (`TrackingProvider`) so AfterShip/EasyPost/17TRACK can be added later without touching handlers. Status is computed on demand, never stored. Standard-library only at runtime; pytest for tests.

**Tech Stack:** Python 3 (stdlib only), pytest, Hermes plugin contract (`register(ctx)` + `ctx.register_tool`).

---

## File Structure

- `plugin.yaml` — Hermes manifest (name, version, description, provides_tools).
- `__init__.py` — `register(ctx)` wiring 4 schemas → 4 handlers.
- `providers/__init__.py` — `CANONICAL_STATUSES`, `StatusResult`, `TrackingProvider` ABC, `get_provider()`.
- `providers/mock.py` — `MockProvider` (deterministic status by tracking number).
- `store.py` — `ShipmentStore` (load/save/list/find/add/remove against an injectable JSON path).
- `schemas.py` — 4 OpenAI-format tool schema dicts.
- `tools.py` — 4 handlers (`args: dict, **kwargs) -> str`); resolve store path via injectable hook; never raise.
- `data/shipments.json` — committed empty store `{"shipments": []}`.
- `tests/test_tools.py` — pytest suite with `tmp_path` isolation fixture.
- `README.md` — deployment + manual test docs.
- `.gitignore` — Python defaults.

> **Note on imports:** A Hermes plugin is loaded as a package, so intra-plugin imports use relative form (`from . import ...`, `from .providers import ...`). The test suite imports the same way by treating the plugin dir as a package (a `tests/__init__.py` is not required; tests use `from .. import tools` is NOT used — instead tests sit inside the package and import via the package). To keep tests runnable with a plain `pytest` from the repo root, the plan uses a `conftest.py` at repo root that adds the repo root to `sys.path` and tests import modules directly (`import tools`, `import store`, `from providers import get_provider`). This avoids packaging ceremony for the MVP while matching how Hermes imports the modules at runtime (Hermes adds the plugin dir to the path).

---

## Task 0: Repo scaffold + git init

**Files:**
- Create: `.gitignore`
- Create: `data/shipments.json`
- Create: `conftest.py`

- [ ] **Step 1: Initialize the git repository**

Run:
```bash
cd /c/Users/Jstoi/source/repos/PackTrack
git init
```
Expected: "Initialized empty Git repository".

- [ ] **Step 2: Create `.gitignore`**

```
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.venv/
venv/
env/
.DS_Store
Thumbs.db
.idea/
.vscode/
```

- [ ] **Step 3: Create the empty data store `data/shipments.json`**

```json
{
  "shipments": []
}
```

- [ ] **Step 4: Create root `conftest.py` so tests import plugin modules directly**

```python
import os
import sys

# Make the plugin modules (tools, store, schemas, providers) importable from the
# repo root during tests. Hermes adds the plugin directory to the path at runtime;
# this mirrors that for `pytest` run from the repo root.
sys.path.insert(0, os.path.dirname(__file__))
```

- [ ] **Step 5: Commit**

```bash
git add .gitignore data/shipments.json conftest.py docs/
git commit -m "chore: scaffold PackTrak repo, git init, design + plan docs"
```

---

## Task 1: Canonical statuses + provider contract

**Files:**
- Create: `providers/__init__.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tools.py`:
```python
import json

from providers import CANONICAL_STATUSES, StatusResult, TrackingProvider, get_provider


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools.py::test_canonical_statuses_are_the_ten_expected -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'providers'`.

- [ ] **Step 3: Write minimal implementation**

Create `providers/__init__.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tools.py::test_canonical_statuses_are_the_ten_expected -v`
Expected: PASS (note: `get_provider` import works; `mock` import is lazy so it won't fail yet).

- [ ] **Step 5: Commit**

```bash
git add providers/__init__.py tests/test_tools.py
git commit -m "feat: add canonical statuses and TrackingProvider contract"
```

---

## Task 2: MockProvider

**Files:**
- Create: `providers/mock.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py::test_mock_fetch_status_is_deterministic_and_canonical -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'providers.mock'`.

- [ ] **Step 3: Write minimal implementation**

Create `providers/mock.py`:
```python
"""Deterministic mock tracking provider for the MVP.

`fetch_status` derives a stable status from the tracking number so the same number
always returns the same status (repeatable for tests) while different numbers vary.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from . import CANONICAL_STATUSES, StatusResult, TrackingProvider


class MockProvider(TrackingProvider):
    name = "mock"

    def normalize_status(self, raw: str) -> str:
        candidate = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
        if candidate in CANONICAL_STATUSES:
            return candidate
        return "unknown"

    def fetch_status(self, tracking_number: str, carrier: Optional[str]) -> StatusResult:
        digest = hashlib.sha256(tracking_number.encode("utf-8")).hexdigest()
        index = int(digest, 16) % len(CANONICAL_STATUSES)
        raw = CANONICAL_STATUSES[index]
        return StatusResult(
            status=self.normalize_status(raw),
            raw_status=raw,
            provider=self.name,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools.py -k mock -v`
Expected: PASS (both mock tests).

- [ ] **Step 5: Commit**

```bash
git add providers/mock.py tests/test_tools.py
git commit -m "feat: add deterministic MockProvider"
```

---

## Task 3: ShipmentStore (persistence)

**Files:**
- Create: `store.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:
```python
import pytest

from store import ShipmentStore


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py -k store -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'store'`.

- [ ] **Step 3: Write minimal implementation**

Create `store.py`:
```python
"""JSON-file persistence for tracked shipments.

The file path is injected (not hardcoded) so tests can point the store at a temp
file. The store holds only user-supplied data; status is computed on demand by the
provider layer and never persisted here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


class ShipmentStore:
    def __init__(self, path):
        self._path = Path(path)

    def _read(self) -> dict:
        if not self._path.exists():
            return {"shipments": []}
        with self._path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

    def list(self) -> List[dict]:
        return self._read()["shipments"]

    def find(self, tracking_number: str) -> Optional[dict]:
        for record in self.list():
            if record["tracking_number"] == tracking_number:
                return record
        return None

    def add(self, tracking_number: str, carrier: Optional[str] = None,
            label: Optional[str] = None) -> dict:
        data = self._read()
        record = {
            "tracking_number": tracking_number,
            "carrier": carrier,
            "label": label,
            "added_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        data["shipments"].append(record)
        self._write(data)
        return record

    def remove(self, tracking_number: str) -> bool:
        data = self._read()
        before = len(data["shipments"])
        data["shipments"] = [
            r for r in data["shipments"] if r["tracking_number"] != tracking_number
        ]
        if len(data["shipments"]) == before:
            return False
        self._write(data)
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools.py -k store -v`
Expected: PASS (all five store tests).

- [ ] **Step 5: Commit**

```bash
git add store.py tests/test_tools.py
git commit -m "feat: add ShipmentStore JSON persistence with injectable path"
```

---

## Task 4: Tool schemas

**Files:**
- Create: `schemas.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools.py`:
```python
import schemas as schemas_module


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools.py -k schema -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'schemas'`.

- [ ] **Step 3: Write minimal implementation**

Create `schemas.py`:
```python
"""OpenAI-format tool schemas for the PackTrak Hermes plugin.

Descriptions are written for the LLM: they explain when each tool should be called.
"""

ADD_TRACKING = {
    "name": "shipment_add_tracking",
    "description": (
        "Start tracking a package by its tracking number. Optionally include the "
        "carrier (e.g. 'ups', 'fedex') and a human-friendly label. Rejects empty "
        "tracking numbers and numbers that are already tracked."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tracking_number": {
                "type": "string",
                "description": "The carrier tracking number to track.",
            },
            "carrier": {
                "type": "string",
                "description": "Optional carrier slug, e.g. 'ups', 'fedex', 'usps'.",
            },
            "label": {
                "type": "string",
                "description": "Optional human-friendly label for this shipment.",
            },
        },
        "required": ["tracking_number"],
    },
}

GET_STATUS = {
    "name": "shipment_get_status",
    "description": (
        "Get the current delivery status of a tracked shipment by its tracking "
        "number. Returns a normalized status such as in_transit or delivered."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tracking_number": {
                "type": "string",
                "description": "The tracking number of an already-tracked shipment.",
            },
        },
        "required": ["tracking_number"],
    },
}

LIST_TRACKED = {
    "name": "shipment_list_tracked",
    "description": "List all shipments currently being tracked.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

REMOVE_TRACKING = {
    "name": "shipment_remove_tracking",
    "description": (
        "Stop tracking a shipment and remove it from the tracked list, by its "
        "tracking number."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tracking_number": {
                "type": "string",
                "description": "The tracking number of the shipment to remove.",
            },
        },
        "required": ["tracking_number"],
    },
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tools.py -k schema -v`
Expected: PASS (both schema tests).

- [ ] **Step 5: Commit**

```bash
git add schemas.py tests/test_tools.py
git commit -m "feat: add OpenAI-format tool schemas"
```

---

## Task 5: Tool handlers

**Files:**
- Create: `tools.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:
```python
import tools


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py -k "add_tracking or list_tracked or get_status or remove_tracking" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools'`.

- [ ] **Step 3: Write minimal implementation**

Create `tools.py`:
```python
"""Hermes tool handlers for PackTrak.

Each handler takes a dict of LLM-supplied args, returns a JSON string, and never
raises — errors are returned as {"error": "..."}. The store path is resolved through
`_get_store`, a single injectable hook that tests override to use a temp file.
"""
from __future__ import annotations

import json
from pathlib import Path

from providers import get_provider
from store import ShipmentStore

# Default runtime store: data/shipments.json next to this module.
_DEFAULT_STORE_PATH = Path(__file__).parent / "data" / "shipments.json"


def _get_store() -> ShipmentStore:
    """Return the active store. Overridden in tests to point at a temp file."""
    return ShipmentStore(_DEFAULT_STORE_PATH)


def _public(record: dict) -> dict:
    return {
        "tracking_number": record["tracking_number"],
        "carrier": record["carrier"],
        "label": record["label"],
        "added_at": record["added_at"],
    }


def shipment_add_tracking(args: dict, **kwargs) -> str:
    try:
        raw = args.get("tracking_number")
        tracking_number = (raw or "").strip()
        if not tracking_number:
            return json.dumps({"error": "tracking_number is required"})

        store = _get_store()
        if store.find(tracking_number) is not None:
            return json.dumps({"error": "tracking_number already tracked"})

        carrier = args.get("carrier")
        label = args.get("label")
        record = store.add(tracking_number, carrier=carrier, label=label)
        return json.dumps({"success": True, "shipment": _public(record)})
    except Exception as exc:  # never raise out of a handler
        return json.dumps({"error": str(exc)})


def shipment_list_tracked(args: dict, **kwargs) -> str:
    try:
        records = _get_store().list()
        return json.dumps({
            "success": True,
            "count": len(records),
            "shipments": [_public(r) for r in records],
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def shipment_get_status(args: dict, **kwargs) -> str:
    try:
        raw = args.get("tracking_number")
        tracking_number = (raw or "").strip()
        if not tracking_number:
            return json.dumps({"error": "tracking_number is required"})

        record = _get_store().find(tracking_number)
        if record is None:
            return json.dumps({"error": "tracking_number not tracked"})

        result = get_provider().fetch_status(tracking_number, record["carrier"])
        return json.dumps({
            "success": True,
            "tracking_number": tracking_number,
            "carrier": record["carrier"],
            "status": result.status,
            "raw_status": result.raw_status,
            "provider": result.provider,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def shipment_remove_tracking(args: dict, **kwargs) -> str:
    try:
        raw = args.get("tracking_number")
        tracking_number = (raw or "").strip()
        if not tracking_number:
            return json.dumps({"error": "tracking_number is required"})

        if _get_store().remove(tracking_number):
            return json.dumps({"success": True, "removed": tracking_number})
        return json.dumps({"error": "tracking_number not tracked"})
    except Exception as exc:
        return json.dumps({"error": str(exc)})
```

- [ ] **Step 4: Run the full test suite to verify everything passes**

Run: `pytest -v`
Expected: PASS (all tests across statuses, mock, store, schemas, handlers).

- [ ] **Step 5: Commit**

```bash
git add tools.py tests/test_tools.py
git commit -m "feat: add four shipment tool handlers"
```

---

## Task 6: Hermes manifest + register entry point

**Files:**
- Create: `plugin.yaml`
- Create: `__init__.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools.py`:
```python
class _RecordingCtx:
    def __init__(self):
        self.registered = {}

    def register_tool(self, name, toolset, schema, handler):
        self.registered[name] = {"toolset": toolset, "schema": schema, "handler": handler}


def test_register_wires_all_four_tools():
    import importlib

    pkg = importlib.import_module("__init__")  # the plugin's __init__.py at repo root
    ctx = _RecordingCtx()
    pkg.register(ctx)
    assert set(ctx.registered.keys()) == {
        "shipment_add_tracking",
        "shipment_get_status",
        "shipment_list_tracked",
        "shipment_remove_tracking",
    }
    # Each wired handler is callable.
    for entry in ctx.registered.values():
        assert callable(entry["handler"])
```

> **Note:** At runtime Hermes imports the plugin as a package and calls its
> `register`. In tests we load the repo-root `__init__.py` by module name to verify
> wiring without packaging ceremony. Because `__init__.py` uses absolute imports
> (`import schemas`, `import tools`) that resolve via the `conftest.py` path insert,
> this load succeeds from the repo root.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools.py::test_register_wires_all_four_tools -v`
Expected: FAIL with `ModuleNotFoundError: No module named '__init__'` (or AttributeError on `register`).

- [ ] **Step 3: Write the implementations**

Create `plugin.yaml`:
```yaml
name: shipment-tracker
version: 0.1.0
description: Track package shipments and their delivery status (PackTrak MVP, mock provider).
author: jstoia16@gmail.com
provides_tools:
  - shipment_add_tracking
  - shipment_get_status
  - shipment_list_tracked
  - shipment_remove_tracking
```

Create `__init__.py`:
```python
"""PackTrak — Hermes shipment-tracker plugin entry point.

Hermes calls `register(ctx)` once at startup to wire each tool schema to its handler.
"""
import schemas
import tools


def register(ctx):
    ctx.register_tool(
        name="shipment_add_tracking",
        toolset="shipment",
        schema=schemas.ADD_TRACKING,
        handler=tools.shipment_add_tracking,
    )
    ctx.register_tool(
        name="shipment_get_status",
        toolset="shipment",
        schema=schemas.GET_STATUS,
        handler=tools.shipment_get_status,
    )
    ctx.register_tool(
        name="shipment_list_tracked",
        toolset="shipment",
        schema=schemas.LIST_TRACKED,
        handler=tools.shipment_list_tracked,
    )
    ctx.register_tool(
        name="shipment_remove_tracking",
        toolset="shipment",
        schema=schemas.REMOVE_TRACKING,
        handler=tools.shipment_remove_tracking,
    )
```

- [ ] **Step 4: Run the full suite to verify it passes**

Run: `pytest -v`
Expected: PASS (all tests including registration).

- [ ] **Step 5: Commit**

```bash
git add plugin.yaml __init__.py tests/test_tools.py
git commit -m "feat: add plugin.yaml manifest and register entry point"
```

---

## Task 7: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

````markdown
# PackTrak — Hermes Shipment Tracker

A Hermes Agent plugin that tracks package shipments. MVP uses a deterministic mock
provider; the provider layer is built to swap in AfterShip, EasyPost, or 17TRACK
later without changing the tool handlers.

## Tools

| Tool | Purpose |
|------|---------|
| `shipment_add_tracking` | Track a number (optional carrier + label). Rejects empty and duplicate numbers. |
| `shipment_get_status` | Get normalized delivery status for a tracked number. |
| `shipment_list_tracked` | List all tracked shipments. |
| `shipment_remove_tracking` | Stop tracking a number. |

Statuses are normalized to: `pending`, `info_received`, `in_transit`,
`out_for_delivery`, `delivered`, `available_for_pickup`, `delivery_attempted`,
`exception`, `returned`, `unknown`.

## Install into Hermes

1. **Clone into the Hermes plugins directory:**

   ```bash
   git clone <this-repo-url> ~/.hermes/plugins/shipment-tracker
   ```

2. **Enable the plugin:**

   ```bash
   hermes plugins enable shipment-tracker
   ```

   Troubleshoot discovery with:

   ```bash
   HERMES_PLUGINS_DEBUG=1 hermes plugins list
   ```

3. **Restart Hermes (or the Hermes gateway)** so the plugin is loaded.

4. **Test manually** in a Hermes session — ask the agent to:
   - add tracking for `1Z999AA10123456784` with carrier `ups` and label `Test box`
   - list tracked shipments
   - get the status of `1Z999AA10123456784`
   - remove tracking for `1Z999AA10123456784`

## Data

Tracked shipments are stored in `data/shipments.json` (`{"shipments": [...]}`).
The repo ships an empty store. Manual testing mutates this file; reset it with:

```bash
git checkout data/shipments.json
```

## Development

Run the tests (pytest, standard library only):

```bash
pytest -v
```

## Swapping the provider

`providers/__init__.py` defines the `TrackingProvider` ABC and `get_provider()`
factory. To add a real backend:

1. Create `providers/aftership.py` with a `class AfterShipProvider(TrackingProvider)`
   implementing `normalize_status` and `fetch_status`.
2. Register its name in `get_provider`.
3. Point `tools.get_provider()` at the new name.

No tool handler changes are required.
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with install and manual-test instructions"
```

---

## Task 8: Final verification

- [ ] **Step 1: Run the entire suite from a clean state**

Run: `pytest -v`
Expected: ALL tests PASS.

- [ ] **Step 2: Confirm the data store is unmodified by tests**

Run: `git status data/shipments.json`
Expected: no changes (tests use temp files, not the real store).

- [ ] **Step 3: Confirm repo structure matches the spec**

Run: `git ls-files`
Expected: `plugin.yaml`, `__init__.py`, `schemas.py`, `tools.py`, `store.py`,
`providers/__init__.py`, `providers/mock.py`, `data/shipments.json`,
`tests/test_tools.py`, `README.md`, `.gitignore`, `conftest.py`, plus `docs/`.

- [ ] **Step 4: Final commit if anything is outstanding**

```bash
git add -A
git commit -m "chore: PackTrak MVP complete" || echo "nothing to commit"
```
