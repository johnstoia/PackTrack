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
   git clone <this-repo-url> ~/.hermes/plugins/packtrack
   ```

2. **Enable the plugin:**

   ```bash
   hermes plugins enable packtrack
   ```

   Troubleshoot discovery with:

   ```bash
   HERMES_PLUGINS_DEBUG=1 hermes plugins list
   ```

3. **Enable the `shipment` toolset.** The plugin registers its four tools under a
   `shipment` toolset, and Hermes toolsets are opt-in, so enable it once:

   ```bash
   hermes tools enable shipment
   ```

   (If this reports `Unknown toolset 'shipment'`, the plugin failed to load — see
   [Troubleshooting](#troubleshooting) below.)

4. **Restart Hermes (or the Hermes gateway)** so the plugin and toolset are loaded
   into a fresh session.

5. **Test manually** in a Hermes session — ask the agent to:
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

**Intra-plugin imports must be relative.** Hermes loads `~/.hermes/plugins/<name>/`
as a Python **package**, so modules import their siblings with relative imports
(`from . import schemas, tools`, `from .providers import get_provider`). Absolute
imports like `import schemas` will raise `ModuleNotFoundError` at load time, Hermes
will silently disable the plugin's tools, and the toolset will never appear. The
test harness (`conftest.py`) loads this directory as the `packtrack` package so the
tests exercise that exact import path.

## Troubleshooting

**Tools don't appear / `hermes tools enable shipment` says `Unknown toolset`.**
This means `register()` didn't run — almost always a plugin load error. Check the log:

```bash
grep -i packtrack ~/.hermes/logs/agent.log | tail -5
```

A line like `Failed to load plugin 'packtrack': No module named 'schemas'` confirms an
import problem (see "intra-plugin imports must be relative" above). After fixing and
pulling, restart Hermes and re-run `hermes tools enable shipment`.

## How tracking works

`shipment_get_status` returns live tracking for any carrier with **no API key, no
login, and no account.** It uses [`pyseventeentrack`](https://github.com/johnstoia/pyseventeentrack)
(`client.track.find`), which reads 17track.net's public tracking endpoint; the carrier
is auto-detected from the tracking number.

**Dependency install is automatic.** On the first real tracking call, the plugin
installs `pyseventeentrack` into the environment Hermes runs in (a venv-scoped
`pip install` of the pinned spec), then proceeds. No setup step is required.

To install it ahead of time (or if auto-install is disabled), do it manually into
Hermes's venv:

```bash
~/.hermes/hermes-agent/venv/bin/pip install -r ~/.hermes/plugins/packtrack/requirements.txt
# the pin: pyseventeentrack @ git+https://github.com/johnstoia/pyseventeentrack.git@v1.1.5
```

Set `PACKTRACK_NO_AUTOINSTALL=1` to disable the auto-install; the plugin then returns
a clear "install pyseventeentrack" error until the dependency is present. Add a
shipment with `carrier: mock` to force the deterministic **mock** backend for
offline/testing (the `provider` field in the output always shows `17track` vs `mock`).

**This is a community/unofficial integration**, not an official 17track product: it
relies on an undocumented public endpoint that can change. Tracking failures are
treated as transient ("temporarily unavailable"), never as "package gone." Keep
polling light — this plugin is intended to run per-user, not as a shared server.

### Live integration test

The unit suite is hermetic (the library boundary is mocked). One opt-in test makes a
real network call and is skipped unless `PACKTRACK_LIVE_TRACKING_NUMBER` is set (and
the dependency is installed):

```bash
PACKTRACK_LIVE_TRACKING_NUMBER=9400111899223817428490 pytest -m integration
```

## Swapping the provider

`providers/__init__.py` defines the `TrackingProvider` ABC and `get_provider()`
factory. To add a real backend:

1. Create `providers/aftership.py` with a `class AfterShipProvider(TrackingProvider)`
   implementing `normalize_status` and `fetch_status`.
2. Register its name in `get_provider`.
3. Point `tools.get_provider()` at the new name.

No tool handler changes are required.
