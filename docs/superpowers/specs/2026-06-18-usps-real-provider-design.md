# Real Carrier Providers — USPS (Sub-project #1) Design

**Date:** 2026-06-18
**Branch:** `feature/real-carrier-providers`
**Status:** Approved design — ready for implementation plan

## Context & Scope

PackTrak currently answers `shipment_get_status` from a deterministic **mock** provider.
This sub-project replaces mock with **real USPS tracking** behind the existing provider
seam, and establishes the shared OAuth/HTTP machinery + carrier router that UPS and
FedEx will reuse as small follow-ups.

This is the first of three sequenced sub-projects (each its own spec → plan →
implementation):

1. **Real carrier providers (USPS first) — THIS spec.** Makes `get_status` real;
   builds the `OAuthCarrierProvider` base + carrier router. UPS/FedEx are later
   additive providers.
2. **Status-change alerts** — change detection (store begins persisting last-known
   status), a trigger (polling via Hermes `cronjob` is the leaning; webhooks are
   inconsistent across carriers and need public ingress), and a notification channel.
3. **Delivered lifecycle** — prompt-for-deletion once a shipment is delivered; built
   on #2.

Items 4 and 5 from the product list (alerts, delivered-prompt) are **explicitly out
of scope here.**

### Constraints

- **Per-carrier credentials are supplied by the user** via environment variables.
  No secrets are committed or generated. USPS/UPS/FedEx tracking is free-tier (no paid
  spend); the original "no paid APIs / no secrets" MVP constraint is relaxed only to
  the extent of reading user-provided env vars and making free tracking calls.
- **Standard library only at runtime** — HTTP via `urllib`. No new dependencies.
- **The plugin must always load**, even with no credentials set. Gating is per-carrier
  at call time, never via `plugin.yaml: requires_env` (which would disable the whole
  plugin).

## Architecture

```
providers/
├── __init__.py        # CHANGED: statuses/StatusResult/ABC kept; get_provider() becomes a router; ProviderError hierarchy added
├── mock.py            # unchanged (fallback / default backend)
├── http_client.py     # NEW: stdlib urllib JSON HTTP — token POST + bearer GET, timeouts, status-code → typed errors
├── oauth_carrier.py   # NEW: OAuthCarrierProvider base — env-var creds, token fetch + cache (expiry), fetch_status template
└── usps.py            # NEW: USPSProvider — endpoints, request/response shape, USPS → canonical status map
```

### Routing

`get_provider(carrier)` resolves a shipment's stored `carrier` slug to a provider:

- `"usps"` → `USPSProvider` (later: `"ups"`, `"fedex"`)
- `"mock"` → `MockProvider` (keeps existing calls/tests working)
- `None` / empty / unrecognized → `MockProvider` (plugin stays useful for shipments
  without a supported carrier)

Carrier matching is case-insensitive (normalize to lowercase). The signature keeps a
default of `"mock"` for backward compatibility with existing callers/tests.

### Shared base — `OAuthCarrierProvider(TrackingProvider)`

Owns the pattern all three carriers share:

- Read credentials from named env vars; raise `CredentialsMissingError` if absent.
- Fetch an OAuth client-credentials token; cache it in a **module-level cache keyed by
  carrier** with expiry = `expires_in` minus a 60s safety margin; reuse until expired.
- Call the tracking endpoint with `Authorization: Bearer <token>`; on `401`, refresh
  the token once and retry.
- Parse JSON; translate failures into typed errors.

Subclasses supply only: env var names, base URL/env override, token path, tracking
path + query, response→raw-status extraction, and the status mapping.

### Error model (`providers/__init__.py`)

```
ProviderError(Exception)
├── CredentialsMissingError    # required env vars not set
├── TrackingNotFoundError      # carrier reports the number is unknown (HTTP 404)
└── CarrierAPIError            # transport error, timeout, 401-after-refresh, 429/503, other 4xx/5xx
```

`fetch_status` raises these; `shipment_get_status` catches them and returns
`{"error": <message>}`. Handlers still never raise.

## USPS Provider (locked to USPS Tracking v3 OpenAPI spec)

### Endpoints

- `USPS_API_BASE` (env, optional) = `https://apis.usps.com` (prod, default) or
  `https://apis-tem.usps.com` (test). This is the **host root** — the token path is
  not under `/tracking/v3`.
- **Token:** `POST {base}/oauth2/v3/token` — OAuth2 client-credentials, scope
  `tracking`. Request body form-encoded:
  `grant_type=client_credentials&client_id=<key>&client_secret=<secret>&scope=tracking`.
  (The spec documents the token URL but not the body shape; the opt-in integration
  test confirms it. If USPS requires a JSON body instead, it is a one-line change.)
- **Tracking:** `GET {base}/tracking/v3/tracking/{trackingNumber}?expand=DETAIL` with
  `Authorization: Bearer <token>` and `Accept: application/json`.

### `expand=DETAIL` is mandatory

The default `SUMMARY` returns `TrackingSummary` (only `eventSummaries[]` free-text
strings — no status field). Only `DETAIL` returns `TrackingDetails` with the
structured status fields below.

### Credentials

- `USPS_CONSUMER_KEY`, `USPS_CONSUMER_SECRET` (the OAuth consumer key/secret from
  developer.usps.com). Missing either → `CredentialsMissingError("USPS credentials not
  configured: set USPS_CONSUMER_KEY and USPS_CONSUMER_SECRET")`.

### Status extraction & mapping

From the `TrackingDetails` response, read the first present, in priority order:

