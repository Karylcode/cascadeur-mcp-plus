"""Unit tests for the AI inbetweening tools (no Cascadeur needed).

cascadeur_mcp_plus/tools/inbetweening.py imports common/rig/timeline, so this module installs fake
common and rig modules and the real (pure-Python) timeline helper before loading it. The pure
helpers are tested for real; the begin/poll/cancel cycle runs against a small fake scene so the
background-job contract is checked without Qt.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace

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


def _load(name: str, filename: str):
    full = PACKAGE + "." + name
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, os.path.join(ROOT, PACKAGE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


# Fakes are installed in setUpModule, not here: unittest imports every test module before it runs
# any test, so installing package-name stand-ins at import time would leak into other test modules.
_REPLACED = {}
_package(PACKAGE, os.path.join(ROOT, PACKAGE))
timeline = _load("timeline", "timeline.py")
errors = sys.modules[PACKAGE + ".errors"]
inbetweening = None


# ---------------------------------------------------------------------------
# Fake Cascadeur side (just enough for the tool module)
# ---------------------------------------------------------------------------


class FakeInterval:
    def __init__(self, name):
        self.interpolation = SimpleNamespace(name=name)


class FakeSection:
    def __init__(self, name):
        self.interval = FakeInterval(name)


class FakeLayer:
    def __init__(self, name, keys, interpolation):
        self.header = SimpleNamespace(name=name)
        self._keys = list(keys)
        self.sections = {frame: FakeSection(interpolation.get(frame, "STEP")) for frame in keys}

    def key_frame_indices(self):
        return list(self._keys)

    def set_interpolation(self, name):
        for frame in self._keys:
            self.sections[frame] = FakeSection(name)


class FakeViewer:
    def __init__(self, layers, mapping):
        self.layers = layers
        self.mapping = mapping

    def layer(self, layer_id):
        return self.layers[layer_id]

    def find_layer(self, layer_id):
        return self.layers[layer_id]

    def all_layer_ids(self):
        return list(self.layers)

    def layer_id_by_obj_id_or_null(self, object_id):
        return self.mapping.get(object_id)


class FakeSelection:
    def __init__(self, ids):
        self.ordered_ids = list(ids)
        self.ids = set(ids)


class FakeSelector:
    def __init__(self):
        self.ids = ["old"]
        self.pivot_value = "old-pivot"
        self.select_calls = []

    def selected(self):
        return FakeSelection(self.ids)

    def pivot(self):
        return self.pivot_value

    def select(self, ids, pivot=None):
        self.select_calls.append((set(ids), pivot))
        self.ids = list(ids)
        self.pivot_value = pivot


class FakeFramesInterval:
    first = 0
    last = 30


class FakeLayerSelection:
    def frames_interval(self):
        return FakeFramesInterval()


class FakeLayerSelector:
    def __init__(self):
        self.ids = ["layer1"]
        self.restore_calls = []

    def all_included_layer_ids(self):
        return list(self.ids)

    def selection(self):
        return FakeLayerSelection()

    def set_full_selection_by_parts(self, ids, first, last):
        self.restore_calls.append((list(ids), first, last))
        self.ids = list(ids)


class FakeModelViewer:
    def get_object_name(self, object_id):
        return "hand_MainPoint_l" if object_id == "hand" else str(object_id)


class FakeScene:
    def __init__(self, viewer):
        self.viewer = viewer
        self.selector_value = FakeSelector()
        self.layer_selector = FakeLayerSelector()
        self.frame = 0
        self.frames = []

    def layers_viewer(self):
        return self.viewer

    def model_viewer(self):
        return FakeModelViewer()

    def selector(self):
        return self.selector_value

    def get_layers_selector(self):
        return self.layer_selector

    def get_current_frame(self, clamp_animation=True):
        return self.frame

    def set_current_frame(self, frame):
        self.frames.append(frame)
        self.frame = frame


class FakeSettings:
    def get_int_value(self, group, key):
        return 60 if (group, key) == ("InbetweeningTool", "MaxIntervalLength") else 0

    def get_float_value(self, group, key):
        return 0.0


class FakeViewScene:
    def get_setting_handler(self):
        return FakeSettings()


class FakeActionManager:
    def __init__(self):
        self.calls = []

    def call_action(self, key):
        self.calls.append(key)
        if CALLBACK is not None:
            CALLBACK()


CALLBACK = None
APP = SimpleNamespace(get_action_manager=lambda: ACTION_MANAGER)
ACTION_MANAGER = FakeActionManager()
VIEW = FakeViewScene()
LAYERS = {}
VIEWER = FakeViewer(LAYERS, {"hand": "layer1"})
SCENE = FakeScene(VIEWER)


def _fail(error, hint=None, **data):
    result = {"ok": False, "error": str(error)}
    if hint:
        result["hint"] = str(hint)
    result.update(data)
    return result


class FakeRigError(Exception):
    def result(self):
        return {"ok": False, "error": str(self), "hint": "fake"}


def setUpModule():
    """Install the fake package modules and load the tool module for this test module only."""
    global inbetweening, timeline, fake_common, fake_rig
    for name in (
        PACKAGE + ".common",
        PACKAGE + ".rig",
        PACKAGE + ".timeline",
        PACKAGE + ".tools.inbetweening",
    ):
        _REPLACED[name] = sys.modules.pop(name, None)
    timeline = _load("timeline", "timeline.py")

    fake_common = types.ModuleType(PACKAGE + ".common")
    fake_common.ToolError = errors.ToolError
    fake_common.LOG_PATH = os.path.join(tempfile.gettempdir(), "cascadeur-mcp-plus-T20-no-such-log.log")
    fake_common.ok = lambda **data: {"ok": True, **data}
    fake_common.fail = _fail
    fake_common.jsonable = lambda value: value
    fake_common.scenes = lambda: (APP, VIEW, SCENE)
    fake_common.log_tail = lambda since=None, contains=None, lines=40: []
    sys.modules[PACKAGE + ".common"] = fake_common

    fake_rig = types.ModuleType(PACKAGE + ".rig")
    fake_rig.RigError = FakeRigError
    fake_rig.controllers = lambda character=None: [{"id": "hand"}]
    fake_rig.resolve = lambda names, character=None: ["hand"]
    sys.modules[PACKAGE + ".rig"] = fake_rig

    _package(PACKAGE + ".tools", os.path.join(ROOT, PACKAGE, "tools"))
    _load("tools._schema", os.path.join("tools", "_schema.py"))
    inbetweening = _load("tools.inbetweening", os.path.join("tools", "inbetweening.py"))
    # The real tool queues the blocking Cascadeur action on the next event-loop turn; unit tests
    # run it immediately so begin/poll can be checked without Qt.
    inbetweening._schedule = lambda callback: callback()


class _Clock:
    """A monotonic clock the tests advance explicitly."""

    def __init__(self):
        self.value = 100.0

    def monotonic(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


CLOCK = _Clock()


def _reset_world(keys=(0, 30), interpolation=None):
    global CALLBACK
    inbetweening._now = CLOCK.monotonic
    CLOCK.value = 100.0
    inbetweening._clear_active()
    ACTION_MANAGER.calls.clear()
    LAYERS.clear()
    LAYERS["layer1"] = FakeLayer("Arm_L", keys, interpolation or {0: "STEP", 30: "STEP"})
    VIEWER.mapping = {"hand": "layer1"}
    SCENE.selector_value = FakeSelector()
    SCENE.layer_selector = FakeLayerSelector()
    SCENE.frame = 7
    SCENE.frames.clear()
    CALLBACK = None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class IntervalTests(unittest.TestCase):
    def test_parse_interval(self):
        self.assertEqual(inbetweening.parse_interval(0, 30), (0, 30))
        self.assertEqual(inbetweening.parse_interval("0", "30"), (0, 30))

    def test_parse_interval_rejects_empty_and_reversed_ranges(self):
        for start, end in ((10, 10), (20, 10), (-1, 10), ("a", 10)):
            with self.assertRaises(errors.ToolError, msg=(start, end)):
                inbetweening.parse_interval(start, end)


class KeyReportTests(unittest.TestCase):
    def test_report_and_prerequisites(self):
        report = inbetweening.key_report({"arm": [0, 30]}, 0, 30)
        self.assertEqual(report["key_frames"], [0, 30])
        self.assertEqual(report["inside"], [0, 30])
        self.assertEqual(report["before"], [0])
        self.assertEqual(report["after"], [30])
        inbetweening.check_prerequisites(report, 0, 30)

    def test_report_finds_missing_boundaries(self):
        report = inbetweening.key_report({"arm": [10]}, 0, 30)
        self.assertEqual(report["layers_without_boundary_keys"], ["arm"])
        with self.assertRaises(errors.ToolError) as caught:
            inbetweening.check_prerequisites(report, 0, 30)
        self.assertIn("at least two", str(caught.exception))

    def test_missing_start_and_end_are_separate_errors(self):
        open_right = inbetweening.key_report({"arm": [0, 10]}, 0, 30)
        with self.assertRaises(errors.ToolError) as caught:
            inbetweening.check_prerequisites(open_right, 0, 30)
        self.assertIn("at or after", str(caught.exception))
        open_left = inbetweening.key_report({"arm": [10, 30]}, 0, 30)
        with self.assertRaises(errors.ToolError) as caught:
            inbetweening.check_prerequisites(open_left, 0, 30)
        self.assertIn("at or before", str(caught.exception))

    def test_interval_without_inside_key_is_rejected(self):
        report = inbetweening.key_report({"arm": [0, 30]}, 5, 20)
        with self.assertRaises(errors.ToolError) as caught:
            inbetweening.check_prerequisites(report, 5, 20)
        self.assertIn("between frames", str(caught.exception))

    def test_longest_gap_only_counts_gaps_touching_the_interval(self):
        self.assertEqual(
            inbetweening.longest_key_gap({"arm": [0, 10, 40]}, 0, 40),
            {"length": 30, "frames": [10, 40]},
        )
        self.assertEqual(
            inbetweening.longest_key_gap({"arm": [0, 10, 40]}, 0, 10),
            {"length": 10, "frames": [0, 10]},
        )
        self.assertIsNone(inbetweening.longest_key_gap({"arm": [0, 10]}, 20, 30)["length"])


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        _reset_world()

    def test_snapshot_and_layer_name(self):
        snapshot = inbetweening.snapshot_layers(VIEWER, ["layer1"])
        self.assertEqual(snapshot["layer1"]["name"], "Arm_L")
        self.assertEqual(snapshot["layer1"]["keys"], [0, 30])
        self.assertEqual(snapshot["layer1"]["interpolation"], {0: "step", 30: "step"})

    def test_diff_reports_changed_keys_and_interpolation(self):
        before = inbetweening.snapshot_layers(VIEWER, ["layer1"])
        LAYERS["layer1"]._keys = [0, 15, 30]
        LAYERS["layer1"].sections = {0: FakeSection("BEZIER"), 15: FakeSection("AI"), 30: FakeSection("STEP")}
        after = inbetweening.snapshot_layers(VIEWER, ["layer1"])
        diff = inbetweening.diff_snapshots(before, after, 0, 30)
        self.assertEqual(diff["keys_added"], 1)
        self.assertEqual(diff["changed_first"], 0)
        self.assertEqual(diff["changed_last"], 15)
        self.assertEqual(diff["changed_layers"], ["Arm_L"])

    def test_unchanged_snapshots_have_no_range(self):
        snapshot = inbetweening.snapshot_layers(VIEWER, ["layer1"])
        diff = inbetweening.diff_snapshots(snapshot, snapshot, 0, 30)
        self.assertIsNone(diff["changed_first"])
        self.assertEqual(diff["keys_added"], 0)

    def test_interpolation_range_covers_the_whole_interval(self):
        before = inbetweening.snapshot_layers(VIEWER, ["layer1"])
        LAYERS["layer1"].sections[0] = FakeSection("AI")
        after = inbetweening.snapshot_layers(VIEWER, ["layer1"])
        self.assertEqual(inbetweening.changed_interpolation_range(before, after, 0, 30), (0, 29))
        self.assertIsNone(inbetweening.changed_interpolation_range(before, before, 0, 30))

    def test_interval_summary_finds_ai_layers(self):
        LAYERS["layer1"].set_interpolation("AI")
        snapshot = inbetweening.snapshot_layers(VIEWER, ["layer1"])
        summary = inbetweening.interval_summary(snapshot, 0, 30)
        self.assertEqual(summary["ai_layers"], ["Arm_L"])
        self.assertEqual(summary["interpolation"], {"ai": 1})

    def test_signature_changes_with_data(self):
        snapshot = inbetweening.snapshot_layers(VIEWER, ["layer1"])
        first = inbetweening.signature(snapshot)
        LAYERS["layer1"].set_interpolation("LINEAR")
        self.assertNotEqual(first, inbetweening.signature(inbetweening.snapshot_layers(VIEWER, ["layer1"])))


class LogTests(unittest.TestCase):
    def test_messages_errors_and_done(self):
        lines = [
            "Inbetweening: interpolate after 0",
            "Inbetweening: done",
            "Motion Generation error: no rig",
            "unrelated line",
        ]
        messages = inbetweening.log_messages(lines)
        self.assertEqual(len(messages), 3)
        self.assertEqual(inbetweening.log_error(lines), "Motion Generation error: no rig")
        self.assertTrue(inbetweening.log_done(lines))
        self.assertIsNone(inbetweening.log_error(["Inbetweening: done"]))
        self.assertFalse(inbetweening.log_done(["Inbetweening: interpolate after 0"]))


# ---------------------------------------------------------------------------
# begin/poll/cancel against the fake scene
# ---------------------------------------------------------------------------


class JobTests(unittest.TestCase):
    def setUp(self):
        _reset_world()

    def tearDown(self):
        inbetweening._cancel()

    def test_one_key_is_refused_before_the_action(self):
        _reset_world(keys=(0,), interpolation={0: "STEP"})
        result = inbetweening.ai_inbetween(start=0, end=30, controllers=["left_hand"])
        self.assertFalse(result["ok"], result)
        self.assertIn("at least two", result["error"])
        self.assertEqual(ACTION_MANAGER.calls, [])
        self.assertIsNone(inbetweening._active())

    def test_motion_generation_requires_the_same_key_bounds(self):
        _reset_world(keys=(0,), interpolation={0: "STEP"})
        result = inbetweening.generate_motion(start=0, end=30, controllers=["left_hand"])
        self.assertFalse(result["ok"], result)
        self.assertIn("at least two", result["error"])

    def test_begin_selects_and_calls_the_action(self):
        result = inbetweening.ai_inbetween(start=0, end=30, controllers=["left_hand"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(ACTION_MANAGER.calls, [inbetweening.AI_ACTION])
        self.assertEqual(SCENE.selector_value.ids, ["hand"])
        self.assertEqual(SCENE.layer_selector.ids, ["layer1"])
        self.assertIsNotNone(inbetweening._active())

    def test_second_begin_is_refused_while_one_runs(self):
        self.assertTrue(inbetweening.ai_inbetween(start=0, end=30, controllers=["left_hand"])["ok"])
        second = inbetweening.generate_motion(start=0, end=30, controllers=["left_hand"])
        self.assertFalse(second["ok"], second)
        self.assertIn("still running", second["error"])
        self.assertEqual(ACTION_MANAGER.calls, [inbetweening.AI_ACTION])

    def test_poll_finishes_when_ai_interpolation_settles_and_restores_state(self):
        self.assertTrue(inbetweening.ai_inbetween(start=0, end=30, controllers=["left_hand"])["ok"])
        LAYERS["layer1"].set_interpolation("AI")
        update = inbetweening._poll(start=0, end=30, controllers=["left_hand"])
        for _ in range(inbetweening.QUIET_POLLS + 1):
            CLOCK.advance(0.2)
            update = inbetweening._poll(start=0, end=30, controllers=["left_hand"])
            if update.get("done"):
                break
        self.assertTrue(update.get("done"), update)
        result = update["result"]
        self.assertEqual(result["first_frame"], 0)
        self.assertEqual(result["last_frame"], 30)
        self.assertEqual(result["ai_layers"], 1)
        self.assertEqual(result["changed_first"], 0)
        self.assertEqual(result["changed_last"], 30)
        self.assertEqual(SCENE.selector_value.ids, ["old"])
        self.assertEqual(SCENE.layer_selector.restore_calls[-1], (["layer1"], 0, 30))
        self.assertEqual(SCENE.frame, 7)
        self.assertIsNone(inbetweening._active())

    def test_poll_fails_when_ai_never_lands(self):
        self.assertTrue(inbetweening.ai_inbetween(start=0, end=30, controllers=["left_hand"])["ok"])
        inbetweening._poll(start=0, end=30, controllers=["left_hand"])  # records the after-action snapshot
        CLOCK.advance(inbetweening.AI_APPLY_TIMEOUT_S + 1)
        update = inbetweening._poll(start=0, end=30, controllers=["left_hand"])
        self.assertFalse(update["ok"], update)
        self.assertIn("did not mark", update["error"])
        self.assertIsNone(inbetweening._active())
        self.assertEqual(SCENE.selector_value.ids, ["old"])

    def test_cancel_restores_the_old_selection(self):
        self.assertTrue(inbetweening.ai_inbetween(start=0, end=30, controllers=["left_hand"])["ok"])
        inbetweening._cancel()
        self.assertIsNone(inbetweening._active())
        self.assertEqual(SCENE.selector_value.ids, ["old"])
        self.assertEqual(SCENE.frame, 7)

    def test_motion_poll_needs_a_change(self):
        self.assertTrue(inbetweening.generate_motion(start=0, end=30, controllers=["left_hand"])["ok"])
        inbetweening._poll(start=0, end=30, controllers=["left_hand"])  # records the after-action snapshot
        CLOCK.advance(inbetweening.MOTION_CHANGE_TIMEOUT_S + 1)
        update = inbetweening._poll(start=0, end=30, controllers=["left_hand"])
        self.assertFalse(update["ok"], update)
        self.assertIn("without changing", update["error"])


class ToolTableTests(unittest.TestCase):
    def test_tools_are_background_jobs_with_poll_and_cancel(self):
        names = [tool["name"] for tool in inbetweening.TOOLS]
        self.assertEqual(names, ["ai_inbetween", "generate_motion"])
        for tool in inbetweening.TOOLS:
            self.assertTrue(tool["description"].strip().endswith("."), tool["name"])
            self.assertTrue(tool["background"], tool["name"])
            self.assertTrue(callable(tool["fn"]), tool["name"])
            self.assertTrue(callable(tool["poll"]), tool["name"])
            self.assertTrue(callable(tool["cancel"]), tool["name"])
            self.assertEqual(tool["inputSchema"]["type"], "object", tool["name"])
            self.assertEqual(tool["inputSchema"]["required"], ["start", "end"])
            self.assertEqual(tool["job_timeout"], inbetweening.JOB_TIMEOUT_S)

    def test_descriptions_explain_the_difference(self):
        by_name = {tool["name"]: tool for tool in inbetweening.TOOLS}
        self.assertIn("AI inbetweening", by_name["ai_inbetween"]["description"])
        self.assertIn("motion generator", by_name["generate_motion"]["description"])
        self.assertIn("keyframes", by_name["ai_inbetween"]["description"])


def tearDownModule():
    """Remove this module's fakes and put back whatever another test module had loaded."""
    for name in (
        PACKAGE + ".tools.inbetweening",
        PACKAGE + ".timeline",
        PACKAGE + ".rig",
        PACKAGE + ".common",
    ):
        sys.modules.pop(name, None)
    for name, module in _REPLACED.items():
        if module is not None:
            sys.modules[name] = module


if __name__ == "__main__":
    unittest.main()
