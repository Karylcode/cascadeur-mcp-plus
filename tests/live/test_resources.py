"""Live tests for MCP resources (T40).

Run through tests/live/run.py after taking the live-test lock. The tests only read; they open
their own Cascy.casc tab for the scene, timeline and controllers resources and tear it down
without saving.
"""

from __future__ import annotations

import json
import unittest

try:
    from . import client
    from .fixture import LiveTestCase
except ImportError:
    import client
    from fixture import LiveTestCase

BUILTIN_URIS = {
    "cascadeur://scene",
    "cascadeur://log",
    "cascadeur://timeline",
    "cascadeur://controllers",
}
CONTROLLER_KINDS = {"main_point", "direction", "box", "other"}


class ResourceTests(LiveTestCase):
    def rpc(self, method, params=None):
        response = client._rpc(method, params or {})
        return response.get("result") or {}

    def read(self, uri):
        result = self.rpc("resources/read", {"uri": uri})
        contents = result.get("contents") or []
        self.assertEqual(len(contents), 1, f"expected one content item for {uri}")
        item = contents[0]
        self.assertEqual(item.get("uri"), uri)
        self.assertIn(item.get("mimeType"), ("application/json", "text/plain"))
        text = item.get("text")
        self.assertIsInstance(text, str)
        if item.get("mimeType") == "application/json":
            return json.loads(text)
        return text

    def test_initialize_advertises_resources(self):
        result = self.rpc("initialize", {"protocolVersion": "2025-06-18"})
        capabilities = result.get("capabilities") or {}
        self.assertIn("resources", capabilities)
        self.assertIn("tools", capabilities)

    def test_lists_builtin_resources(self):
        self.open_sample("Cascy.casc")
        resources = self.rpc("resources/list").get("resources") or []
        uris = {resource["uri"] for resource in resources}
        self.assertTrue(BUILTIN_URIS.issubset(uris), f"missing resources: {BUILTIN_URIS - uris}")
        for resource in resources:
            self.assertIsInstance(resource.get("name"), str)
            self.assertIsInstance(resource.get("description"), str)
            self.assertIn(resource.get("mimeType"), ("application/json", "text/plain"))

    def test_reads_scene_and_timeline(self):
        self.open_sample("Cascy.casc")
        scene = self.read("cascadeur://scene")
        self.assertIsInstance(scene["scene"]["name"], str)
        self.assertTrue(scene["scene"]["path"].lower().endswith("cascy.casc"))
        self.assertEqual(
            set(scene["frames"]),
            {"current", "first", "last", "animation_size"},
        )
        timeline = self.read("cascadeur://timeline")
        self.assertEqual(timeline, scene["frames"])

    def test_reads_controllers(self):
        self.open_sample("Cascy.casc")
        data = self.read("cascadeur://controllers")
        self.assertGreater(data["count"], 0, "Cascy should expose Point control points")
        self.assertEqual(len(data["controllers"]), min(data["count"], 500))
        for controller in data["controllers"]:
            self.assertIsInstance(controller["name"], str)
            self.assertIn(controller["kind"], CONTROLLER_KINDS)
        self.assertTrue(
            any(controller["kind"] == "main_point" for controller in data["controllers"]),
            "expected at least one main_point controller in Cascy",
        )

    def test_reads_log(self):
        log = self.read("cascadeur://log")
        self.assertTrue(log.strip())
        self.assertNotIn("No log lines found", log)

    def test_unknown_resource_is_an_error(self):
        with self.assertRaises(client.LiveError):
            client._rpc("resources/read", {"uri": "cascadeur://does-not-exist"})


if __name__ == "__main__":
    unittest.main()
