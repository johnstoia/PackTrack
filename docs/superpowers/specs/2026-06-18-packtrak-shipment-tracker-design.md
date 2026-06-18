# PackTrak — Hermes Shipment-Tracker Plugin (MVP Design)

**Date:** 2026-06-18
**Status:** Approved design — ready for implementation plan

## Goal

Build a standalone Git repo for a Hermes Agent plugin named **PackTrak**. The repo
is structured so it can be copied or cloned into:

```
~/.hermes/plugins/shipment-tracker
```

The plugin provides four Hermes tools for tracking package shipments:

- `shipment_add_tracking`
- `shipment_get_status`
- `shipment_list_tracked`
- `shipment_remove_tracking`

The MVP uses a **mock** tracking provider. The provider layer is deliberately
designed as a swappable seam so AfterShip, EasyPost, or 17TRACK can be added later
without touching the Hermes-facing handlers.

### Constraints

- No paid/external API calls.
- No secrets or API keys created or required.
- Standard-library only at runtime (pytest is the only test-time dependency).

## Hermes Plugin Contract (reference)

Grounded in the Hermes "Build a Hermes Plugin" guide:

- Plugins live in `~/.hermes/plugins/<plugin-name>/`.
- `plugin.yaml` manifest declares `name`, `version`, `description`, and
  `provides_tools`.
- `__init__.py` exposes `register(ctx)`, the entry point called once at startup.
  It wires each schema to its handler via
  `ctx.register_tool(name=..., toolset=..., schema=..., handler=...)`.
- Tool **schemas** are OpenAI-format dicts: `{"name", "description", "parameters":
  {"type": "object", "properties": {...}, "required": [...]}}`. Descriptions matter —
  the LLM reads them to decide when to call a tool.
- Tool **handlers** have the signature `def handler(args: dict, **kwargs) -> str`.
  They **always return a JSON string** and **never raise**; errors are returned as
  JSON instead.

## Architecture

```
shipment-tracker/
├── plugin.yaml              # Hermes manifest: name, version, provides_tools
├── __init__.py              # register(ctx) — wires 4 schemas → 4 handlers
├── schemas.py               # 4 OpenAI-format tool schema dicts
├── tools.py                 # 4 handlers (args: dict, **kwargs) -> JSON str; never raise
├── store.py                 # shipment persistence (load/save/find/add/remove)
├── providers/
│   ├── __init__.py          # TrackingProvider ABC + canonical statuses + get_provider()
│   └── mock.py              # MockProvider (deterministic status by tracking #)
├── data/
│   └── shipments.json       # JSON store: {"shipments": [...]}
├── tests/
│   └── test_tools.py        # pytest: add/list/get/remove + validation cases
├── README.md
└── .gitignore
```

### Module responsibilities

- **`schemas.py`** — pure data. Four OpenAI-format schema dicts, one per tool, with
  clear `description` fields.
- **`tools.py`** — four thin handlers. Each parses `args`, calls `store` and/or the
  active provider, and serializes a JSON response. Catches all exceptions and returns
  `{"error": ...}`. Never raises.
- **`store.py`** — persistence, separated from the Hermes-facing handlers. Loads and
  saves `data/shipments.json`, finds by tracking number, enforces uniqueness, adds and
  removes records. The store's file path is **injectable** (parameter / overridable
  default) so tests point it at a temp file instead of the real data file.
- **`providers/__init__.py`** — defines the `TrackingProvider` ABC (the swap seam),
  the frozen set of canonical statuses, and `get_provider(name="mock")` factory.
- **`providers/mock.py`** — `MockProvider`, the only concrete provider in the MVP.

### Provider layer (the swap seam)

`providers/__init__.py` defines an abstract base class:

```python
class TrackingProvider(ABC):
    name: str

    @abstractmethod
    def normalize_status(self, raw: str) -> str:
        """Map a provider-specific status string to one canonical status.
        Unrecognized input maps to 'unknown'."""

    @abstractmethod
    def fetch_status(self, tracking_number: str, carrier: str | None) -> "StatusResult":
        """Return the current status for a tracking number."""
```

`get_provider(name="mock")` is a small factory returning the active provider
instance. Tools call `get_provider()` and never import a concrete provider class.
Adding AfterShip later = add `providers/aftership.py` with a `TrackingProvider`
subclass and register its name in the factory; handlers are untouched.

`StatusResult` is a lightweight dataclass: `status` (canonical), `raw_status` (what
the provider reported), and `provider` (name string).

## Data Model

A shipment record in `data/shipments.json`:

```json
{
  "tracking_number": "1Z999AA10123456784",
  "carrier": "ups",
  "label": "Birthday gift",
  "added_at": "2026-06-18T12:00:00Z"
}
```

