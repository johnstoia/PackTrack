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
   git clone <this-repo-url> ~/.hermes/plugins/PackTrak
   ```

2. **Enable the plugin:**

   ```bash
   hermes plugins enable PackTrak
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
