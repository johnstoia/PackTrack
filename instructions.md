# Plugin Design Brief: Package Tracking via `pyseventeentrack`

> **Purpose of this document.** This is a briefing for an AI assistant helping
> design a package-tracking plugin. It explains the tracking library the plugin
> will use, the architecture constraints, the exact API surface, and the
> intended plugin behavior. Read it as the source of truth for how to use the
> library; everything here has been tested against the live service.

---

## 1. TL;DR

- The plugin tracks packages by **tracking number only — no login, no API key,
  no 17track account.**
- It uses a forked library: **`pyseventeentrack`** pinned to
  **`git+https://github.com/johnstoia/pyseventeentrack.git@v1.1.5`**.
- The one call that matters is **`await client.track.find(*tracking_numbers)`**,
  which returns each package's status and full event history.
- The library is **async** (`asyncio` + `aiohttp`).
- The plugin is **deployed per-user (each user runs their own instance)**, which
  is what makes the no-auth approach safe — see §3.
- There is **no webhook/push**; the plugin **polls** (see §8 for cadence).

---

## 2. The library: what, where, install

`pyseventeentrack` is a Python library that talks to 17track.net. It has two
halves:

1. **Account methods** (`client.profile.*`) — login, list, add, rename, archive
   packages in a 17track *account*. **The plugin does NOT use these.** They
   require a username/password login.
2. **Tracking** (`client.track.find`) — fetches a package's status and full
   event history from 17track's **public** tracking endpoint. **No login.**
   This is the only part the plugin uses.

**Install / depend on it** (pin to the tag for reproducibility):

```toml
# pyproject.toml
dependencies = [
    "pyseventeentrack @ git+https://github.com/johnstoia/pyseventeentrack.git@v1.1.5",
]
```
```text
# or requirements.txt
pyseventeentrack @ git+https://github.com/johnstoia/pyseventeentrack.git@v1.1.5
```

---

## 3. Architecture constraints (read before designing)

- **Per-user / distributed deployment.** Each user runs their own instance, so
  polling goes out from each user's own machine/IP. Do **not** design a central
  server that polls 17track on behalf of all users — that funnels all traffic
  through one IP against an unofficial endpoint and will get rate-limited or
  blocked. Distributed polling is the safe pattern and the whole reason no
  shared API key is needed.
- **Unofficial endpoint.** `client.track.find` calls a public endpoint the
  17track web app uses (`t.17track.net/track/restapi`). It is not an official,
  documented API. It works today and is tested, but it can change without
  notice. All the fragile detail (URL + required headers) is isolated in one
  file (`pyseventeentrack/track.py`), so if it breaks, the fix is a one-file
  patch + a new tag, and instances bump their pin.
- **No webhooks.** This endpoint is request/response only. The plugin must
  **poll**. (17track *does* offer an official API with push/webhooks, but it
  requires per-user API keys and quotas, which this design intentionally
  avoids.)

---

## 4. API reference

### Client

```python
from pyseventeentrack import Client

client = Client()                    # creates a new connection per call
# or, for connection pooling across many calls:
# async with aiohttp.ClientSession() as session:
#     client = Client(session=session)
```

### `await client.track.find(*tracking_numbers) -> list[TrackedPackage]`

- Pass one or more tracking numbers. **Batch a user's numbers into one call**
  (e.g. `find("A", "B", "C")`) — one request instead of N.
