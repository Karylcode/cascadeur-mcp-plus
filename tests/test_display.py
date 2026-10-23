"""Unit tests for the display tools (no Cascadeur needed).

The module is loaded directly, like tests/test_api.py does, so importing it does not pull in the
Cascadeur-only package __init__ (server/PySide6/csc). The Cascadeur-facing helpers are replaced
with fakes; action ids, state derivations and the setters' action plans are covered here, and
tests/live/test_display.py checks them against the real Cascadeur UI.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_display():
    """Load tools/display.py without importing the Cascadeur-only package __init__."""
    package = "cascadeur_mcp_plus"
    if package not in sys.modules:
        module = types.ModuleType(package)
        module.__path__ = [os.path.join(ROOT, package)]
        sys.modules[package] = module
    tools_package = package + ".tools"
    if tools_package not in sys.modules:
        module = types.ModuleType(tools_package)
        module.__path__ = [os.path.join(ROOT, package, "tools")]
        sys.modules[tools_package] = module
    name = tools_package + ".display"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, package, "tools", "display.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


display = load_display()

BASE_STATE = {
    "view_mode": "view",
    "view_modes": ["view"],
    "viewport_layout": 1,
    "ghosts": "none",
    "ghost_base_mode": "none",
    "ghost_keyframes": False,
    "trajectory_mode": "off",
    "trajectory_active": False,
    "only_key_frames": False,
    "silhouette": False,
    "surface": False,
}


def state(**overrides):
    return {**BASE_STATE, **overrides}


class DisplayMappingTests(unittest.TestCase):
    def test_view_mode_actions_are_the_cascadeur_toolbar_ids(self):
        self.assertEqual(
            display.VIEW_ACTIONS,
            {
                "mesh": "VisualizerStateController.AllModes.Mesh mode",
                "joint": "VisualizerStateController.AllModes.Joint mode",
                "box_controller": "VisualizerStateController.AllModes.Box controller mode",
                "point_controller": "VisualizerStateController.AllModes.Point controller mode",
                "autoposing": "VisualizerStateController.AllModes.AutoPosing mode",
                "rigging_view": "VisualizerStateController.AllModes.Rigging view mode",
                "view": "VisualizerStateController.AllModes.View mode",
            },
        )

    def test_ghost_action_ids_and_states(self):
        self.assertEqual(
            display.GHOST_ACTIONS,
            {
                "none": "GhostTool.Disable ghost",
                "previous": "GhostTool.Previous frames ghosts",
                "next": "GhostTool.Next frames ghosts",
                "neighbors": "GhostTool.Neighbor frame ghosts",
                "keyframes": "GhostTool.Keyframe ghosts",
                "selected_frames": "GhostTool.Ghosts for selected frames",
            },
        )
        self.assertEqual(
            display.GHOST_STATE_NAMES,
            {0: "none", 1: "previous", 2: "next", 3: "neighbors", 4: "selected_frames"},
        )

    def test_trajectory_and_layout_action_ids(self):
        self.assertEqual(
            display.TRAJECTORY_ACTIONS,
            {
                "all": "TrajectoryTool.All trajectories",
                "edges": "TrajectoryTool.Edge trajectories",
                "edges_and_fixed": "TrajectoryTool.Object and edge trajectories",
                "parents": "TrajectoryTool.Parent trajectories",
            },
        )
        self.assertEqual(display.TRAJECTORY_MODE_NAMES, {0: "parents", 1: "all", 2: "edges", 3: "edges_and_fixed"})
        self.assertEqual(display.TRAJECTORY_SWITCH, "TrajectoryTool.Switch trajectory activity")
        self.assertEqual(display.TRAJECTORY_ONLY_KEYS, "TrajectoryTool.Using only key frames")
        self.assertEqual(
            display.VIEWPORT_ACTIONS,
            {1: "Viewport.One Viewport", 2: "Viewport.Two Viewports", 4: "Viewport.Four Viewports"},
        )
        self.assertEqual(display.SILHOUETTE_ACTION, "View.Silhouette mode")
        self.assertEqual(display.SURFACE_ACTION, "View.Surface")

    def test_viewport_mode_numbers_match_the_stub(self):
        self.assertEqual(
            display.VIEW_MODE_NAMES,
            {
                0: "view",
                1: "autoposing",
                2: "point_controller",
                3: "box_controller",
                4: "joint",
                5: "mesh",
                6: "rigging_view",
            },
        )
        self.assertEqual(display._view_mode_name(display.VIEW_MODE_VALUES["mesh"]), "mesh")
        self.assertEqual(display._view_mode_name(99), "unknown(99)")
        self.assertIsNone(display._view_mode_name(None))
        self.assertIsNone(display._view_mode_name("not a mode"))

    def test_ghosts_from_flags_ignores_the_keyframe_toggle(self):
        self.assertIsNone(display._ghosts_from_flags({}))
        self.assertEqual(display._ghosts_from_flags({"GhostTool.Disable ghost": True}), "none")
        self.assertEqual(display._ghosts_from_flags({"GhostTool.Previous frames ghosts": True}), "previous")
        self.assertEqual(display._ghosts_from_flags({"GhostTool.Next frames ghosts": True}), "next")
        self.assertEqual(display._ghosts_from_flags({"GhostTool.Neighbor frame ghosts": True}), "neighbors")
        self.assertEqual(display._ghosts_from_flags({"GhostTool.Ghosts for selected frames": True}), "selected_frames")
        self.assertIsNone(display._ghosts_from_flags({"GhostTool.Keyframe ghosts": True}))
        self.assertIsNone(display._ghosts_from_flags({"GhostTool.Disable ghost": False}))

    def test_trajectory_from_flags(self):
        flags = {
            "TrajectoryTool.Switch trajectory activity": True,
            "TrajectoryTool.Using only key frames": True,
            "TrajectoryTool.Edge trajectories": True,
        }
        self.assertEqual(display._trajectory_from_flags(flags), ("edges", True, True))
        flags["TrajectoryTool.Switch trajectory activity"] = False
        self.assertEqual(display._trajectory_from_flags(flags), ("off", False, True))
        self.assertEqual(
            display._trajectory_from_flags({"TrajectoryTool.Switch trajectory activity": True}),
            (None, True, None),
        )
        self.assertEqual(display._trajectory_from_flags({}), (None, None, None))

    def test_tools_are_registered_with_schemas(self):
        tools = {tool["name"]: tool for tool in display.TOOLS}
        self.assertEqual(
            list(tools),
            [
                "get_display_state",
                "set_view_mode",
                "set_ghosts",
                "set_trajectories",
                "set_viewport_layout",
                "set_silhouette",
                "set_surface",
            ],
        )
        self.assertEqual(tools["get_display_state"]["inputSchema"]["properties"], {})
        self.assertIn("fn", tools["get_display_state"])
        for name, tool in tools.items():
            self.assertTrue(tool["description"])
            self.assertEqual(("fn" in tool) + ("handler" in tool), 1, name)
            if name == "get_display_state":
                self.assertFalse(tool.get("settle"))
            else:
                self.assertIn("handler", tool, name)
                self.assertTrue(tool.get("settle"), name)
        self.assertEqual(
            set(tools["set_view_mode"]["inputSchema"]["properties"]["mode"]["enum"]), set(display.VIEW_ACTIONS)
        )
        self.assertEqual(
            set(tools["set_ghosts"]["inputSchema"]["properties"]["mode"]["enum"]), set(display.GHOST_ACTIONS)
        )
        self.assertEqual(
            tools["set_trajectories"]["inputSchema"]["properties"]["mode"]["enum"], ["off", *display.TRAJECTORY_ACTIONS]
        )
        self.assertEqual(tools["set_viewport_layout"]["inputSchema"]["properties"]["count"]["enum"], [1, 2, 4])


class DisplayToolTests(unittest.TestCase):
    def test_get_display_state_returns_the_read_state(self):
        with mock.patch.object(display, "_read_state", return_value=state(view_mode="mesh", ghosts="next")):
            result = display.get_display_state()
        self.assertTrue(result["ok"])
        self.assertEqual(result["view_mode"], "mesh")
        self.assertEqual(result["ghosts"], "next")

    def test_get_display_state_reports_errors(self):
        with mock.patch.object(display, "_read_state", side_effect=RuntimeError("no scene")):
            result = display.get_display_state()
        self.assertFalse(result["ok"])
        self.assertIn("no scene", result["error"])
        self.assertIn("hint", result)

    def test_settle_polls_until_the_predicate_matches(self):
        reads = [state(surface=True), state(surface=False)]
        with mock.patch.object(display, "_gui_read", side_effect=reads):
            result = display._settle(lambda value: value.get("surface") is False, timeout=1.0)
        self.assertFalse(result["surface"])

    def test_settle_returns_the_last_state_on_timeout(self):
        with mock.patch.object(display, "_gui_read", return_value=state(surface=True)):
            result = display._settle(lambda value: value.get("surface") is False, timeout=0.05)
        self.assertTrue(result["surface"])

    def test_set_ghosts_switches_the_mode(self):
        after = state(ghosts="previous", ghost_base_mode="previous")
        with (
            mock.patch.object(display, "_gui_read", return_value=state()),
            mock.patch.object(display, "_gui_action") as action,
            mock.patch.object(display, "_settle", return_value=after),
        ):
            result = display.set_ghosts("previous")
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["ghosts"], "previous")
        action.assert_called_once_with(display.GHOST_ACTIONS["previous"])

    def test_set_ghosts_is_idempotent(self):
        with (
            mock.patch.object(display, "_gui_read", return_value=state(ghosts="previous", ghost_base_mode="previous")),
            mock.patch.object(display, "_gui_action") as action,
            mock.patch.object(display, "_settle") as settle,
        ):
            result = display.set_ghosts("previous")
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        action.assert_not_called()
        settle.assert_not_called()

    def test_set_ghosts_keyframes_enables_ghosts_and_key_mode(self):
        after = state(ghosts="keyframes", ghost_base_mode="previous", ghost_keyframes=True)
        with (
            mock.patch.object(display, "_gui_read", return_value=state()),
            mock.patch.object(display, "_gui_action") as action,
            mock.patch.object(display, "_settle", return_value=after),
        ):
            result = display.set_ghosts("keyframes")
        self.assertTrue(result["ok"])
        self.assertEqual(result["ghosts"], "keyframes")
        self.assertTrue(result["ghost_keyframes"])
        self.assertEqual(
            [call.args[0] for call in action.call_args_list],
            [display.GHOST_ACTIONS["previous"], display.GHOST_ACTIONS["keyframes"]],
        )

    def test_set_ghosts_leaves_keyframe_mode_for_frame_based_modes(self):
        before = state(ghosts="keyframes", ghost_base_mode="previous", ghost_keyframes=True)
        after = state(ghosts="previous", ghost_base_mode="previous", ghost_keyframes=False)
        with (
            mock.patch.object(display, "_gui_read", return_value=before),
            mock.patch.object(display, "_gui_action") as action,
            mock.patch.object(display, "_settle", return_value=after),
        ):
            result = display.set_ghosts("previous")
        self.assertTrue(result["ok"])
        action.assert_called_once_with(display.GHOST_ACTIONS["keyframes"])

    def test_set_ghosts_none_turns_off_keyframe_mode_too(self):
        before = state(ghosts="keyframes", ghost_base_mode="previous", ghost_keyframes=True)
        after = state()
        with (
            mock.patch.object(display, "_gui_read", return_value=before),
            mock.patch.object(display, "_gui_action") as action,
            mock.patch.object(display, "_settle", return_value=after),
        ):
            result = display.set_ghosts("none")
        self.assertTrue(result["ok"])
        self.assertEqual(
            [call.args[0] for call in action.call_args_list],
            [display.GHOST_ACTIONS["keyframes"], display.GHOST_ACTIONS["none"]],
        )

    def test_set_ghosts_validates_and_reports_missing_state(self):
        self.assertFalse(display.set_ghosts("spooky")["ok"])
        with mock.patch.object(display, "_gui_read", return_value=state(ghost_base_mode=None, ghost_keyframes=None)):
            result = display.set_ghosts("previous")
        self.assertFalse(result["ok"])
        self.assertIn("Ghost", result["error"])

    def test_set_ghosts_reports_a_rejected_action(self):
        with (
            mock.patch.object(display, "_gui_read", return_value=state()),
            mock.patch.object(display, "_gui_action"),
            mock.patch.object(display, "_settle", return_value=state()),
        ):
            result = display.set_ghosts("previous")
        self.assertFalse(result["ok"])
        self.assertEqual(result["ghosts"], "none")

    def test_set_view_mode_uses_the_toolbar_action(self):
        with (
            mock.patch.object(display, "_gui_read", return_value=state()),
            mock.patch.object(display, "_gui_action") as action,
            mock.patch.object(display, "_settle", return_value=state(view_mode="mesh")),
            mock.patch.object(display, "_gui_direct_view_mode") as direct,
        ):
            result = display.set_view_mode("mesh")
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        action.assert_called_once_with(display.VIEW_ACTIONS["mesh"])
        direct.assert_not_called()

    def test_set_view_mode_falls_back_to_the_viewport_api(self):
        with (
            mock.patch.object(display, "_gui_read", return_value=state()),
            mock.patch.object(display, "_gui_action"),
            mock.patch.object(display, "_settle", side_effect=[state(), state(view_mode="mesh")]),
            mock.patch.object(display, "_gui_direct_view_mode") as direct,
        ):
            result = display.set_view_mode("mesh")
        self.assertTrue(result["ok"])
        direct.assert_called_once_with("mesh")

    def test_set_view_mode_is_idempotent(self):
        with (
            mock.patch.object(display, "_gui_read", return_value=state(view_mode="mesh")),
            mock.patch.object(display, "_gui_action") as action,
        ):
            result = display.set_view_mode("mesh")
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        action.assert_not_called()

    def test_set_view_mode_rejects_unknown_modes(self):
        result = display.set_view_mode("wireframe")
        self.assertFalse(result["ok"])
        self.assertIn("mesh", result["hint"])

    def test_set_trajectories_turns_the_tool_on_and_picks_the_mode(self):
        after = state(trajectory_mode="all", trajectory_active=True)
        with (
            mock.patch.object(display, "_gui_read", return_value=state()),
            mock.patch.object(display, "_gui_action") as action,
            mock.patch.object(display, "_settle", return_value=after),
        ):
            result = display.set_trajectories("all")
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["trajectory_mode"], "all")
        self.assertEqual(
            [call.args[0] for call in action.call_args_list],
            [display.TRAJECTORY_SWITCH, display.TRAJECTORY_ACTIONS["all"]],
        )

    def test_set_trajectories_turns_the_tool_off(self):
        before = state(trajectory_mode="edges", trajectory_active=True)
        with (
            mock.patch.object(display, "_gui_read", return_value=before),
            mock.patch.object(display, "_gui_action") as action,
            mock.patch.object(display, "_settle", return_value=state()),
        ):
            result = display.set_trajectories("off")
        self.assertTrue(result["ok"])
        self.assertEqual(result["trajectory_mode"], "off")
        action.assert_called_once_with(display.TRAJECTORY_SWITCH)

    def test_set_trajectories_only_key_frames_toggle(self):
        after = state(trajectory_mode="all", trajectory_active=True, only_key_frames=True)
        with (
            mock.patch.object(display, "_gui_read", return_value=state(trajectory_mode="all", trajectory_active=True)),
            mock.patch.object(display, "_gui_action") as action,
            mock.patch.object(display, "_settle", return_value=after),
        ):
            result = display.set_trajectories("all", only_key_frames=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["only_key_frames"])
        action.assert_called_once_with(display.TRAJECTORY_ONLY_KEYS)

    def test_set_trajectories_is_idempotent(self):
        before = state(trajectory_mode="all", trajectory_active=True, only_key_frames=True)
        with (
            mock.patch.object(display, "_gui_read", return_value=before),
            mock.patch.object(display, "_gui_action") as action,
            mock.patch.object(display, "_settle") as settle,
        ):
            result = display.set_trajectories("all", only_key_frames=True)
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        action.assert_not_called()
        settle.assert_not_called()

    def test_set_trajectories_validates_and_reports_missing_state(self):
        self.assertFalse(display.set_trajectories("sideways")["ok"])
        self.assertFalse(display.set_trajectories("all", only_key_frames="yes")["ok"])
        with mock.patch.object(display, "_gui_read", return_value=state(trajectory_active=None)):
            self.assertFalse(display.set_trajectories("all")["ok"])

    def test_set_trajectories_reports_a_rejected_mode(self):
        with (
            mock.patch.object(display, "_gui_read", return_value=state()),
            mock.patch.object(display, "_gui_action"),
            mock.patch.object(display, "_settle", return_value=state(trajectory_mode="edges", trajectory_active=True)),
        ):
            result = display.set_trajectories("all")
        self.assertFalse(result["ok"])
        self.assertEqual(result["trajectory_mode"], "edges")

    def test_set_viewport_layout(self):
        with (
            mock.patch.object(display, "_gui_read", return_value=state()),
            mock.patch.object(display, "_gui_action") as action,
            mock.patch.object(display, "_settle", return_value=state(viewport_layout=4)),
        ):
            result = display.set_viewport_layout(4)
        self.assertTrue(result["ok"])
        self.assertEqual(result["viewport_layout"], 4)
        action.assert_called_once_with(display.VIEWPORT_ACTIONS[4])

        with (
            mock.patch.object(display, "_gui_read", return_value=state(viewport_layout=2)),
            mock.patch.object(display, "_gui_action") as action,
        ):
            result = display.set_viewport_layout(2)
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        action.assert_not_called()

    def test_set_viewport_layout_validates_and_reports_failures(self):
        self.assertFalse(display.set_viewport_layout(3)["ok"])
        self.assertFalse(display.set_viewport_layout(True)["ok"])
        self.assertFalse(display.set_viewport_layout("two")["ok"])
        with (
            mock.patch.object(display, "_gui_read", return_value=state()),
            mock.patch.object(display, "_gui_action"),
            mock.patch.object(display, "_settle", return_value=state()),
        ):
            result = display.set_viewport_layout(4)
        self.assertFalse(result["ok"])
        self.assertEqual(result["viewport_layout"], 1)

    def test_silhouette_uses_the_action_and_is_idempotent(self):
        with (
            mock.patch.object(display, "_gui_read", return_value=state()),
            mock.patch.object(display, "_gui_action") as action,
            mock.patch.object(display, "_settle", return_value=state(silhouette=True)),
        ):
            result = display.set_silhouette(True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["silhouette"])
        action.assert_called_once_with(display.SILHOUETTE_ACTION)

        with (
            mock.patch.object(display, "_gui_read", return_value=state(silhouette=True)),
            mock.patch.object(display, "_gui_action") as action,
        ):
            result = display.set_silhouette(True)
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        action.assert_not_called()

    def test_surface_uses_the_toolbar_property_and_is_idempotent(self):
        with (
            mock.patch.object(display, "_gui_read", return_value=state(surface=True)),
            mock.patch.object(display, "_gui_set_surface") as setter,
            mock.patch.object(display, "_settle", return_value=state(surface=False)),
        ):
            result = display.set_surface(False)
        self.assertTrue(result["ok"])
        self.assertFalse(result["surface"])
        setter.assert_called_once_with(False)

        with (
            mock.patch.object(display, "_gui_read", return_value=state(surface=False)),
            mock.patch.object(display, "_gui_set_surface") as setter,
        ):
            result = display.set_surface(False)
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        setter.assert_not_called()

    def test_silhouette_and_surface_validate_and_reject(self):
        self.assertFalse(display.set_silhouette(1)["ok"])
        self.assertFalse(display.set_surface("on")["ok"])
        with mock.patch.object(display, "_gui_read", return_value=state(silhouette=None)):
            result = display.set_silhouette(True)
        self.assertFalse(result["ok"])
        self.assertIn("silhouette", result["error"].lower())
        with (
            mock.patch.object(display, "_gui_read", return_value=state()),
            mock.patch.object(display, "_gui_action"),
            mock.patch.object(display, "_settle", return_value=state()),
        ):
            result = display.set_silhouette(True)
        self.assertFalse(result["ok"])
        with (
            mock.patch.object(display, "_gui_read", return_value=state(surface=True)),
            mock.patch.object(display, "_gui_set_surface"),
            mock.patch.object(display, "_settle", return_value=state(surface=True)),
        ):
            result = display.set_surface(False)
        self.assertFalse(result["ok"])
        self.assertTrue(result["surface"])


if __name__ == "__main__":
    unittest.main()
