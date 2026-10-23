"""One basic, non-destructive call per tool that existed before T02.

Everything runs inside a Cascy.casc test scene opened by the fixture, so even
ui_drag and enter_rig_mode cannot touch the user's scene. tearDown discards the
test scene without saving and checks the user's scene list and frame are unchanged.

Run with: python tests/live/run.py test_smoke.py
"""

from __future__ import annotations

import unittest

try:
    from . import client
    from .fixture import LiveTestCase
except ImportError:
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]


# The 18 tools that existed when this ticket was written. Later tickets add tools;
# they add their own live tests and do not need to extend this set.
EXPECTED_TOOLS = {
    "run_python",
    "get_scene_info",
    "get_object_info",
    "enter_rig_mode",
    "get_log",
    "ui_tree",
    "ui_click",
    "ui_drag",
    "ui_hover",
    "ui_key",
    "ui_type",
    "ui_set_property",
    "ui_invoke",
    "dialogs",
    "list_actions",
    "call_action",
    "screenshot",
    "project_point",
}


class SmokeTests(LiveTestCase):
    def test_every_tool_answers(self) -> None:
        opened = self.open_sample("Cascy.casc")
        self.assertEqual(opened["name"], "Cascy.casc")
        self.assertGreater(opened["objects"], 0, "Cascy.casc should contain objects")

        available = {tool["name"] for tool in client.tools()}
        self.assertEqual(EXPECTED_TOOLS - available, set(), "the live server is missing pre-T02 tools")

        called: set[str] = set()

        def call(tool: str, **arguments):
            called.add(tool)
            return client.call(tool, **arguments)

        # Scene inspection -------------------------------------------------
        scene = call("get_scene_info")
        self.assertEqual(scene["scene"]["name"], "Cascy.casc")
        self.assertGreater(scene["objects"]["count"], 0)
        sample = scene["objects"]["sample"]
        self.assertTrue(sample, "get_scene_info returned no sample objects")
        joint = next((item["name"] for item in sample if item["type"] == "Joint"), sample[0]["name"])
        object_info = call("get_object_info", name=joint, max_properties=5)
        self.assertEqual(object_info["matches"], 1)
        self.assertEqual(object_info["objects"][0]["name"], joint)

        projection = call("project_point", names=[joint])
        self.assertIn(joint, projection["points"])
        self.assertGreater(projection["points"][joint]["depth"], 0)
        viewport = projection["viewport_rect"]

        python_result = call("run_python", code="1 + 1")
        self.assertTrue(python_result["ok"])
        self.assertEqual(python_result["value"], "2")

        log = call("get_log", lines=5)
        self.assertTrue(log["lines"])
        self.assertTrue(log["path"])

        actions = call("list_actions", query="Timeline")
        self.assertTrue(actions["actions"])
        self.assertTrue(any(action["actionId"].startswith("Timeline.") for action in actions["actions"]))

        # UI read-only -----------------------------------------------------
        tree = call("ui_tree", query="Filter by name")
        self.assertTrue(tree["windows"], "ui_tree found no windows")
        panels = [item for item in tree["elements"] if item.get("placeholderText") == "Filter by name"]
        self.assertTrue(panels, "the Outliner's 'Filter by name' panel was not found")
        filter_ref = panels[0]["ref"]

        typed = call("ui_type", query="Filter by name", text="pelvis", replace=True, release_focus=True)
        self.assertEqual(typed["typed"], "pelvis")
        call("ui_type", query="Filter by name", text="", replace=True, release_focus=True)

        prop = call("ui_set_property", ref=filter_ref, name="visible", value=True)
        self.assertTrue(prop["ok"], f"ui_set_property failed: {prop!r}")
        invoked = call("ui_invoke", ref=filter_ref, method="forceActiveFocus")
        self.assertTrue(invoked["ok"], f"ui_invoke failed: {invoked!r}")
        call("ui_key", keys="Escape")

        # Mouse input in the empty lower part of the 3D viewport -----------
        x = int(viewport[0] + viewport[2] * 0.2)
        y = int(viewport[1] + viewport[3] * 0.8)
        clicked = call("ui_click", x=x, y=y)
        self.assertIn("clicked", clicked)
        hovered = call("ui_hover", x=x, y=y)
        self.assertIn("hovered", hovered)
        dragged = call("ui_drag", x=x, y=y, dx=5, dy=0, steps=3, duration_ms=100)
        self.assertEqual(len(dragged["start"]), 2)
        if dragged.get("dialogs"):
            self.dismiss_dialogs(timeout=1.0)

        action = call("call_action", key="Timeline.To first frame")
        self.assertEqual(action["called"], "Timeline.To first frame")

        dialogs = call("dialogs")
        self.assertIn("dialogs", dialogs)

        # Capture ----------------------------------------------------------
        shot = call("screenshot", restore=True, max_width=320)
        self.assertEqual(len(shot["images"]), 1)
        self.assertEqual(shot["images"][0]["mimeType"], "image/jpeg")
        self.assertEqual(len(shot["meta"]["window_size"]), 2)

        # Entering Rig mode can change scene state, so it is last.
        rig = call("enter_rig_mode")
        self.assertNotIn("error", rig, f"enter_rig_mode failed: {rig!r}")
        self.assertTrue(rig.get("rig_mode"), f"enter_rig_mode did not enter Rig mode: {rig!r}")
        self.dismiss_dialogs(timeout=1.0)

        self.assertEqual(EXPECTED_TOOLS - called, set(), "some pre-T02 tools were not called")


if __name__ == "__main__":
    unittest.main()