- File shape: `{ "shipments": [ ... ] }`.
- `tracking_number` is the unique key; duplicate prevention is enforced on it.
- `carrier` and `label` are optional; stored as `null` when not provided.
- `added_at` is an ISO-8601 UTC timestamp set at add time.
- **Status is not stored.** It is computed on demand by the active provider, so the
  store holds only user-supplied data.

## Status Normalization

The ten canonical statuses are defined once in `providers/__init__.py` as a frozen
collection (the order is also the basis for the mock's deterministic mapping):

```
pending, info_received, in_transit, out_for_delivery, delivered,
available_for_pickup, delivery_attempted, exception, returned, unknown
```

`TrackingProvider.normalize_status(raw)` maps any provider-specific string to one of
these; anything unrecognized → `"unknown"`. This single method is the contract every
future provider implements with its own mapping table.

### Mock provider behavior

`MockProvider.fetch_status()` deterministically selects a status from the tracking
number: a stable hash of the tracking number indexes into the canonical status list.
The same tracking number always returns the same status (repeatable for tests),
while different numbers produce varied statuses. The mock returns a raw value that is
then run through `normalize_status()`, so the real normalization flow is exercised
end to end.

## Tool Contracts

All handlers return a JSON **string** and never raise. Errors are
`{"error": "<message>"}`.

### `shipment_add_tracking`
- **Input:** `tracking_number` (string, required), `carrier` (string, optional),
  `label` (string, optional).
- **Validation:** trims whitespace; empty / whitespace-only →
  `{"error": "tracking_number is required"}`; already tracked →
  `{"error": "tracking_number already tracked"}`.
- **Success:** `{"success": true, "shipment": {tracking_number, carrier, label, added_at}}`.

### `shipment_list_tracked`
- **Input:** none.
- **Success:** `{"success": true, "count": N, "shipments": [ {tracking_number, carrier, label, added_at}, ... ]}`.

### `shipment_get_status`
- **Input:** `tracking_number` (string, required).
- **Not found:** `{"error": "tracking_number not tracked"}`.
- **Success:** `{"success": true, "tracking_number": "...", "carrier": "...",
  "status": "in_transit", "raw_status": "...", "provider": "mock"}`.

### `shipment_remove_tracking`
- **Input:** `tracking_number` (string, required).
- **Not found:** `{"error": "tracking_number not tracked"}`.
- **Success:** `{"success": true, "removed": "<tracking_number>"}`.

Each tool has a matching OpenAI-format schema dict in `schemas.py`. The `provider`
field in the status output makes it explicit which backend answered — useful once
real providers exist.

## Testing

- **Framework:** pytest, standard-library only.
- **Isolation:** `store.py` takes the JSON file path as a parameter rather than
  hardcoding it. A pytest fixture points the store at a `tmp_path` file so tests
  never touch the real `data/shipments.json`. Handlers resolve the store path through
  a single injectable hook that the fixture overrides, so tests exercise the real
  handler code end to end against a temp store.

### Test cases (`tests/test_tools.py`)

- `add` → success + record; persisted (reload from file confirms).
- `add` with empty string / whitespace-only → `{"error": ...}`; nothing persisted.
- `add` duplicate tracking number → `{"error": ...}`; only one record exists.
- `add` with optional carrier + label → stored correctly; without them → `null`.
- `list` empty → `count: 0`; `list` after adds → correct count + contents.
- `get_status` tracked number → success; status is one of the 10 canonical values;
  deterministic (same number twice → same status).
- `get_status` unknown number → `{"error": ...}`.
- `remove` tracked number → success; absent from `list` afterward.
- `remove` unknown number → `{"error": ...}`.

All assertions parse the returned JSON string, which also verifies handler output is
always valid JSON.

## README (deployment docs)

`README.md` explains how to:

1. Clone this repo into `~/.hermes/plugins/shipment-tracker`.
2. Enable the plugin in Hermes (`hermes plugins enable shipment-tracker` / config),
   noting `HERMES_PLUGINS_DEBUG=1 hermes plugins list` for troubleshooting discovery.
3. Restart Hermes / the Hermes gateway.
4. Test the plugin manually (example tool invocations for add → list → get_status →
   remove).
5. Run the test suite (`pytest`).
6. Where the swap seam is, for adding a real provider later.

## .gitignore

Python defaults: `__pycache__/`, `*.pyc`, `.pytest_cache/`, virtualenv dirs, common
editor/OS files. Decision for the MVP: `data/shipments.json` is committed once as an
empty store (`{"shipments": []}`) and remains tracked. It is **not** gitignored.
Local mutations during manual testing are expected; the README notes that the user
can `git checkout data/shipments.json` to reset it.

## Out of Scope (YAGNI)

- Real provider integrations (AfterShip / EasyPost / 17TRACK) — the seam is built,
  the implementations are not.
- Webhooks, background polling, or status-change notifications.
- Authentication, secrets, or environment-variable gating (`requires_env`).
- Concurrency / multi-process locking on the JSON store.
- Hermes hooks, slash commands, or CLI subcommands.
