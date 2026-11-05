"""Pytest configuration.

Unit tests import plugin modules top-level (`from ledger import Ledger`), so the plugin root goes on
sys.path. Hermes itself loads the plugin as a PACKAGE (spec_from_file_location with
submodule_search_locations), so `load_plugin_package()` reproduces that for wiring tests.
"""
import importlib.util
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT))


def load_plugin_package(name: str = "say_do_gate_under_test"):
    """Import the plugin root as a package the way Hermes does; fresh each call."""
    for mod in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
        del sys.modules[mod]
    spec = importlib.util.spec_from_file_location(
        name, str(PLUGIN_ROOT / "__init__.py"), submodule_search_locations=[str(PLUGIN_ROOT)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[name] = pkg
    spec.loader.exec_module(pkg)
    return pkg


class FakeCtx:
    """Minimal stand-in for Hermes' PluginContext: config lookup + hook capture."""

    def __init__(self, settings=None):
        self.settings = dict(settings or {})
        self.hooks = {}
        self.held = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, callback, *, hold_stream=False):
        self.hooks[name] = callback
        self.held[name] = hold_stream