- No authentication required.
- Carrier is **auto-detected** (you don't pass it).
- Raises `InvalidTrackingNumberError` if the response contains no shipments
  (e.g. a bogus number). Raises `RequestError` on HTTP/network errors. Both
  subclass `SeventeenTrackError`.

### `TrackedPackage`

| Field | Type | Notes |
|---|---|---|
| `tracking_number` | `str` | the number you queried |
| `carrier` | `str \| None` | auto-detected, e.g. `"USPS"` |
| `status` | `str \| None` | overall status, e.g. `"InTransit"`, `"Delivered"` |
| `sub_status` | `str \| None` | finer status, e.g. `"InTransit_PickedUp"` |
| `events` | `list[TrackingEvent]` | full history, **newest first** |
| `events_hash` | `int \| None` | hash of the event history; **changes when there's new activity** — use for change detection |
| `latest_sync_time` | `str \| None` | ISO 8601 UTC; when 17track last re-synced from the carrier |

### `TrackingEvent`

| Field | Type | Notes |
|---|---|---|
| `timestamp` | `str \| None` | ISO 8601 in the event's local time zone |
| `timestamp_utc` | `str \| None` | ISO 8601 in UTC |
| `description` | `str \| None` | human-readable event text |
| `location` | `str \| None` | free-text location |
| `stage` | `str \| None` | tracking stage if provided |
| `sub_status` | `str \| None` | status this event represents |
| `city` / `state` / `country` | `str \| None` | structured location |

### Errors

```python
from pyseventeentrack.errors import SeventeenTrackError, InvalidTrackingNumberError
# Wrap find() in try/except SeventeenTrackError. Treat failures as
# "retry later", never as "package is gone".
```

---

## 5. Status values

Confirmed live: `status == "InTransit"`, with `sub_status` values like
`"InTransit_Other"`, `"InTransit_PickedUp"`, `"InfoReceived"`.

The delivery trigger is expected to be **`status == "Delivered"`**, but this has
**not yet been confirmed against a real delivered package** — verify the exact
string with a delivered package before hard-coding the delivery branch. If it
differs, it's a one-line change in the plugin. Other 17track statuses you may
encounter include `NotFound`, `InfoReceived`, `Expired`, `AvailableForPickup`,
`DeliveryFailure`, and `Exception` — handle unknown/unexpected statuses
gracefully rather than assuming the set is fixed.

---

## 6. The plugin we are building

An AI-agent tool. Flow:

1. The agent calls the plugin with a **tracking number** (+ optional
   **description** and **carrier**, which are plugin-side metadata only — the
   library auto-detects carrier and does not need either).
2. The plugin returns the package's **current status and event information**.
3. If the user opted in to monitoring, the plugin **watches for changes** and
   sends the user a **package update** when new activity appears.
4. On **delivery**, the plugin tells the user the package was delivered, then
   **asks whether to stop tracking it**.
5. If yes, the plugin **removes the package from its own tracking list**
   (purely plugin-side state; no library/account call involved).

---

## 7. Implementation guidance

### On-demand status lookup (the agent tool call)

```python
from pyseventeentrack import Client
from pyseventeentrack.errors import SeventeenTrackError

async def get_status(tracking_number: str):
    try:
        packages = await Client().track.find(tracking_number)
    except SeventeenTrackError:
        return None  # surface "couldn't reach tracking right now"
    pkg = packages[0]
    return {
        "carrier": pkg.carrier,
        "status": pkg.status,
        "sub_status": pkg.sub_status,
        "latest": pkg.events[0].description if pkg.events else None,
        "events": [
            {"time": e.timestamp, "location": e.location, "text": e.description}
            for e in pkg.events
        ],
    }
```

### Background monitoring loop (change + delivery detection)

Persist a little state **per tracked package**:

```text
{ tracking_number, last_events_hash, last_status, monitor: bool, ... }
```

```python
async def poll_once(record):
    try:
        pkg = (await Client().track.find(record.tracking_number))[0]
    except SeventeenTrackError:
        return  # transient: try again next cycle, do NOT drop the package

    # 1. New activity?  (cheap: compare the hash)
    if pkg.events_hash != record.last_events_hash:
        if record.monitor:
            notify_update(record, pkg.events)   # the newest events are events[0:]
        record.last_events_hash = pkg.events_hash

    # 2. Delivered?
    if pkg.status == "Delivered" and record.last_status != "Delivered":
        notify_delivered(record, pkg)
        ask_user_to_stop_tracking(record)       # "yes" -> drop record from your store

    record.last_status = pkg.status
```

### Change detection: use `events_hash`

`events_hash` changes when (and only when) the event history changes. Comparing
the stored hash to the new one is cheaper and more reliable than diffing event
lists. When it changes, the new events are at the **front** of `events` (newest
first); diff against your last-seen newest timestamp if you need the exact
delta.

### Polling cadence: tiered, not fixed

There is no push, so poll — but tier the interval by how close to delivery the
package is, and never poll faster than 17track itself re-syncs
(`latest_sync_time`):

| Package state | Suggested interval |
|---|---|
| `InfoReceived` / early transit | every 6–12h |
| `InTransit` (moving) | every 3–6h |
| near delivery (`OutForDelivery`, etc.) | every 1–2h |
| `Delivered` / terminal | stop polling |

Add **jitter** (randomize within the interval) and **stagger** packages so you
don't fire them all at the same instant. On error, **back off** and retry later.

---

## 8. Things to confirm / caveats

- **Confirm the `"Delivered"` status string** against a real delivered package
  before shipping the delivery trigger (see §5).
- **Treat tracking failures as transient.** A failed `find()` means "retry
  later," never "package gone." Don't drop packages on error.
- **Degrade gracefully.** Because every user depends on one unofficial endpoint,
  design so that a tracking outage produces a clear "temporarily unavailable"
  message, not a crash or data loss. That lets the maintainer ship a library
  patch (new tag) without users losing their tracked packages.
- **Unofficial / ToS.** Automating an unofficial endpoint is a gray area. Keep
  per-instance polling light, and tell users this is a community/unofficial
  integration, not an official 17track product.

---

## 9. What NOT to do

- ❌ Don't use `client.profile.*` (login/add/archive) — those need an account
  login and are not part of this design.
- ❌ Don't centralize polling on one server for all users (see §3).
- ❌ Don't poll on a tight fixed interval or faster than `latest_sync_time`
  changes — it returns identical data and risks rate-limiting.
- ❌ Don't treat a failed/empty response as "package delivered/removed."
- ❌ Don't assume the status enum is fixed — handle unexpected values.

---

## 10. Quick reference

```python
from pyseventeentrack import Client
from pyseventeentrack.errors import SeventeenTrackError

pkg = (await Client().track.find("9305520762601257572770"))[0]
pkg.status          # "InTransit"
pkg.carrier         # "USPS"
pkg.events_hash     # 647248911  -> changed since last poll == new activity
pkg.latest_sync_time# "2026-06-19T16:12:22Z"
pkg.events[0]       # newest TrackingEvent: .timestamp, .location, .description
```

Dependency pin: `git+https://github.com/johnstoia/pyseventeentrack.git@v1.1.5`
