"""Unit tests for the cycle tools (no Cascadeur needed).

cascadeur_mcp_plus/tools/cycles.py is loaded directly, like tests/test_timeline.py does, with the
Cascadeur-only common module and the csc module replaced by fakes. The pure helpers (offset
parsing, the cycle period, position matching) are tested for real; the tools are tested against a
fake scene and a fake ActionManager to check argument errors, the animation-length guard, the
confirmed timeline selection and the exact action names. No test brings up the cycle API: the
module does not use it (reading cycles crashed Cascadeur 2026.2.3 three times).

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
PACKAGE = "cascadeur_mcp_plus"
_MISSING = object()


def _package(name: str, path: str) -> types.ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    module = types.ModuleType(name)
    module.__path__ = [path]
    sys.modules[name] = module
    return module


def _load(name: str, filename: str):
    full = PACKAGE + "." + name
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, os.path.join(ROOT, PACKAGE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Fake Cascadeur surface
# ---------------------------------------------------------------------------


class FakeBoundary:
    first_frame = 0
    last_frame = 100
    first_visible_frame = 0
    last_visible_frame = 100


class FakeViewScene:
    def animation_boundary(self):
        return FakeBoundary()


class FakeViewer:
    def __init__(self, layer_ids, last_key):
        self.layer_ids = list(layer_ids)
        self.last_key = last_key

    def all_layer_ids(self):
        return list(self.layer_ids)

    def last_key_pos(self):
        return self.last_key


class FakeDataViewer:
    def __init__(self, size):
        self.size = size

    def get_animation_size(self):
        return self.size


class FakeModelViewer:
    def __init__(self, size):
        self.size = size

    def data_viewer(self):
        return FakeDataViewer(self.size)


class FakeScene:
    def __init__(self, layer_ids=("layer0", "layer1"), last_key=100, animation_size=100):
        self.viewer = FakeViewer(layer_ids, last_key)
        self.animation_size = animation_size

    def layers_viewer(self):
        return self.viewer

    def model_viewer(self):
        return FakeModelViewer(self.animation_size)


class FakeActionManager:
    def __init__(self):
        self.calls = []
        self.error = None

    def call_action(self, key):
        self.calls.append(key)
        if self.error is not None:
            raise self.error


CURRENT_SCENE = FakeScene()
CURRENT_VIEW = FakeViewScene()
ACTION_MANAGER = FakeActionManager()
SELECTED: list = []
SELECT_RESULT = None  # set per test


def _fail(error, hint=None, **data):
    result = {"ok": False, "error": str(error)}
    if hint:
        result["hint"] = str(hint)
    result.update(data)
    return result


fake_common = types.ModuleType(PACKAGE + ".common")
fake_common.ok = lambda **data: {"ok": True, **data}
fake_common.fail = _fail
fake_common.jsonable = lambda value: value
fake_common.scenes = lambda: (None, CURRENT_VIEW, CURRENT_SCENE)

fake_csc = types.ModuleType("csc")
fake_csc.app = types.SimpleNamespace(
    get_application=lambda: types.SimpleNamespace(get_action_manager=lambda: ACTION_MANAGER)
)


def _fake_select(first, last, layer_ids=None):
    SELECTED.append({"first": first, "last": last, "layer_ids": list(layer_ids or [])})
    if SELECT_RESULT is not None:
        return SELECT_RESULT
    return {"first": first, "last": last, "layers": len(layer_ids or [])}


_package(PACKAGE, os.path.join(ROOT, PACKAGE))
timeline = _load("timeline", "timeline.py")
errors = sys.modules[PACKAGE + ".errors"]
_saved_common = sys.modules.get(PACKAGE + ".common", _MISSING)
sys.modules[PACKAGE + ".common"] = fake_common
_package(PACKAGE + ".tools", os.path.join(ROOT, PACKAGE, "tools"))
_load("tools._schema", os.path.join("tools", "_schema.py"))
cycles = _load("tools.cycles", os.path.join("tools", "cycles.py"))
if _saved_common is _MISSING:
    sys.modules.pop(PACKAGE + ".common", None)
else:
    sys.modules[PACKAGE + ".common"] = _saved_common

cycles._csc = lambda: fake_csc


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class OffsetTests(unittest.TestCase):
    def test_defaults_to_none(self):
        for value in (None, "", "  ", "none", "NONE"):
            self.assertEqual(cycles.normalize_offset(value), "none", value)

    def test_accepts_the_documented_values(self):
        self.assertEqual(cycles.normalize_offset("position"), "position")
        self.assertEqual(cycles.normalize_offset("position_rotation"), "position_rotation")
        self.assertEqual(cycles.normalize_offset("Position Rotation"), "position_rotation")
        self.assertEqual(cycles.normalize_offset("position-rotation"), "position_rotation")
        self.assertEqual(cycles.normalize_offset("position+rotation"), "position_rotation")

    def test_rejects_unknown_values(self):
        for value in ("spin", "rotate", "position_rota"):
            with self.assertRaises(errors.ToolError, msg=repr(value)):
                cycles.normalize_offset(value)


class GeometryTests(unittest.TestCase):
    def test_cycle_period_includes_both_ends(self):
        self.assertEqual(cycles.cycle_period(0, 30), 31)
        self.assertEqual(cycles.cycle_period(10, 10), 1)
        self.assertEqual(cycles.cycle_period(0, 39), 40)

    def test_pose_matches_with_a_tolerance(self):
        self.assertTrue(cycles.pose_matches([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]))
        self.assertTrue(cycles.pose_matches([1.0, 2.0, 3.0], [1.0005, 2.0, 3.0]))
        self.assertFalse(cycles.pose_matches([1.0, 2.0, 3.0], [1.01, 2.0, 3.0]))
        self.assertFalse(cycles.pose_matches([1.0, 2.0], [1.0, 2.0, 3.0]))

    def test_pose_matches_rejects_unusable_values(self):
        self.assertFalse(cycles.pose_matches(None, [1.0]))
        self.assertFalse(cycles.pose_matches(["x"], [1.0]))
        self.assertFalse(cycles.pose_matches([], []))


# ---------------------------------------------------------------------------
# Tool functions against the fake scene
# ---------------------------------------------------------------------------


class WorldTestCase(unittest.TestCase):
    def setUp(self):
        global CURRENT_SCENE, SELECTED, SELECT_RESULT
        CURRENT_SCENE = FakeScene()
        SELECTED = []
        SELECT_RESULT = None
        ACTION_MANAGER.calls = []
        ACTION_MANAGER.error = None
        self.select_patch = mock.patch.object(timeline, "select_interval", side_effect=_fake_select)
        self.select_patch.start()

    def tearDown(self):
        self.select_patch.stop()


class CreateCycleTests(WorldTestCase):
    def test_requires_a_start_and_end(self):
        result = cycles.create_cycle()
        self.assertFalse(result["ok"])
        self.assertIn("start", result["error"])
        self.assertTrue(result["hint"])

    def test_rejects_a_reversed_range(self):
        result = cycles.create_cycle(start=30, end=10)
        self.assertFalse(result["ok"])
        self.assertIn("greater", result["error"])

    def test_rejects_a_single_frame(self):
        result = cycles.create_cycle(start=10, end=10)
        self.assertFalse(result["ok"])
        self.assertIn("two frames", result["error"])

    def test_rejects_a_bad_offset(self):
        result = cycles.create_cycle(start=0, end=30, offset="spin")
        self.assertFalse(result["ok"])
        self.assertIn("offset", result["error"])

    def test_rejects_frames_outside_the_animation_range(self):
        result = cycles.create_cycle(start=0, end=120)
        self.assertFalse(result["ok"])
        self.assertIn("outside the animation range", result["error"])
        self.assertIn("set_animation_range", result["hint"])
        self.assertEqual(ACTION_MANAGER.calls, [])

    def test_rejects_a_one_frame_animation_before_any_action(self):
        global CURRENT_SCENE
        CURRENT_SCENE = FakeScene(last_key=0, animation_size=1)
        result = cycles.create_cycle(start=0, end=30, offset="position")
        self.assertFalse(result["ok"])
        self.assertIn("only 1 frame", result["error"])
        self.assertIn("one-frame animation", result["hint"])
        self.assertEqual(result["animation_size"], 1)
        self.assertEqual(ACTION_MANAGER.calls, [])
        self.assertEqual(SELECTED, [])

    def test_rejects_no_keyed_frames(self):
        global CURRENT_SCENE
        CURRENT_SCENE = FakeScene(last_key=-1, animation_size=2)
        result = cycles.create_cycle(start=0, end=1)
        self.assertFalse(result["ok"])
        self.assertIn("only 1 frame", result["error"])
        self.assertEqual(ACTION_MANAGER.calls, [])

    def test_rejects_frames_past_the_animated_content(self):
        global CURRENT_SCENE
        CURRENT_SCENE = FakeScene(last_key=39, animation_size=40)
        result = cycles.create_cycle(start=0, end=50)
        self.assertFalse(result["ok"])
        self.assertIn("past the animated content", result["error"])
        self.assertIn("39", result["hint"])
        self.assertEqual(ACTION_MANAGER.calls, [])

    def test_every_offset_mode_runs_its_timeline_action(self):
        global SELECTED
        for offset, action in cycles.OFFSET_ACTIONS.items():
            ACTION_MANAGER.calls = []
            SELECTED = []
            result = cycles.create_cycle(start=0, end=30, offset=offset)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["action"], action)
            self.assertEqual(result["offset"], offset)
            self.assertEqual(result["start"], 0)
            self.assertEqual(result["end"], 30)
            self.assertEqual(ACTION_MANAGER.calls, [action])
            self.assertEqual(SELECTED, [{"first": 0, "last": 30, "layer_ids": ["layer0", "layer1"]}])
            self.assertIsNone(result["undo"])
            self.assertIn("undo step", result["hint"])

    def test_selection_mismatch_is_not_acted_on(self):
        global SELECT_RESULT
        SELECT_RESULT = {"first": 0, "last": 29, "layers": 2}
        result = cycles.create_cycle(start=0, end=30)
        self.assertFalse(result["ok"])
        self.assertIn("selected frames", result["error"])
        self.assertIn("set_animation_range", result["hint"])
        self.assertEqual(ACTION_MANAGER.calls, [])

    def test_action_failure_is_reported(self):
        ACTION_MANAGER.error = RuntimeError("action disabled")
        result = cycles.create_cycle(start=0, end=30)
        self.assertFalse(result["ok"])
        self.assertIn("Timeline.Create cycle", result["error"])
        self.assertIn("action disabled", result["error"])
        self.assertIn("action", result)

    def test_no_layers_fails_with_a_hint(self):
        global CURRENT_SCENE
        CURRENT_SCENE = FakeScene(layer_ids=())
        result = cycles.create_cycle(start=0, end=30)
        self.assertFalse(result["ok"])
        self.assertIn("no timeline layers", result["error"])


class RemoveCyclesTests(WorldTestCase):
    def test_defaults_to_the_whole_animation_range(self):
        result = cycles.remove_cycles()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["start"], 0)
        self.assertEqual(result["end"], 100)
        self.assertEqual(result["action"], cycles.REMOVE_ACTION)
        self.assertEqual(ACTION_MANAGER.calls, [cycles.REMOVE_ACTION])
        self.assertEqual(SELECTED[0]["first"], 0)
        self.assertEqual(SELECTED[0]["last"], 100)
        self.assertIsNone(result["undo"])

    def test_start_only_removes_to_the_end(self):
        result = cycles.remove_cycles(start=20)
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["start"], result["end"]), (20, 100))

    def test_end_only_removes_from_the_start(self):
        result = cycles.remove_cycles(end=20)
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["start"], result["end"]), (0, 20))

    def test_explicit_range(self):
        result = cycles.remove_cycles(start=10, end=20)
        self.assertTrue(result["ok"], result)
        self.assertEqual(SELECTED, [{"first": 10, "last": 20, "layer_ids": ["layer0", "layer1"]}])

    def test_rejects_a_reversed_range(self):
        result = cycles.remove_cycles(start=20, end=10)
        self.assertFalse(result["ok"])
        self.assertIn("greater", result["error"])
        self.assertEqual(ACTION_MANAGER.calls, [])

    def test_selection_mismatch_is_not_acted_on(self):
        global SELECT_RESULT
        SELECT_RESULT = {"first": 0, "last": 30, "layers": 2}
        result = cycles.remove_cycles()
        self.assertFalse(result["ok"])
        self.assertIn("selected frames", result["error"])
        self.assertEqual(ACTION_MANAGER.calls, [])

    def test_action_failure_is_reported(self):
        ACTION_MANAGER.error = RuntimeError("no cycles")
        result = cycles.remove_cycles()
        self.assertFalse(result["ok"])
        self.assertIn("Timeline.Remove cycles", result["error"])

    def test_no_layers_fails_with_a_hint(self):
        global CURRENT_SCENE
        CURRENT_SCENE = FakeScene(layer_ids=())
        result = cycles.remove_cycles()
        self.assertFalse(result["ok"])
        self.assertIn("no timeline layers", result["error"])


class ToolTableTests(unittest.TestCase):
    def test_tools(self):
        names = [tool["name"] for tool in cycles.TOOLS]
        self.assertEqual(names, ["create_cycle", "remove_cycles"])
        self.assertEqual(len(names), len(set(names)))
        for tool in cycles.TOOLS:
            self.assertTrue(tool["description"].strip().endswith("."), tool["name"])
            self.assertEqual(tool["inputSchema"]["type"], "object", tool["name"])
            self.assertTrue(callable(tool.get("fn")), tool["name"])
            self.assertNotIn("handler", tool)

    def test_create_cycle_schema_lists_the_offsets(self):
        tool = next(item for item in cycles.TOOLS if item["name"] == "create_cycle")
        self.assertEqual(tool["inputSchema"]["required"], ["start", "end"])
        self.assertEqual(tool["inputSchema"]["properties"]["offset"]["enum"], list(cycles.OFFSETS))

    def test_no_cycle_reading_api_is_used(self):
        # The module must never call the cycle introspection API (it crashed Cascadeur), even
        # though its docstring explains why.
        with open(os.path.join(ROOT, PACKAGE, "tools", "cycles.py"), encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in (".layers.CyclesViewer(", ".layers.CyclesEditor(", "get_cycles_in_frames"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
