"""Tests for the MCP resource registry (no Cascadeur needed).

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILTIN_URIS = {
    "cascadeur://scene",
    "cascadeur://log",
    "cascadeur://timeline",
    "cascadeur://controllers",
}


def load_resources_module():
    """Load resources.py without importing the package (its __init__ needs Cascadeur's Python)."""
    path = os.path.join(ROOT, "cascadeur_mcp_plus", "resources.py")
    spec = importlib.util.spec_from_file_location("resources_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


class ResourceRegistryTests(unittest.TestCase):
    def test_builtin_resources_are_complete_and_unique(self):
        module = load_resources_module()
        resources = module.load_resources()
        uris = [resource["uri"] for resource in resources]
        self.assertEqual(len(uris), len(set(uris)))
        self.assertEqual(set(uris), BUILTIN_URIS)
        for resource in resources:
            self.assertTrue(resource["uri"].startswith("cascadeur://"))
            for key in ("name", "description", "mimeType", "fn"):
                self.assertIn(key, resource)
            self.assertTrue(callable(resource["fn"]))
            self.assertIn(resource["mimeType"], ("application/json", "text/plain"))
            metadata = {key: resource[key] for key in ("uri", "name", "description", "mimeType")}
            self.assertEqual(json.loads(json.dumps(metadata)), metadata)

    def test_log_resource_runs_outside_the_gui_thread(self):
        module = load_resources_module()
        log = next(resource for resource in module.load_resources() if resource["uri"] == "cascadeur://log")
        self.assertIs(log.get("gui"), False)

    def test_sibling_resource_modules_are_loaded_automatically(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = os.path.join(tmp, "probe_pkg")
            os.makedirs(package)
            with open(os.path.join(package, "__init__.py"), "w", encoding="utf-8"):
                pass
            shutil.copy(os.path.join(ROOT, "cascadeur_mcp_plus", "resources.py"), package)
            with open(os.path.join(package, "resources_probe.py"), "w", encoding="utf-8") as handle:
                handle.write(
                    'RESOURCES = [{"uri": "probe://extra", "name": "Extra", "description": "extra", '
                    '"mimeType": "text/plain", "fn": lambda: "extra"}]\n'
                )
            sys.path.insert(0, tmp)
            try:
                module = importlib.import_module("probe_pkg.resources")
                uris = [resource["uri"] for resource in module.load_resources()]
            finally:
                sys.path.remove(tmp)
                for name in [n for n in sys.modules if n == "probe_pkg" or n.startswith("probe_pkg.")]:
                    del sys.modules[name]
        self.assertIn("probe://extra", uris)
        self.assertTrue(BUILTIN_URIS.issubset(set(uris)))


if __name__ == "__main__":
    unittest.main()
