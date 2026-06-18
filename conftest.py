import os
import sys

# Make the plugin modules (tools, store, schemas, providers) importable from the
# repo root during tests. Hermes adds the plugin directory to the path at runtime;
# this mirrors that for `pytest` run from the repo root.
sys.path.insert(0, os.path.dirname(__file__))
