"""Unit tests for the cleanup tools (T19, no Cascadeur needed).

cascadeur_mcp_plus/tools/cleanup.py is loaded through a synthetic package with fake
common/rig/layers/ui modules, the technique tests/test_physics.py uses. The pure metrics,
the argument/range validation, the log classifier and the background job's state machine are
exercised here; the real Cascadeur actions are covered by tests/live/test_cleanup.py.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "t19_cleanup_pkg"


def _package(name: str, path: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = [path]
    sys.modules[name] = module
    return module


def _load(name: str, filename: str):
    full = PACKAGE + "." + name
    spec = importlib.util.spec_from_file_location(full, os.path.join(ROOT, "cascadeur_mcp_plus", filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


_package(PACKAGE, os.path.join(ROOT, "cascadeur_mcp_plus"))
_package(PACKAGE + ".tools", os.path.join(ROOT, "cascadeur_mcp_plus", "tools"))
errors = _load("errors", "errors.py")
timeline = _load("timeline", "timeline.py")


# ---------------------------------------------------------------------------
# Fake common / rig / layers / ui modules
# ---------------------------------------------------------------------------


def _fail(error, hint=None, **data):
    result = {"ok": False, "error": str(error)}
    if hint:
        result["hint"] = str(hint)
    result.update(data)
    return result


def _ok(**data):
    return {"ok": True, **data}


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class FakeCommon(types.ModuleType):
    LOG_PATH = os.path.join(os.path.dirname(__file__), "not-a-real-cascadeur-log.log")

    def __init__(self):
        super().__init__(PACKAGE + ".common")
        self.world = None
        self.lines = []

    def ok(self, **data):
        return _ok(**data)

    def fail(self, error, hint=None, **data):
        return _fail(error, hint, **data)

    def jsonable(self, value):
        return _jsonable(value)

    def scenes(self):
        return None, self.world.view, self.world.scene

    def log_tail(self, since=None, contains=None, lines=40):
        return list(self.lines)[-lines:]


common = FakeCommon()
sys.modules[PACKAGE + ".common"] = common


class FakeRig(types.ModuleType):
    class RigError(Exception):
        def result(self):
            return _fail(str(self), "check the controller names")

    def __init__(self):
        super().__init__(PACKAGE + ".rig")
        self.objects = []
        self.aliases = {}

    def controllers(self):
        return [{"id": object_id} for object_id in self.objects]

    def resolve(self, names, character=None):
        result = []
        for name in names:
            if name not in self.aliases:
                raise self.RigError(f"unknown controller {name!r}")
            result.append(self.aliases[name])
        return result


rig = FakeRig()
sys.modules[PACKAGE + ".rig"] = rig


class FakeLayers(types.ModuleType):
    def __init__(self):
        super().__init__(PACKAGE + ".tools.layers")
        self.blocked = None

    def ensure_unlocked(self, object_ids):
        return self.blocked


layers = FakeLayers()
sys.modules[PACKAGE + ".tools.layers"] = layers


class FakeUi(types.ModuleType):
    def __init__(self):
        super().__init__(PACKAGE + ".ui")
        self.dialog_list = []
        self.clicks = []
        self.key_presses = []
        self.tree_elements = []

    def dialogs(self):
        return {"dialogs": list(self.dialog_list)}

    def click(self, ref=None, query=None, **kwargs):
        self.clicks.append(ref or query)
        return {"clicked": ref}

    def keys(self, sequence, window=None):
        self.key_presses.append(sequence)
        return {"pressed": sequence}

    def tree(self, **kwargs):
        return {"elements": list(self.tree_elements), "windows": []}


ui = FakeUi()
sys.modules[PACKAGE + ".ui"] = ui


cleanup = _load("tools.cleanup", os.path.join("tools", "cleanup.py"))


# ---------------------------------------------------------------------------
# Fake scene graph (enough for _start_job and _poll)
# ---------------------------------------------------------------------------


class FakeBoundary:
    def __init__(self, first=0, last=20):
        self.first_frame = first
        self.last_frame = last
        self.first_visible_frame = first
        self.last_visible_frame = last


class FakeInterval:
    def __init__(self, first, last):
        self.first = first
        self.last = last


class FakeSelection:
    def __init__(self, first, last):
        self._interval = FakeInterval(first, last)

    def frames_interval(self):
        return self._interval


class FakeLayersSelector:
    def __init__(self, world):
        self.world = world

    def set_full_selection_by_parts(self, ids, first, last):
        self.world.selection_request = (list(ids), int(first), int(last))

    def selection(self):
        return FakeSelection(*self.world.selection_reply)


class FakeSelector:
    def __init__(self, world):
        self.world = world

    def select(self, ids, pivot=None):
        self.world.selected = list(ids)
        return True

    def selected(self):
        return types.SimpleNamespace(ordered_ids=list(self.world.selected_ids))


class FakeLayersViewer:
    def __init__(self, world):
        self.world = world

    def all_layer_ids(self):
        return ["layer-1", "layer-2"]

    def layer_id_by_obj_id_or_null(self, object_id):
        return "layer-1"

    def layer(self, layer_id):
        return self.world.layers.get(layer_id, FakeLayer([], {}))

    def header(self, layer_id):
        return types.SimpleNamespace(name=str(layer_id))


class FakeLayer:
    def __init__(self, keys, interpolation):
        self._keys = keys
        self.sections = {
            frame: types.SimpleNamespace(interval=types.SimpleNamespace(interpolation=name))
            for frame, name in interpolation.items()
        }

    def key_frame_indices(self):
        return list(self._keys)


class FakeBehaviourViewer:
    def __init__(self, world):
        self.world = world

    def get_behaviour_by_name(self, object_id, name):
        return str(object_id) + ":" + name

    def get_behaviour_data(self, behaviour, name):
        return str(behaviour) + ":" + name

    def get_behaviours(self, name):
        return []


class FakeDataViewer:
    def __init__(self, world):
        self.world = world

    def get_data_value(self, data_id, frame=None):
        return self.world.position


class FakeModelViewer:
    def __init__(self, world):
        self._behaviour_viewer = FakeBehaviourViewer(world)
        self._data_viewer = FakeDataViewer(world)

    def behaviour_viewer(self):
        return self._behaviour_viewer

    def data_viewer(self):
        return self._data_viewer

    def get_object_name(self, object_id):
        return str(object_id)


class FakeScene:
    def __init__(self, world):
        self.world = world
        self._selector = FakeSelector(world)
        self._layers_viewer = FakeLayersViewer(world)
        self._model_viewer = FakeModelViewer(world)

    def selector(self):
        return self._selector

    def layers_viewer(self):
        return self._layers_viewer

    def get_layers_selector(self):
        return self.world.layers_selector

    def model_viewer(self):
        return self._model_viewer

    def get_current_frame(self, clamp=True):
        return 0


class FakeViewScene:
    def __init__(self, world):
        self.world = world

    def animation_boundary(self):
        return FakeBoundary(*self.world.boundary)

    def get_setting_handler(self):
        return types.SimpleNamespace(get_float_value=lambda group, key: 30.0)


class FakeWorld:
    def __init__(self):
        self.boundary = (0, 20)
        self.selection_reply = (0, 20)
        self.selection_request = None
        self.selected = []
        self.selected_ids = []
        self.position = [0.0, 1.0, 0.0]
        self.layers = {
            "layer-1": FakeLayer([0, 10, 20], {0: "bezier", 10: "linear"}),
            "layer-2": FakeLayer([5], {5: "step"}),
        }
        self.layers_selector = FakeLayersSelector(self)
        self.scene = FakeScene(self)
        self.view = FakeViewScene(self)
        self.objects = ["obj-1", "obj-2"]
        rig.objects = list(self.objects)
        rig.aliases = {"left_foot": "obj-1", "right_foot": "obj-2"}
        common.world = self


def reset_world():
    world = FakeWorld()
    ui.dialog_list = []
    ui.clicks = []
    ui.key_presses = []
    ui.tree_elements = []
    layers.blocked = None
    cleanup._clear_state()
    return world


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class RangeTests(unittest.TestCase):
    def test_defaults_are_the_whole_animation(self):
        self.assertEqual(cleanup.parse_range(None, None, 0, 30), (0, 30))

    def test_partial_bound(self):
        self.assertEqual(cleanup.parse_range(5, None, 0, 30), (5, 30))
        self.assertEqual(cleanup.parse_range(None, 25, 0, 30), (0, 25))

    def test_accepts_strings(self):
        self.assertEqual(cleanup.parse_range("3", "9", 0, 30), (3, 9))

    def test_start_after_end_is_rejected(self):
        with self.assertRaises(cleanup._BadArgument):
            cleanup.parse_range(9, 3, 0, 30)

    def test_outside_animation_is_rejected(self):
        with self.assertRaises(cleanup._BadArgument):
            cleanup.parse_range(-1, 10, 0, 30)

    def test_cleaning_needs_three_frames(self):
        with self.assertRaises(cleanup._BadArgument):
            cleanup.parse_range(5, 6, 0, 30, cleanup.FIX_FOOT)
        with self.assertRaises(cleanup._BadArgument):
            cleanup.parse_range(5, 6, 0, 30, cleanup.FIX_COLLISIONS)

    def test_prepare_keys_accepts_two_frames(self):
        self.assertEqual(cleanup.parse_range(5, 6, 0, 30, cleanup.PREPARE_KEYS), (5, 6))


class ControllerTests(unittest.TestCase):
    def test_none_means_whole_character(self):
        self.assertIsNone(cleanup.parse_controllers(None))

    def test_string_and_list_are_accepted(self):
        self.assertEqual(cleanup.parse_controllers("left_foot"), ["left_foot"])
        self.assertEqual(cleanup.parse_controllers(["left_foot", "right_foot"]), ["left_foot", "right_foot"])

    def test_empty_or_invalid_is_rejected(self):
        for value in ([], [""], [1], 5, {"name": "left_foot"}):
            with self.assertRaises(cleanup._BadArgument):
                cleanup.parse_controllers(value)


class SlideSummaryTests(unittest.TestCase):
    def test_horizontal_distance_ignores_y(self):
        self.assertAlmostEqual(cleanup.horizontal_distance([0, 0, 0], [3, 100, 4]), 5.0)

    def test_only_consecutive_contact_frames_count(self):
        points = {0: ["foot"], 1: ["foot"], 2: [], 3: ["foot"]}
        positions = {
            "foot": {
                0: [0, 0, 0],
                1: [3, 0, 4],
                2: [100, 0, 100],
                3: [101, 0, 100],
            }
        }
        summary = cleanup.slide_summary(points, positions, 0, 3)
        self.assertAlmostEqual(summary["slide_total_cm"], 5.0)
        self.assertEqual(summary["contact_transitions"], 1)
        self.assertEqual(summary["contact_frames"], 1)
        self.assertEqual(summary["point_count"], 1)

    def test_y_movement_is_not_slide(self):
        points = {0: ["foot"], 1: ["foot"]}
        positions = {"foot": {0: [1, 0, 1], 1: [1, 50, 1]}}
        self.assertEqual(cleanup.slide_summary(points, positions, 0, 1)["slide_total_cm"], 0.0)

    def test_missing_position_is_skipped(self):
        points = {0: ["foot"], 1: ["foot"]}
        positions = {"foot": {0: [0, 0, 0]}}
        summary = cleanup.slide_summary(points, positions, 0, 1)
        self.assertEqual(summary["contact_transitions"], 0)
        self.assertEqual(summary["pairs"], [])

    def test_pairs_are_returned_for_restricted_comparison(self):
        points = {0: ["foot"], 1: ["foot"]}
        positions = {"foot": {0: [0, 0, 0], 1: [2, 0, 0]}}
        summary = cleanup.slide_summary(points, positions, 0, 1)
        self.assertEqual(summary["pairs"], [{"point": "foot", "frame": 0, "slide_cm": 2.0}])

    def test_truncated_flag(self):
        points = {0: ["a", "b", "c"], 1: ["a", "b", "c"]}
        positions = {key: {0: [0, 0, 0], 1: [1, 0, 0]} for key in "abc"}
        summary = cleanup.slide_summary(points, positions, 0, 1)
        self.assertFalse(summary["truncated_pairs"])


class RestrictSlideTests(unittest.TestCase):
    def test_only_the_original_pairs_are_compared(self):
        before = {"pairs": [{"point": "a", "frame": 0, "slide_cm": 10.0}, {"point": "b", "frame": 0, "slide_cm": 5.0}]}
        after = {
            "pairs": [
                {"point": "a", "frame": 0, "slide_cm": 2.0},
                {"point": "b", "frame": 0, "slide_cm": 1.0},
                {"point": "c", "frame": 0, "slide_cm": 99.0},
            ]
        }
        result = cleanup.restrict_slide(before, after)
        self.assertEqual(result["slide_total_cm"], 3.0)
        self.assertEqual(result["slide_max_cm"], 2.0)
        self.assertEqual(result["matched_pairs"], 2)
        self.assertEqual(result["before_pairs"], 2)

    def test_missing_after_pair_counts_as_zero(self):
        before = {"pairs": [{"point": "a", "frame": 0, "slide_cm": 4.0}]}
        result = cleanup.restrict_slide(before, {"pairs": []})
        self.assertEqual(result["slide_total_cm"], 0.0)
        self.assertEqual(result["matched_pairs"], 0)


class CollisionSummaryTests(unittest.TestCase):
    def test_totals_and_maximum(self):
        infos = {0: {"a": 1.0, "b": 2.0}, 2: {"a": 0.5}}
        summary = cleanup.collision_summary(infos)
        self.assertAlmostEqual(summary["penetration_total_cm"], 3.5)
        self.assertAlmostEqual(summary["penetration_max_cm"], 2.0)
        self.assertEqual(summary["max_depth_frame"], 0)
        self.assertEqual(summary["max_depth_point"], "b")
        self.assertEqual(summary["colliding_frames"], 2)
        self.assertEqual(summary["colliding_points"], 2)
        self.assertEqual(summary["point_depths"][0], {"frame": 0, "point": "a", "depth_cm": 1.0})

    def test_negative_depths_are_absolutised(self):
        summary = cleanup.collision_summary({0: {"a": -2.5}})
        self.assertEqual(summary["penetration_total_cm"], 2.5)

    def test_empty(self):
        summary = cleanup.collision_summary({})
        self.assertEqual(summary["penetration_total_cm"], 0.0)
        self.assertIsNone(summary["max_depth_frame"])

    def test_restrict_to_original_points_and_frames(self):
        before = {
            "point_depths": [
                {"frame": 0, "point": "a", "depth_cm": 4.0},
                {"frame": 1, "point": "b", "depth_cm": 2.0},
            ]
        }
        after = {
            "point_depths": [
                {"frame": 0, "point": "a", "depth_cm": 1.0},
                {"frame": 1, "point": "b", "depth_cm": 0.5},
                {"frame": 2, "point": "c", "depth_cm": 50.0},
            ]
        }
        result = cleanup.restrict_collisions(before, after)
        self.assertEqual(result["penetration_total_cm"], 1.5)
        self.assertEqual(result["penetration_max_cm"], 1.0)
        self.assertEqual(result["matched_pairs"], 2)

    def test_missing_after_point_is_zero(self):
        before = {"point_depths": [{"frame": 0, "point": "a", "depth_cm": 4.0}]}
        result = cleanup.restrict_collisions(before, {"point_depths": []})
        self.assertEqual(result["penetration_total_cm"], 0.0)


class KeySummaryTests(unittest.TestCase):
    def test_counts_and_interpolation(self):
        layers = [("Hand_L", [0, 10, 20], {0: "bezier", 10: "linear"})]
        summary = cleanup.key_summary(layers, 0, 20)
        self.assertEqual(summary["key_count"], 3)
        self.assertEqual(summary["key_frames"], [0, 10, 20])
        self.assertEqual(summary["interpolation"], {"bezier": 1, "linear": 1})
        self.assertEqual(summary["layers_with_keys"], 1)

    def test_range_clips_keys(self):
        layers = [("Hand_L", [0, 10, 30], {})]
        summary = cleanup.key_summary(layers, 5, 20)
        self.assertEqual(summary["key_count"], 1)
        self.assertEqual(summary["key_frames"], [10])


class DeltaTests(unittest.TestCase):
    def test_total_delta_and_percent(self):
        before = {"slide_total_cm": 10.0}
        after = {"slide_total_cm": 2.5}
        self.assertEqual(cleanup.total_delta(before, after, "slide_total_cm"), 7.5)
        self.assertEqual(cleanup.reduction_percent(before, after, "slide_total_cm"), 75.0)

    def test_zero_before_has_no_percentage(self):
        self.assertIsNone(cleanup.reduction_percent({"x": 0.0}, {"x": 1.0}, "x"))


class ParseLogTests(unittest.TestCase):
    def test_foot_completion(self):
        log = cleanup.parse_log(
            [
                "Command: FixFoot: interpolate position and rotation",
                "Fulcrum motion cleaning completed from 0 to 39 frames",
            ],
            cleanup.FIX_FOOT,
        )
        self.assertEqual(log["completed"], {"first": 0, "last": 39})
        self.assertEqual(log["commands"], ["FixFoot: interpolate position and rotation"])
        self.assertEqual(log["errors"], [])

    def test_collision_completion(self):
        log = cleanup.parse_log(["Collision cleaning completed from 5 to 9 frames"], cleanup.FIX_COLLISIONS)
        self.assertEqual(log["completed"], {"first": 5, "last": 9})

    def test_auto_keys_done_has_no_frame_range(self):
        log = cleanup.parse_log(["Command: Auto keys", "Auto keys: done"], cleanup.PREPARE_KEYS)
        self.assertIsNotNone(log["completed"])
        self.assertIsNone(log["completed"]["first"])
        self.assertIn("done", log["completed"]["message"])

    def test_foot_errors(self):
        log = cleanup.parse_log(["Interval is less than 3 frames, Select more frames"], cleanup.FIX_FOOT)
        self.assertTrue(log["errors"])
        self.assertIsNone(log["completed"])

    def test_collision_errors(self):
        log = cleanup.parse_log(["FixCollisions: No CenterOfMass found in the scene."], cleanup.FIX_COLLISIONS)
        self.assertTrue(log["errors"])

    def test_auto_keys_errors(self):
        log = cleanup.parse_log(["action 'AutoKeys' failed"], cleanup.PREPARE_KEYS)
        self.assertTrue(log["errors"])


# ---------------------------------------------------------------------------
# Background job state machine
# ---------------------------------------------------------------------------


BASE_STATE = {
    "kind": cleanup.FIX_FOOT,
    "action": cleanup.ACTION_FIX_FOOT,
    "first": 0,
    "last": 20,
    "controllers": ["left_foot"],
    "explicit_controllers": True,
    "object_ids": ["obj-1"],
    "probe_frames": [0, 10, 20],
    "before_probe": {},
    "last_probe": {},
    "before": {},
    "measurement_error": None,
    "selection": {"selected": 1},
    "interval": {"first": 0, "last": 20, "layers": 1},
    "started": 0.0,
    "triggered_at": None,
    "phase": "trigger",
    "poll_count": 0,
    "stable": 0,
    "changed": False,
    "completed": None,
    "error": None,
    "log_offset": 0,
    "dialog_actions": [],
    "commands": [],
    "run_clicked": None,
    "panel_open": False,
    "last_ui_check": 0.0,
    "first_change": None,
}


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class JobTests(unittest.TestCase):
    def setUp(self):
        self.world = reset_world()
        self.clock = FakeClock()
        self._real = {
            "time": cleanup.time,
            "trigger": cleanup._trigger,
            "dialogs": cleanup._handle_dialogs,
            "new_log": cleanup._new_log,
            "probe": cleanup._probe,
            "probe_changed": cleanup._probe_changed,
            "close_ui": cleanup._close_ui,
            "measure_slide": cleanup.measure_slide,
            "measure_collisions": cleanup.measure_collisions,
            "layers": cleanup._ensure_unlocked,
        }
        cleanup.time = self.clock
        cleanup._trigger = lambda state: None
        cleanup._handle_dialogs = lambda state: None
        cleanup._close_ui = lambda state: []
        cleanup._ensure_unlocked = lambda object_ids: layers.ensure_unlocked(object_ids)

    def tearDown(self):
        cleanup.time = self._real["time"]
        cleanup._trigger = self._real["trigger"]
        cleanup._handle_dialogs = self._real["dialogs"]
        cleanup._new_log = self._real["new_log"]
        cleanup._probe = self._real["probe"]
        cleanup._probe_changed = self._real["probe_changed"]
        cleanup._close_ui = self._real["close_ui"]
        cleanup.measure_slide = self._real["measure_slide"]
        cleanup.measure_collisions = self._real["measure_collisions"]
        cleanup._ensure_unlocked = self._real["layers"]
        cleanup._clear_state()

    def install_state(self, **overrides):
        state = dict(BASE_STATE)
        state.update(overrides)
        cleanup._set_state(state)
        return state

    def test_trigger_phase_calls_the_action_once(self):
        calls = []
        cleanup._trigger = lambda state: calls.append(state["action"])
        state = self.install_state()
        first = cleanup._poll()
        second = cleanup._poll()
        self.assertFalse(first["done"])
        self.assertFalse(second["done"])
        self.assertEqual(calls, [cleanup.ACTION_FIX_FOOT])
        self.assertEqual(state["phase"], "running")

    def test_trigger_error_is_reported(self):
        cleanup._trigger = lambda state: (_ for _ in ()).throw(RuntimeError("refused"))
        self.install_state()
        result = cleanup._poll()
        self.assertFalse(result["ok"])
        self.assertIn("refused", result["error"])
        self.assertIsNone(cleanup._state())

    def test_completion_log_finishes_the_job(self):
        cleanup._new_log = lambda state: ["Fulcrum motion cleaning completed from 0 to 20 frames"]
        cleanup._probe = lambda state: {}
        cleanup._probe_changed = lambda state, probe, before: False
        before = {"slide_total_cm": 10.0, "pairs": [{"point": "a", "frame": 0, "slide_cm": 10.0}]}
        after = {"slide_total_cm": 2.0, "pairs": [{"point": "a", "frame": 0, "slide_cm": 2.0}]}
        cleanup.measure_slide = lambda first, last: dict(after)
        self.install_state(before=before)
        cleanup._poll()  # trigger
        first = cleanup._poll()
        self.assertFalse(first["done"])
        self.assertEqual(cleanup._state()["phase"], "finishing")
        self.clock.advance(cleanup.POST_COMPLETE_SECONDS + 0.1)
        finished = cleanup._poll()
        self.assertTrue(finished["done"])
        result = finished["result"]
        self.assertTrue(result["ok"])
        self.assertEqual(result["change"]["slide_reduction_cm"], 8.0)
        self.assertEqual(result["change"]["basis"], "same_point_frames")
        self.assertEqual(result["same_point_frames"]["slide_total_cm"], 2.0)
        self.assertIsNone(cleanup._state())

    def test_collision_completion_uses_restricted_points(self):
        cleanup._new_log = lambda state: ["Collision cleaning completed from 0 to 20 frames"]
        cleanup._probe = lambda state: {}
        cleanup._probe_changed = lambda state, probe, before: False
        before = {
            "penetration_total_cm": 5.0,
            "point_depths": [{"frame": 0, "point": "a", "depth_cm": 5.0}],
        }
        after = {
            "penetration_total_cm": 20.0,
            "point_depths": [
                {"frame": 0, "point": "a", "depth_cm": 1.0},
                {"frame": 5, "point": "b", "depth_cm": 19.0},
            ],
        }
        cleanup.measure_collisions = lambda first, last: dict(after)
        self.install_state(kind=cleanup.FIX_COLLISIONS, action=cleanup.ACTION_FIX_COLLISIONS, before=before)
        cleanup._poll()
        cleanup._poll()
        self.clock.advance(cleanup.POST_COMPLETE_SECONDS + 0.1)
        result = cleanup._poll()["result"]
        self.assertEqual(result["change"]["penetration_reduction_cm"], 4.0)
        self.assertEqual(result["same_point_frames"]["penetration_total_cm"], 1.0)

    def test_auto_keys_done_finishes_without_pose_change(self):
        cleanup._new_log = lambda state: ["Auto keys: done"]
        cleanup._probe = lambda state: {"key_count": 3}
        cleanup._probe_changed = lambda state, probe, before: False
        before = {"key_count": 3, "layers_with_keys": 1, "key_frames": [0, 10, 20], "interpolation": {}}
        after = {
            "key_count": 5,
            "layers_with_keys": 1,
            "key_frames": [0, 5, 10, 15, 20],
            "interpolation": {"bezier": 5},
        }
        cleanup._all_layers_key_summary = lambda first, last: dict(after)
        self.install_state(
            kind=cleanup.PREPARE_KEYS,
            action=cleanup.ACTION_PREPARE_KEYS,
            before=before,
            before_probe={"key_count": 3},
        )
        cleanup._poll()
        cleanup._poll()
        self.clock.advance(cleanup.POST_COMPLETE_SECONDS + 0.1)
        result = cleanup._poll()["result"]
        self.assertTrue(result["ok"])
        self.assertEqual(result["change"]["key_delta"], 2)

    def test_error_log_fails_and_clears_state(self):
        cleanup._new_log = lambda state: ["Fulcrum motion cleaning failed"]
        cleanup._probe = lambda state: {}
        cleanup._probe_changed = lambda state, probe, before: False
        self.install_state()
        cleanup._poll()
        result = cleanup._poll()
        self.assertFalse(result["ok"])
        self.assertIn("failed", result["error"])
        self.assertIsNone(cleanup._state())

    def test_data_stable_without_log_completes(self):
        cleanup._new_log = lambda state: []
        cleanup._probe = lambda state: {"changed": True}
        cleanup._probe_changed = lambda state, probe, before: True
        cleanup.measure_slide = lambda first, last: {
            "slide_total_cm": 1.0,
            "pairs": [{"point": "a", "frame": 0, "slide_cm": 1.0}],
        }
        self.install_state(
            before={"slide_total_cm": 5.0, "pairs": [{"point": "a", "frame": 0, "slide_cm": 5.0}]},
            before_probe={"a": {0: [0, 0, 0]}},
        )
        cleanup._poll()  # trigger
        finished = None
        for _ in range(cleanup.STABLE_POLLS + 12):
            self.clock.advance(0.2)
            result = cleanup._poll()
            if result.get("done") or cleanup._state() is None:
                finished = result
                break
        self.assertIsNone(cleanup._state(), "the stable probe should have finished the job")
        self.assertTrue(finished["done"])
        self.assertEqual(finished["result"]["completion"], "data_stable")

    def test_no_change_times_out(self):
        cleanup._new_log = lambda state: []
        cleanup._probe = lambda state: {}
        cleanup._probe_changed = lambda state, probe, before: False
        self.install_state()
        cleanup._poll()
        self.clock.advance(cleanup.NO_CHANGE_SECONDS + 1.0)
        result = cleanup._poll()
        self.assertFalse(result["ok"])
        self.assertIn("did not change", result["error"])
        self.assertIsNone(cleanup._state())

    def test_open_dialogs_are_reported(self):
        ui.dialog_list = [{"kind": "dialog", "texts": ["Done"], "buttons": [{"ref": "d0:0", "text": "OK"}]}]
        cleanup._new_log = lambda state: ["Fulcrum motion cleaning completed from 0 to 20 frames"]
        cleanup._probe = lambda state: {}
        cleanup._probe_changed = lambda state, probe, before: False
        cleanup.measure_slide = lambda first, last: {"slide_total_cm": 0.0, "pairs": []}
        cleanup._close_ui = self._real["close_ui"]
        self.install_state()
        cleanup._poll()
        cleanup._poll()
        self.clock.advance(cleanup.POST_COMPLETE_SECONDS + 0.1)
        result = cleanup._poll()["result"]
        self.assertIn("open_dialogs", result)
        self.assertIn("Done", result["open_dialogs"][0])

    def test_cancel_closes_ui_and_clears_state(self):
        closed = []
        cleanup._close_ui = lambda state: closed.append(True) or []
        self.install_state()
        cleanup._cancel(start=0)
        self.assertEqual(closed, [True])
        self.assertIsNone(cleanup._state())


class StartJobTests(unittest.TestCase):
    def setUp(self):
        self.world = reset_world()
        self._measure_slide = cleanup.measure_slide
        self._measure_collisions = cleanup.measure_collisions
        self._key_summary = cleanup._all_layers_key_summary
        cleanup.measure_slide = lambda first, last: {
            "slide_total_cm": 3.0,
            "pairs": [{"point": "a", "frame": 0, "slide_cm": 3.0}],
        }
        cleanup.measure_collisions = lambda first, last: {
            "penetration_total_cm": 2.0,
            "point_depths": [{"frame": 0, "point": "a", "depth_cm": 2.0}],
        }
        cleanup._all_layers_key_summary = lambda first, last: {"key_count": 3, "layers_with_keys": 1}

    def tearDown(self):
        cleanup.measure_slide = self._measure_slide
        cleanup.measure_collisions = self._measure_collisions
        cleanup._all_layers_key_summary = self._key_summary
        cleanup._clear_state()

    def test_start_selects_objects_and_range_then_stores_the_before_state(self):
        result = cleanup._start_job(cleanup.FIX_FOOT, 0, 20, "left_foot")
        self.assertTrue(result["ok"])
        self.assertEqual(result["frames"], {"first": 0, "last": 20})
        self.assertEqual(self.world.selected, ["obj-1"])
        self.assertEqual(self.world.selection_request, (["layer-1"], 0, 20))
        state = cleanup._state()
        self.assertIsNotNone(state)
        self.assertEqual(state["phase"], "trigger")
        self.assertEqual(state["before"]["slide_total_cm"], 3.0)
        self.assertIsNone(state["error"])

    def test_whole_character_omits_controllers(self):
        cleanup._start_job(cleanup.FIX_COLLISIONS, None, None, None)
        self.assertEqual(sorted(self.world.selected), ["obj-1", "obj-2"])

    def test_unknown_controller_returns_the_rig_failure(self):
        result = cleanup._start_job(cleanup.FIX_FOOT, 0, 20, "nope")
        self.assertFalse(result["ok"])
        self.assertIn("unknown controller", result["error"])
        self.assertIsNone(cleanup._state())

    def test_locked_layer_returns_the_guard_failure(self):
        layers.blocked = _fail("locked", "unlock it")
        result = cleanup._start_job(cleanup.FIX_FOOT, 0, 20, None)
        self.assertFalse(result["ok"])
        self.assertIn("locked", result["error"])
        self.assertIsNone(cleanup._state())

    def test_selection_mismatch_is_reported(self):
        self.world.selection_reply = (0, 10)
        result = cleanup._start_job(cleanup.FIX_FOOT, 0, 20, None)
        self.assertFalse(result["ok"])
        self.assertIn("instead of 0-20", result["error"])
        self.assertIsNone(cleanup._state())

    def test_measurement_failure_still_starts_the_job(self):
        def boom(first, last):
            raise RuntimeError("no fulcrum data")

        cleanup.measure_slide = boom
        result = cleanup._start_job(cleanup.FIX_FOOT, 0, 20, None)
        self.assertTrue(result["ok"])
        self.assertIn("no fulcrum data", result["measurement_error"])
        self.assertEqual(cleanup._state()["measurement_error"], "RuntimeError: no fulcrum data")

    def test_another_running_job_is_refused(self):
        cleanup._start_job(cleanup.FIX_FOOT, 0, 20, None)
        result = cleanup._start_job(cleanup.FIX_FOOT, 0, 20, None)
        self.assertFalse(result["ok"])
        self.assertIn("still running", result["error"])

    def test_invalid_range_fails_before_selecting(self):
        result = cleanup._start_job(cleanup.FIX_FOOT, 10, 5, None)
        self.assertFalse(result["ok"])
        self.assertIn("must not be greater", result["error"])
        self.assertIsNone(self.world.selection_request)


class ToolTableTests(unittest.TestCase):
    def test_tools_are_background_jobs_with_the_expected_names(self):
        names = [tool["name"] for tool in cleanup.TOOLS]
        self.assertEqual(names, ["fix_foot_sliding", "fix_collisions", "prepare_keys_by_fulcrums"])
        for tool in cleanup.TOOLS:
            self.assertTrue(tool["background"])
            self.assertIn("poll", tool)
            self.assertIn("cancel", tool)
            self.assertEqual(tool["job_timeout"], 300)
            self.assertIs(tool["poll"], cleanup._poll)
            self.assertIs(tool["cancel"], cleanup._cancel)

    def test_schemas(self):
        foot = cleanup.TOOLS[0]["inputSchema"]
        self.assertEqual(set(foot["properties"]), {"start", "end", "controllers"})
        self.assertEqual(foot["required"], [])
        keys = cleanup.TOOLS[2]["inputSchema"]
        self.assertEqual(set(keys["properties"]), {"start", "end"})


if __name__ == "__main__":
    unittest.main()
