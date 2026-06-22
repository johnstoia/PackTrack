"""Test bootstrap: load this plugin as a package named ``packtrack``.

Hermes loads ``~/.hermes/plugins/<name>/`` as a Python package, so the plugin's
modules import their siblings with relative imports (``from . import ...``). To
exercise that exact import path, we register this repo directory as the
``packtrack`` package before any test imports it. The fixed name ``packtrack`` is
independent of the local checkout's folder name.
"""
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "packtrack",
    _ROOT / "__init__.py",
    submodule_search_locations=[str(_ROOT)],
)
_pkg = importlib.util.module_from_spec(_spec)
sys.modules["packtrack"] = _pkg
_spec.loader.exec_module(_pkg)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: live tests that hit real carrier APIs; require credentials, "
        "skipped by default.",
    )


import os as _os

_os.environ.setdefault("PACKTRACK_NO_AUTOINSTALL", "1")