1. `statusCategory` — item status category (primary signal)
2. `status` — item status (fallback)
3. `statusSummary` or `trackingEvents[0].eventType` — last resort
   (`trackingEvents` is reverse-chronological; `[0]` is most recent)

USPS does not enumerate `statusCategory` values in the spec (it points to Pub 199
Appendix G-4), so `USPSProvider.normalize_status` maps by **case-insensitive keyword
match** against the vocabulary the spec's examples reveal, defaulting to `unknown`:

| USPS keyword (case-insensitive) | Canonical |
|---|---|
| `delivered` | `delivered` |
| `out for delivery` | `out_for_delivery` |
| `in transit`, `arrived`, `departed`, `accepted`, `picked up` | `in_transit` |
| `pre-shipment`, `shipping label created`, `awaiting item` | `pending` |
| `received` (label/info received, awaiting item) | `info_received` |
| `available for pickup` | `available_for_pickup` |
| `delivery attempt`, `notice left`, `attempted` | `delivery_attempted` |
| `alert`, `exception` | `exception` |
| `return to sender`, `returned` | `returned` |
| anything else | `unknown` |

Order the keyword checks so more-specific phrases win (e.g. "out for delivery" before
"delivery", "available for pickup" before "pickup").

`fetch_status` returns `StatusResult(status=<canonical>, raw_status=<the USPS string
used>, provider="usps")`.

### HTTP error handling (from the spec's documented responses)

- `404` (Resource Not Found) → `TrackingNotFoundError`.
- `401` (Unauthorized) → refresh token once and retry; if still `401` →
  `CarrierAPIError`.
- `429` / `503` (both carry `Retry-After`) and other 4xx/5xx → `CarrierAPIError`,
  surfacing `error.message` from the `ErrorMessage` body
  (`{apiVersion, error:{code, message, errors:[...]}}`) when present.
- Transport errors / timeouts (`urllib.error.URLError`, socket timeout) →
  `CarrierAPIError`. Default timeout 10s.

### `http_client.py` seam

Two small functions so providers stay testable without network:

- `post_form(url, data: dict, headers: dict, timeout=10) -> dict` — POST form-encoded,
  return parsed JSON; raise `CarrierAPIError` on transport failure.
- `get_json(url, headers: dict, timeout=10) -> dict` — GET, return parsed JSON; map
  HTTP status codes to typed errors as above.

Unit tests monkeypatch these two functions.

## Integration points

- **`tools.py` — `shipment_get_status`:**
  `get_provider(record["carrier"]).fetch_status(tracking_number, record["carrier"])`,
  wrapped to catch `ProviderError` → `{"error": <message>}`. Output `provider` field
  reflects the resolved backend. The add/list/remove handlers are unchanged.
- **`plugin.yaml`:** version → `0.2.0`. No `requires_env` (per-carrier runtime gating).
- **Store / data model:** unchanged. Status stays computed-on-demand; routing uses the
  already-stored `carrier`. To get real tracking, add a shipment with `carrier: "usps"`;
  unknown/blank carrier routes to mock.
- **README:** add a "Carriers & credentials" section — USPS env vars, where to obtain
  them (developer.usps.com), the test-env base, the integration-test command, and the
  "add with `carrier: usps`" note.

## Testing

### Unit tests (hermetic — monkeypatch `http_client.post_form` / `get_json`)

- `USPSProvider.normalize_status`: representative `statusCategory`/`status` values →
  correct canonical; unrecognized → `unknown`; specificity ordering (e.g. "Out for
  Delivery" → `out_for_delivery`, not `delivery_attempted`).
- `fetch_status` happy path: canned `TrackingDetails` JSON → `StatusResult(status,
  raw_status, provider="usps")`.
- `CredentialsMissingError` when env vars unset.
- `TrackingNotFoundError` on 404; `CarrierAPIError` on 500 and on simulated timeout,
  surfacing `error.message`.
- Token caching: two `fetch_status` calls within expiry → `post_form` (token) called
  once.
- Router: `get_provider("usps")` → `USPSProvider`; `get_provider(None)` /
  unrecognized / `get_provider("mock")` → `MockProvider`; matching is
  case-insensitive.
- Handler: `shipment_get_status` with `carrier="usps"` + missing creds →
  `{"error": ...}`; with the HTTP seam mocked → success JSON including
  `provider="usps"` and a canonical `status`.
- All existing mock/store/schema/register tests remain green (19 today).

### Opt-in live integration test

- Marked with a registered `integration` pytest marker; **skipped unless**
  `USPS_CONSUMER_KEY`, `USPS_CONSUMER_SECRET`, and `USPS_TEST_TRACKING_NUMBER` are set.
- Hits real USPS (honoring `USPS_API_BASE`), asserts a `StatusResult` whose `status` is
  one of `CANONICAL_STATUSES` and `provider == "usps"`.
- Normal `pytest` runs stay hermetic and require no credentials. README documents
  `pytest -m integration`.

## Out of Scope (YAGNI / later sub-projects)

- UPS and FedEx providers (small additive follow-ups to this base).
- Status-change alerts and the store persisting last-known status (sub-project #2).
  Note: USPS exposes `POST /tracking/{trackingNumber}/notifications` (Tracking by
  Email) — a candidate for USPS-specific alerts in #2, though polling remains the
  uniform cross-carrier path.
- Delivered → prompt-for-deletion (sub-project #3).
- Tracking-number → carrier auto-detection (carrier is supplied at add time).
- Concurrency hardening of the token cache beyond a simple module-level cache.
