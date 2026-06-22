# PackTrak — Hermes Shipment Tracker

A Hermes Agent plugin that tracks package shipments. It returns live tracking for any
carrier with **no API key, login, or account** (via the no-auth 17track backend), and
falls back to a deterministic mock backend for offline/testing. The provider layer is
a swappable seam, so new backends drop in without changing the tool handlers.

## Tools

| Tool | Purpose |
|------|---------|
| `shipment_add_tracking` | Track a number (optional carrier + label). Rejects empty and duplicate numbers. |
| `shipment_get_status` | Get normalized delivery status for a tracked number. |
| `shipment_list_tracked` | List all tracked shipments. |
| `shipment_remove_tracking` | Stop tracking a number. |
| `shipment_check_updates` | Re-check monitored shipments, report only what changed, and auto-prune finished ones. |
| `shipment_set_monitoring` | Turn monitoring on/off for a shipment. |
| `shipment_prune` | Manually remove finished (delivered/stale) shipments now. |

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

3. **Enable the `shipment` toolset.** The plugin registers its tools under a
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

### Status freshness (why a first check can say "fetching")

17track's public endpoint returns an empty snapshot and re-syncs from the carrier in
the background (a few seconds, sometimes ~10s), so a single read can lag. The plugin
handles this:

- `shipment_get_status` briefly retries, then — if still no fresh data — returns the
  **last known status** ("as of last check") rather than a misleading "unknown." For a
  brand-new number with no history, it asks you to check again in ~10 seconds.
- `shipment_check_updates` primes, waits, and re-reads, so scheduled monitoring sees
  fresh data and keeps the stored status current.

Tunable via env vars (defaults shown):

| Env var | Default | Effect |
|---|---|---|
| `PACKTRACK_GET_STATUS_RETRIES` | `1` | extra live reads on a blank `get_status` (set `0` for instant stored fallback) |
| `PACKTRACK_GET_STATUS_RETRY_DELAY` | `3.0` | seconds between those reads |
| `PACKTRACK_CHECK_REFRESH_WAIT` | `12.0` | seconds `check_updates` waits before re-reading blanks |

If your Hermes setup enforces a short tool-call timeout, lower
`PACKTRACK_CHECK_REFRESH_WAIT` and/or set `PACKTRACK_GET_STATUS_RETRIES=0`.

### Live integration test

The unit suite is hermetic (the library boundary is mocked). One opt-in test makes a
real network call and is skipped unless `PACKTRACK_LIVE_TRACKING_NUMBER` is set (and
the dependency is installed):

```bash
PACKTRACK_LIVE_TRACKING_NUMBER=9400111899223817428490 pytest -m integration
```

## Monitoring & alerts

The plugin remembers each shipment's last-seen activity, so it can tell you **only
what changed**. Two tools support this:

- `shipment_check_updates` — re-checks every monitored shipment and returns only new
  activity (or "no changes"). Delivered shipments stop being monitored automatically,
  and finished ones are auto-pruned (see below).
- `shipment_set_monitoring` — turn watching on/off for a shipment.
- `shipment_prune` — manually run the cleanup now (see below).

**Automatic monitoring is delegated to Hermes's scheduler** (a plugin can't run its
own background loop). Set it up once by asking the agent, e.g.:

> "Set up a recurring job: every 4 hours, run the shipment update check and only
> message me if something changed."

Hermes runs it on schedule and the agent relays results on whatever platform you use
(Telegram, Discord, CLI, …) — nothing platform-specific is built into the plugin.

Notes:
- Whether a scheduled run stays **silent** when there are no changes depends on
  Hermes's cron delivery, not the plugin. The plugin returns a clean "no changes"
  signal; if a run still posts "nothing new," tweak the cron prompt.
- **First lookup:** a brand-new tracking number returns no data until 17track fetches
  it from the carrier (seconds–minutes). Adding it says "tracking started — initial
  data may take a few minutes"; the next check fills it in.

### Delivered lifecycle / auto-prune

Finished shipments are removed automatically so the list stays clean. The same
`shipment_check_updates` run that powers monitoring also sweeps the **entire** store
and hard-deletes:

- **delivered** packages once they've been delivered for `PACKTRACK_DELIVERED_PRUNE_DAYS`
  (default **3 days**) — so you keep seeing "delivered" for a few days, then it drops off;
- **undelivered** packages still being monitored that have had **no status change** for
  `PACKTRACK_STALE_PRUNE_DAYS` (default **30 days**) — lost numbers, typos, or
  never-scanned labels. A shipment you've deliberately stopped monitoring is never
  stale-pruned.

Pruning is by tracking *activity*, not by when it was last polled. On the first sweep
after upgrading, any pre-existing record is given a fresh clock and is **not** deleted
that round, so nothing vanishes unexpectedly.

For on-demand cleanup, `shipment_prune` runs the same logic immediately. Pass
`delivered_now: true` to remove **all** delivered packages right away, ignoring the
3-day grace ("clean up my delivered packages now"). To remove one specific shipment at
any time, use `shipment_remove_tracking`.

| Env var | Default | Effect |
|---|---|---|
| `PACKTRACK_DELIVERED_PRUNE_DAYS` | `3` | days after delivery before auto-removal |
| `PACKTRACK_STALE_PRUNE_DAYS` | `30` | days of no status change before an undelivered, monitored package is removed |

## Swapping the provider

`providers/__init__.py` defines the `TrackingProvider` ABC and `get_provider()`
factory. To add a real backend:

1. Create `providers/aftership.py` with a `class AfterShipProvider(TrackingProvider)`
   implementing `normalize_status` and `fetch_status`.
2. Register its name in `get_provider`.
3. Point `tools.get_provider()` at the new name.

No tool handler changes are required.
