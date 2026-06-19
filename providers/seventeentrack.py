"""No-auth 17track tracking provider.

Wraps ``pyseventeentrack``'s ``client.track.find`` (the public, no-login tracking
endpoint) behind our sync ``TrackingProvider``. The library is async; ``_run_async``
bridges it from sync code safely even when a caller is already inside an event loop.
The ``pyseventeentrack`` dependency is imported lazily and, if missing, installed
once into the active venv on first use (opt out with ``PACKTRACK_NO_AUTOINSTALL=1``);
if it still can't be imported, a clear install error is surfaced.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from typing import Optional

from . import CarrierAPIError, StatusResult, TrackingNotFoundError, TrackingProvider

# Pinned dependency. Installed venv-scoped on first use if missing (see
# _import_track_api), mirroring how Hermes' own lazy_deps installs work.
_PIP_SPEC = (
    "pyseventeentrack @ git+https://github.com/johnstoia/pyseventeentrack.git@v1.1.5"
)
_install_attempted = False

# 17track overall status -> canonical status.
_STATUS_MAP = {
    "InfoReceived": "info_received",
    "InTransit": "in_transit",
    "OutForDelivery": "out_for_delivery",
    "AvailableForPickup": "available_for_pickup",
    "Delivered": "delivered",
    "DeliveryFailure": "delivery_attempted",
    "Exception": "exception",
    "Expired": "exception",
    "NotFound": "unknown",
}


def _run_async(make_coro):
    """Run an async coroutine factory to completion from sync code.

    Safe whether or not an event loop is already running in the current thread:
    if one is running, the coroutine is executed on a dedicated worker thread so
    we never call ``asyncio.run`` inside a live loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(make_coro())

    box = {}

    def _runner():
        try:
            box["value"] = asyncio.run(make_coro())
        except BaseException as exc:  # propagate to the caller's thread
            box["error"] = exc

    thread = threading.Thread(target=_runner)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


def _do_import():
    """Import the library pieces we need. Raises ImportError if not installed."""
    import aiohttp
    from pyseventeentrack import Client
    from pyseventeentrack.errors import InvalidTrackingNumberError, SeventeenTrackError

    return Client, InvalidTrackingNumberError, SeventeenTrackError, aiohttp


def _pip_install(spec: str) -> None:
    """Install ``spec`` into the active interpreter's venv (same scope Hermes uses)."""
    subprocess.run(
        [sys.executable, "-m", "pip", "install", spec],
        check=True,
        stdin=subprocess.DEVNULL,
    )


def _import_track_api():
    """Import pyseventeentrack, lazily installing it once on first miss.

    On the first ImportError it attempts a venv-scoped ``pip install`` of the
    pinned spec, then retries — unless ``PACKTRACK_NO_AUTOINSTALL`` is set, in
    which case it re-raises immediately. The install is attempted at most once
    per process; a persistent failure re-raises ImportError for the caller to map.
    """
    global _install_attempted
    try:
        return _do_import()
    except ImportError:
        if _install_attempted or os.environ.get("PACKTRACK_NO_AUTOINSTALL"):
            raise
        _install_attempted = True
        _pip_install(_PIP_SPEC)
        return _do_import()


class SeventeenTrackProvider(TrackingProvider):
    name = "17track"

    def normalize_status(self, raw: str, sub_status: Optional[str] = None) -> str:
        if sub_status and "return" in sub_status.lower():
            return "returned"
        if not raw:
            return "unknown"
        return _STATUS_MAP.get(raw, "unknown")

    def fetch_status(self, tracking_number: str, carrier: Optional[str] = None) -> StatusResult:
        pkg = self._find(tracking_number)
        latest = pkg.events[0].description if getattr(pkg, "events", None) else None
        return StatusResult(
            status=self.normalize_status(pkg.status, pkg.sub_status),
            raw_status=pkg.status or "",
            provider=self.name,
            carrier=pkg.carrier,
            sub_status=pkg.sub_status,
            detail=latest,
        )

    def _find(self, tracking_number: str):
        """Return the first TrackedPackage for ``tracking_number`` (or raise).

        Lazily imports (and on first miss installs) the dependency; maps library
        errors to our typed errors. Treat failures as transient, never "gone".
        """
        try:
            Client, InvalidTrackingNumberError, SeventeenTrackError, aiohttp = (
                _import_track_api()
            )
        except (ImportError, subprocess.CalledProcessError, OSError) as exc:
            raise CarrierAPIError(
                "pyseventeentrack is not installed and could not be auto-installed. "
                'Install it manually: pip install "pyseventeentrack @ '
                'git+https://github.com/johnstoia/pyseventeentrack.git@v1.1.5"'
            ) from exc

        async def _call():
            async with aiohttp.ClientSession() as session:
                client = Client(session=session)
                return await client.track.find(tracking_number)

        try:
            packages = _run_async(_call)
        except InvalidTrackingNumberError as exc:
            raise TrackingNotFoundError(
                f"no tracking data for {tracking_number}"
            ) from exc
        except SeventeenTrackError as exc:
            raise CarrierAPIError(f"tracking temporarily unavailable: {exc}") from exc

        if not packages:
            raise TrackingNotFoundError(f"no tracking data for {tracking_number}")
        return packages[0]
