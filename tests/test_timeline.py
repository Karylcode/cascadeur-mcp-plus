"""Unit tests for the timeline helpers and tools (no Cascadeur needed).

cascadeur_mcp_plus/timeline.py and cascadeur_mcp_plus/tools/timeline.py are loaded directly,
like tests/test_display.py does, so importing them does not pull in the Cascadeur-only package
__init__ (server/PySide6/csc). The pure helpers (frame parsing, interpolation names, the key
summary) are tested for real; the tool module is loaded with fake common/rig modules to check
argument errors, the "whole character" resolution and the tool table. tests/live/test_timeline.py
checks the same helpers against the running Cascadeur.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "cascadeur_mcp_plus"


def _package(name: str, path: str) -> types.ModuleType:
    full = name
    if full in sys.modules:
        return sys.modules[full]
    module = types.ModuleType(full)
    module.__path__ = [path]
    sys.modules[full] = module
    return module


def _load(name: str, filename: str, attributes=None):
    full = PACKAGE + "." + name
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, os.path.join(ROOT, PACKAGE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    if attributes:
        for key, value in attributes.items():
            setattr(module, key, value)
    return module


_package(PACKAGE, os.path.join(ROOT, PACKAGE))
timeline = _load("timeline", "timeline.py")
errors = sys.modules[PACKAGE + ".errors"]

# ---------------------------------------------------------------------------
# Fake common/rig modules for the tool module (which needs no Cascadeur for the cases below)
# ---------------------------------------------------------------------------


def _fail(error, hint=None, **data):
    result = {"ok": False, "error": str(error)}
    if hint:
        result["hint"] = str(hint)
    result.update(data)
    return result


class FakeModelViewer:
    def get_objects(self, name=None):
        return [] if name else list(FAKE_OBJECTS)


FAKE_OBJECTS = []


class FakeScene:
    def model_viewer(self):
        return FakeModelViewer()


fake_common = types.ModuleType(PACKAGE + ".common")
fake_common.ToolError = errors.ToolError
fake_common.ok = lambda **data: {"ok": True, **data}
fake_common.fail = _fail
fake_common.jsonable = lambda value: value
fake_common.scenes = lambda: (None, None, FakeScene())
sys.modules[PACKAGE + ".common"] = fake_common

fake_rig = types.ModuleType(PACKAGE + ".rig")


class FakeRigError(Exception):
    def result(self):
        return {"ok": False, "error": str(self), "hint": "fake"}


fake_rig.RigError = FakeRigError
fake_rig.CONTROLLERS = []
fake_rig.controllers = lambda character=None: list(fake_rig.CONTROLLERS)
fake_rig.resolve = lambda names, character=None: []
sys.modules[PACKAGE + ".rig"] = fake_rig

_package(PACKAGE + ".tools", os.path.join(ROOT, PACKAGE, "tools"))
_load("tools._schema", os.path.join("tools", "_schema.py"))
tool_timeline = _load("tools.timeline", os.path.join("tools", "timeline.py"))


# ---------------------------------------------------------------------------
# Frame and interpolation parsing
# ---------------------------------------------------------------------------


class FrameParsingTests(unittest.TestCase):
    def test_accepts_whole_frame_numbers(self):
        for value in (15, "15", " 15 ", 15.0):
            self.assertEqual(timeline.parse_frame(value), 15, value)

    def test_rejects_bad_frame_numbers(self):
        for value in (-1, 1.5, True, False, "x", None, "", timeline.MAX_FRAME + 1):
            with self.assertRaises(errors.ToolError, msg=repr(value)):
                timeline.parse_frame(value)

    def test_error_names_the_parameter(self):
        with self.assertRaises(errors.ToolError) as caught:
            timeline.parse_frame(-4, "start")
        self.assertIn("start", str(caught.exception))

    def test_parse_frames_sorts_and_deduplicates(self):
        self.assertEqual(timeline.parse_frames([20, 0, 10, 10]), [0, 10, 20])
        self.assertEqual(timeline.parse_frames(7), [7])
        self.assertEqual(timeline.parse_frames("20, 0;10"), [0, 10, 20])
        self.assertEqual(timeline.parse_frames((5, "3")), [3, 5])

    def test_parse_frames_rejects_empty_and_missing(self):
        for value in (None, [], "", " , "):
            with self.assertRaises(errors.ToolError, msg=repr(value)):
                timeline.parse_frames(value)

    def test_parse_frames_rejects_a_bad_element(self):
        with self.assertRaises(errors.ToolError):
            timeline.parse_frames([0, "ten"])


class InterpolationTests(unittest.TestCase):
    def test_known_values(self):
        expected = {
            0: "bezier",
            1: "low_amplitude_bezier",
            2: "linear",
            3: "step",
            4: "fixed",
            5: "none",
            6: "clamped_bezier",
            7: "ai",
        }
        for value, name in expected.items():
            self.assertEqual(timeline.interpolation_name(value), name)

    def test_prefers_the_enum_name(self):
        class Interpolation:
            name = "CLAMPED_BEZIER"

        self.assertEqual(timeline.interpolation_name(Interpolation()), "clamped_bezier")

    def test_none_and_unknown_values(self):
        self.assertIsNone(timeline.interpolation_name(None))
        self.assertEqual(timeline.interpolation_name(99), "unknown(99)")
        self.assertEqual(timeline.interpolation_name("LINEAR"), "linear")


# ---------------------------------------------------------------------------
# Key summary (pure)
# ---------------------------------------------------------------------------


class SummarizeKeysTests(unittest.TestCase):
    def test_reports_keys_interpolation_and_segments(self):
        summary = timeline.summarize_keys(
            [0, 10, 20],
            {0: "bezier", 10: "linear", 20: "step"},
            start=0,
            end=20,
        )
        self.assertEqual(summary["keys"], [0, 10, 20])
        self.assertEqual(summary["interpolation"], {0: "bezier", 10: "linear", 20: "step"})
        self.assertEqual(
            summary["segments"],
            [
                {"first": 0, "last": 9, "key": True, "interpolation": "bezier"},
                {"first": 10, "last": 19, "key": True, "interpolation": "linear"},
                {"first": 20, "last": 20, "key": True, "interpolation": "step"},
            ],
        )

    def test_range_starts_inside_an_interval(self):
        summary = timeline.summarize_keys(
            [0, 10, 20],
            {0: "bezier", 10: "linear", 20: "step"},
            start=5,
            end=15,
        )
        self.assertEqual(summary["keys"], [10])
        self.assertEqual(
            summary["segments"],
            [
                {"first": 5, "last": 9, "key": False, "interpolation": "bezier"},
                {"first": 10, "last": 15, "key": True, "interpolation": "linear"},
            ],
        )

    def test_range_after_the_last_key_keeps_its_interpolation(self):
        summary = timeline.summarize_keys([0, 10], {0: "bezier", 10: "linear"}, start=25, end=30)
        self.assertEqual(summary["keys"], [])
        self.assertEqual(
            summary["segments"],
            [{"first": 25, "last": 30, "key": False, "interpolation": "linear"}],
        )

    def test_layer_without_keys(self):
        summary = timeline.summarize_keys([], {}, start=3, end=7)
        self.assertEqual(summary["keys"], [])
        self.assertEqual(summary["interpolation"], {})
        self.assertEqual(
            summary["segments"],
            [{"first": 3, "last": 7, "key": False, "interpolation": None}],
        )

    def test_segments_cover_the_range_contiguously(self):
        summary = timeline.summarize_keys([2, 4, 9], {}, start=0, end=12)
        self.assertEqual(summary["segments"][0]["first"], 0)
        self.assertEqual(summary["segments"][-1]["last"], 12)
        for before, after in zip(summary["segments"], summary["segments"][1:], strict=False):
            self.assertEqual(before["last"] + 1, after["first"])
        keyed = [segment["first"] for segment in summary["segments"] if segment["key"]]
        self.assertEqual(keyed, [2, 4, 9])

    def test_reversed_range_is_an_error(self):
        with self.assertRaises(errors.ToolError):
            timeline.summarize_keys([], {}, 10, 0)


# ---------------------------------------------------------------------------
# Duck-typed layer helpers
# ---------------------------------------------------------------------------


class FakeInterval:
    def __init__(self, name):
        self.interpolation = types.SimpleNamespace(name=name)


class FakeSection:
    def __init__(self, name):
        self.interval = FakeInterval(name)


class FakeLayer:
    def __init__(self, keys, sections):
        self._keys = keys
        self.sections = sections

    def key_frame_indices(self):
        return list(self._keys)

    def is_key(self, pos):
        return pos in self._keys


class LayerReadTests(unittest.TestCase):
    def test_key_frames_are_sorted_ints(self):
        self.assertEqual(timeline.key_frames(FakeLayer([20, 0, 10], {})), [0, 10, 20])

    def test_key_frames_of_a_broken_layer_is_empty(self):
        class Broken:
            def key_frame_indices(self):
                raise RuntimeError("no layer")

        self.assertEqual(timeline.key_frames(Broken()), [])

    def test_interpolation_by_key(self):
        layer = FakeLayer([0, 10], {0: FakeSection("BEZIER"), 10: FakeSection("LINEAR")})
        self.assertEqual(timeline.interpolation_by_key(layer), {0: "bezier", 10: "linear"})


# ---------------------------------------------------------------------------
# Tool module: argument errors, whole-character resolution and the tool table
# ---------------------------------------------------------------------------


class ToolTests(unittest.TestCase):
    def setUp(self):
        fake_rig.CONTROLLERS = []
        FAKE_OBJECTS.clear()

    def test_set_frame_requires_a_frame(self):
        result = tool_timeline.set_frame()
        self.assertFalse(result["ok"])
        self.assertIn("frame", result["error"])
        self.assertTrue(result["hint"])

    def test_set_frame_rejects_a_bad_frame(self):
        result = tool_timeline.set_frame(frame=-2)
        self.assertFalse(result["ok"])
        self.assertIn("-2", result["error"])

    def test_set_animation_range_requires_a_bound(self):
        result = tool_timeline.set_animation_range()
        self.assertFalse(result["ok"])
        self.assertIn("first", result["error"])
        self.assertIn("hint", result)

    def test_set_animation_range_rejects_a_reversed_range(self):
        result = tool_timeline.set_animation_range(first=30, last=10)
        self.assertFalse(result["ok"])
        self.assertIn("greater", result["error"])

    def test_list_keys_rejects_a_reversed_range(self):
        result = tool_timeline.list_keys(start=20, end=10)
        self.assertFalse(result["ok"])
        self.assertIn("start", result["error"])
        self.assertIn("hint", result)

    def test_select_frames_requires_start_and_end(self):
        result = tool_timeline.select_frames()
        self.assertFalse(result["ok"])
        self.assertIn("start", result["error"])

    def test_select_frames_rejects_a_reversed_range(self):
        result = tool_timeline.select_frames(start=9, end=3)
        self.assertFalse(result["ok"])
        self.assertIn("greater", result["error"])

    def test_whole_character_uses_every_controller(self):
        fake_rig.CONTROLLERS = [{"id": "a"}, {"id": "b"}]
        self.assertEqual(tool_timeline._objects(None), ["a", "b"])
        self.assertEqual(tool_timeline._objects(""), ["a", "b"])

    def test_whole_character_falls_back_to_every_object(self):
        FAKE_OBJECTS.extend(["o1", "o2"])
        self.assertEqual(tool_timeline._objects(None), ["o1", "o2"])

    def test_tool_table(self):
        names = [tool["name"] for tool in tool_timeline.TOOLS]
        self.assertEqual(
            names,
            [
                "get_timeline",
                "set_frame",
                "set_animation_range",
                "set_key",
                "delete_key",
                "list_keys",
                "select_frames",
            ],
        )
        self.assertEqual(len(names), len(set(names)))
        for tool in tool_timeline.TOOLS:
            self.assertTrue(tool["description"].strip().endswith("."), tool["name"])
            self.assertEqual(tool["inputSchema"]["type"], "object", tool["name"])
            self.assertTrue(callable(tool.get("fn")), tool["name"])
            self.assertNotIn("handler", tool)


if __name__ == "__main__":
    unittest.main()
