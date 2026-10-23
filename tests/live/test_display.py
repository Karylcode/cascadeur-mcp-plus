"""Live tests for the display tools (T34); needs Cascadeur and the live-test lock.

Run through tests/live/run.py after taking the lock and restarting the worktree:

    python docs/tickets/bin/live.py lock T34
    python docs/tickets/bin/live.py restart C:/Users/User/Tools/cascadeur-mcp-plus-wt/T34
    python tests/live/run.py test_display.py

Every test opens its own Cascy.casc tab, checks each option of each display tool against
get_display_state and against the toolbar button's own active/checked flag, and saves a PNG
screenshot for every view mode and viewport layout. LiveTestCase closes the tab without saving
and restores the user's scene; tearDown additionally restores the display state captured before
the test (layout, view mode, ghosts, trajectories, silhouette and surface are toolbar/global state).
"""

from __future__ import annotations

import base64
import os
import unittest

try:  # imported as live.test_display by unittest discovery
    from . import client
    from .fixture import LiveTestCase
except ImportError:  # executed by run.py
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]


VIEW_MODES = ["mesh", "joint", "box_controller", "point_controller", "autoposing", "rigging_view", "view"]
VIEW_ACTIONS = {
    "mesh": "VisualizerStateController.AllModes.Mesh mode",
    "joint": "VisualizerStateController.AllModes.Joint mode",
    "box_controller": "VisualizerStateController.AllModes.Box controller mode",
    "point_controller": "VisualizerStateController.AllModes.Point controller mode",
    "autoposing": "VisualizerStateController.AllModes.AutoPosing mode",
    "rigging_view": "VisualizerStateController.AllModes.Rigging view mode",
    "view": "VisualizerStateController.AllModes.View mode",
}
GHOST_ACTIONS = {
    "none": "GhostTool.Disable ghost",
    "previous": "GhostTool.Previous frames ghosts",
    "next": "GhostTool.Next frames ghosts",
    "neighbors": "GhostTool.Neighbor frame ghosts",
    "keyframes": "GhostTool.Keyframe ghosts",
    "selected_frames": "GhostTool.Ghosts for selected frames",
}
TRAJECTORY_ACTIONS = {
    "all": "TrajectoryTool.All trajectories",
    "edges": "TrajectoryTool.Edge trajectories",
    "edges_and_fixed": "TrajectoryTool.Object and edge trajectories",
    "parents": "TrajectoryTool.Parent trajectories",
}
VIEWPORT_ACTIONS = {1: "Viewport.One Viewport", 2: "Viewport.Two Viewports", 4: "Viewport.Four Viewports"}


class DisplayLiveTests(LiveTestCase):
    def setUp(self) -> None:
        self.display_baseline = client.call("get_display_state")
        self.assertTrue(self.display_baseline.get("ok"), self.display_baseline)
        self.info = self.open_sample("Cascy.casc")
        self.assertGreater(self.info["objects"], 0)

    def tearDown(self) -> None:
        try:
            super().tearDown()
        finally:
            self._restore_baseline_display()

    # -- helpers ---------------------------------------------------------

    def read_state(self) -> dict:
        result = client.call("get_display_state")
        self.assertTrue(result.get("ok"), result)
        return result

    def ui_active(self, action_id: str) -> bool:
        tree = client.call("ui_tree", query=action_id, include_hidden=True, limit=250)
        matches = [element for element in tree.get("elements", []) if element.get("actionId") == action_id]
        self.assertTrue(matches, f"the UI has no element with actionId {action_id!r}")
        return any(element.get("active") is True for element in matches)

    def assert_ui_active(self, action_id: str, expected: bool = True) -> None:
        self.assertEqual(self.ui_active(action_id), expected, f"{action_id} active state")

    def save_screenshot(self, name: str) -> str:
        result = client.call("screenshot", format="png", max_width=800, http_timeout=60.0)
        self.assertTrue(result and result.get("images"), f"screenshot returned no image: {result!r}")
        data = base64.b64decode(result["images"][0]["data"])
        self.assertGreater(len(data), 1000, "screenshot is suspiciously small")
        path = os.path.join(self.out_dir(), name + ".png")
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def _restore_baseline_display(self) -> None:
        base = self.display_baseline
        calls = []
        if base.get("viewport_layout") is not None:
            calls.append(("set_viewport_layout", {"count": base["viewport_layout"]}))
        if base.get("view_mode") is not None:
            calls.append(("set_view_mode", {"mode": base["view_mode"]}))
        if base.get("ghosts") is not None:
            calls.append(("set_ghosts", {"mode": base["ghosts"]}))
        if base.get("trajectory_mode") is not None:
            calls.append(
                ("set_trajectories", {"mode": base["trajectory_mode"], "only_key_frames": base.get("only_key_frames")})
            )
        if base.get("silhouette") is not None:
            calls.append(("set_silhouette", {"enabled": base["silhouette"]}))
        if base.get("surface") is not None:
            calls.append(("set_surface", {"enabled": base["surface"]}))
        failures = []
        for tool, arguments in calls:
            result = client.call(tool, **arguments)
            if not result.get("ok"):
                failures.append(f"{tool}: {result.get('error')}")
        if failures:
            raise AssertionError("could not restore the user's display state: " + "; ".join(failures))

    # -- tests -----------------------------------------------------------

    def test_every_view_mode_switches_state_ui_and_screenshot(self):
        layout = client.call("set_viewport_layout", count=1)
        self.assertTrue(layout.get("ok"), layout)
        for mode in VIEW_MODES:
            with self.subTest(mode=mode):
                result = client.call("set_view_mode", mode=mode)
                self.assertTrue(result.get("ok"), result)
                self.assertEqual(result.get("view_mode"), mode)
                state = self.read_state()
                self.assertEqual(state["view_mode"], mode)
                self.assertEqual(state["view_modes"], [mode])
                self.assert_ui_active(VIEW_ACTIONS[mode])
                self.assertTrue(os.path.isfile(self.save_screenshot(f"view_{mode}")))
                again = client.call("set_view_mode", mode=mode)
                self.assertTrue(again.get("ok"), again)
                self.assertFalse(again.get("changed"), again)
                self.assertEqual(self.read_state()["view_mode"], mode)
        client.call("set_view_mode", mode="view")

    def test_every_ghost_mode_switches_state_and_repeats_identically(self):
        for mode in GHOST_ACTIONS:
            with self.subTest(mode=mode):
                result = client.call("set_ghosts", mode=mode)
                self.assertTrue(result.get("ok"), result)
                self.assertEqual(result.get("ghosts"), mode)
                state = self.read_state()
                self.assertEqual(state["ghosts"], mode)
                if mode == "keyframes":
                    self.assertTrue(state["ghost_keyframes"])
                    self.assert_ui_active(GHOST_ACTIONS["keyframes"])
                    self.assert_ui_active(GHOST_ACTIONS[state["ghost_base_mode"]])
                else:
                    self.assertFalse(state["ghost_keyframes"])
                    self.assert_ui_active(GHOST_ACTIONS[mode])
                    self.assert_ui_active(GHOST_ACTIONS["keyframes"], False)
                again = client.call("set_ghosts", mode=mode)
                self.assertTrue(again.get("ok"), again)
                self.assertFalse(again.get("changed"), again)
                self.assertEqual(again.get("ghosts"), mode)
                self.assertEqual(self.read_state()["ghosts"], mode)
        client.call("set_ghosts", mode="keyframes")
        self.save_screenshot("ghosts_keyframes")
        client.call("set_ghosts", mode="none")

    def test_trajectory_modes_and_only_key_frames(self):
        for mode in TRAJECTORY_ACTIONS:
            with self.subTest(mode=mode):
                result = client.call("set_trajectories", mode=mode)
                self.assertTrue(result.get("ok"), result)
                self.assertEqual(result.get("trajectory_mode"), mode)
                state = self.read_state()
                self.assertEqual(state["trajectory_mode"], mode)
                self.assertTrue(state["trajectory_active"])
                self.assert_ui_active(TRAJECTORY_ACTIONS[mode])
                self.assert_ui_active("TrajectoryTool.Switch trajectory activity")
                again = client.call("set_trajectories", mode=mode)
                self.assertTrue(again.get("ok"), again)
                self.assertFalse(again.get("changed"), again)
                self.assertEqual(self.read_state()["trajectory_mode"], mode)

        off = client.call("set_trajectories", mode="off")
        self.assertTrue(off.get("ok"), off)
        state = self.read_state()
        self.assertEqual(state["trajectory_mode"], "off")
        self.assertFalse(state["trajectory_active"])
        self.assert_ui_active("TrajectoryTool.Switch trajectory activity", False)

        keys_on = client.call("set_trajectories", mode="all", only_key_frames=True)
        self.assertTrue(keys_on.get("ok"), keys_on)
        self.assertTrue(keys_on.get("only_key_frames"))
        state = self.read_state()
        self.assertEqual(state["trajectory_mode"], "all")
        self.assertTrue(state["only_key_frames"])
        self.assert_ui_active("TrajectoryTool.Using only key frames")

        again = client.call("set_trajectories", mode="all", only_key_frames=True)
        self.assertTrue(again.get("ok"), again)
        self.assertFalse(again.get("changed"), again)

        keys_off = client.call("set_trajectories", mode="all", only_key_frames=False)
        self.assertTrue(keys_off.get("ok"), keys_off)
        self.assertFalse(keys_off.get("only_key_frames"))
        self.assertFalse(self.read_state()["only_key_frames"])

        client.call("set_trajectories", mode="off")
        client.call("set_trajectories", mode="all", only_key_frames=False)

    def test_viewport_layouts_switch_state_ui_and_screenshots(self):
        for count in (2, 4, 1):
            with self.subTest(count=count):
                result = client.call("set_viewport_layout", count=count)
                self.assertTrue(result.get("ok"), result)
                self.assertEqual(result.get("viewport_layout"), count)
                state = self.read_state()
                self.assertEqual(state["viewport_layout"], count)
                self.assertEqual(len(state["view_modes"]), count)
                self.assert_ui_active(VIEWPORT_ACTIONS[count])
                self.assertTrue(os.path.isfile(self.save_screenshot(f"layout_{count}")))
                again = client.call("set_viewport_layout", count=count)
                self.assertTrue(again.get("ok"), again)
                self.assertFalse(again.get("changed"), again)
                self.assertEqual(self.read_state()["viewport_layout"], count)

    def test_silhouette_and_surface_switch_state_and_repeat_identically(self):
        for tool, key, action in (
            ("set_silhouette", "silhouette", "View.Silhouette mode"),
            ("set_surface", "surface", "View.Surface"),
        ):
            with self.subTest(tool=tool):
                for enabled in (True, False, True):
                    result = client.call(tool, enabled=enabled)
                    self.assertTrue(result.get("ok"), result)
                    self.assertEqual(result.get(key), enabled)
                    self.assertEqual(self.read_state()[key], enabled)
                    self.assert_ui_active(action, enabled)
                    again = client.call(tool, enabled=enabled)
                    self.assertTrue(again.get("ok"), again)
                    self.assertFalse(again.get("changed"), again)
                    self.assertEqual(self.read_state()[key], enabled)
        client.call("set_silhouette", enabled=True)
        client.call("set_surface", enabled=True)
        self.save_screenshot("silhouette_and_surface_on")
        client.call("set_silhouette", enabled=False)

    def test_state_reports_all_documented_keys(self):
        state = self.read_state()
        for key in (
            "view_mode",
            "view_modes",
            "viewport_layout",
            "ghosts",
            "ghost_base_mode",
            "ghost_keyframes",
            "trajectory_mode",
            "trajectory_active",
            "only_key_frames",
            "silhouette",
            "surface",
        ):
            self.assertIn(key, state)
        self.assertIn(state["view_mode"], VIEW_MODES)
        self.assertIn(state["viewport_layout"], (1, 2, 4))
        self.assertIn(state["ghosts"], GHOST_ACTIONS)
        self.assertIn(state["ghost_base_mode"], ("none", "previous", "next", "neighbors", "selected_frames"))
        self.assertIsInstance(state["ghost_keyframes"], bool)
        self.assertIn(state["trajectory_mode"], ("off", *TRAJECTORY_ACTIONS))
        self.assertIsInstance(state["only_key_frames"], bool)
        self.assertIsInstance(state["silhouette"], bool)
        self.assertIsInstance(state["surface"], bool)


if __name__ == "__main__":
    unittest.main()
