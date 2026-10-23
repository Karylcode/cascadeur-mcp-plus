"""Unit tests for the retargeting tool module (no Cascadeur needed).

The implementation imports Cascadeur, common, dispatch and scene_files lazily, so plain Python can
load it and test argument handling, log parsing, frame validation, stage orchestration and cleanup.
The real Copy/Paste actions are covered by tests/live/test_retarget.py.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_NAME = "cascadeur_mcp_plus.tools.retarget"


def load_module():
    """Load tools/retarget.py without importing the Cascadeur-only package __init__."""
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
    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]
    spec = importlib.util.spec_from_file_location(MODULE_NAME, os.path.join(ROOT, package, "tools", "retarget.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


rt = load_module()
ToolError = sys.modules["cascadeur_mcp_plus.errors"].ToolError


class FakeCommon:
    def __init__(self, log_lines=(), log_position=10):
        self.failures = []
        self.log_lines = list(log_lines)
        self.log_position = log_position

    def ok(self, **data):
        return {"ok": True, **data}

    def fail(self, error, hint=None, **data):
        self.failures.append({"error": str(error), "hint": hint, "data": data})
        result = {"ok": False, "error": str(error)}
        if hint:
            result["hint"] = hint
        result.update(data)
        return result

    def jsonable(self, value):
        if hasattr(value, "tolist"):
            return value.tolist()
        return value

    def _log_size(self):
        return self.log_position

    def log_tail(self, since=None, contains=None, lines=40):
        return list(self.log_lines)[-lines:]


class FakeView:
    def __init__(self, name, path="", scene=None):
        self._name = name
        self._path = path
        self._scene = scene

    def name(self):
        return self._name

    def get_path_name(self):
        return self._path

    def domain_scene(self):
        if self._scene is None:
            raise AssertionError("this fake view has no domain scene")
        return self._scene


class FakeBoundary:
    def __init__(self, first, last):
        self.first_frame = first
        self.last_frame = last


class FakeManager:
    def __init__(self, scenes, current):
        self._scenes = list(scenes)
        self._current = current
        self.switches = []

    def scenes(self):
        return list(self._scenes)

    def current_scene(self):
        return self._current

    def set_current_scene(self, view):
        self.switches.append(view.name())
        self._current = view


class FakeApp:
    def __init__(self, manager, pro=True):
        self._manager = manager
        self._pro = pro
        self.actions = []

    def get_scene_manager(self):
        return self._manager

    def is_pro_features_available(self):
        return self._pro

    def get_action_manager(self):
        app = self

        class Actions:
            def call_action(self, key):
                app.actions.append(key)

        return Actions()


class FakeSceneFiles:
    def __init__(self, manager, open_view=None):
        self.manager = manager
        self.open_view = open_view
        self.open_calls = []
        self.close_calls = []
        self.close_result = {"ok": True}

    def open_scene(self, path):
        self.open_calls.append(path)
        self.manager._current = self.open_view
        return {"ok": True, "scene": {"name": self.open_view.name()}}

    def close_scene(self, scene, discard_changes=False):
        self.close_calls.append((scene, discard_changes))
        return dict(self.close_result)


class RetargetArgumentTests(unittest.TestCase):
    def test_accepts_tab_name_and_absolute_casc_path(self):
        self.assertEqual(rt._scene_argument("Backflip_animation.casc", "source"), "Backflip_animation.casc")
        path = os.path.join(tempfile.gettempdir(), "rig.casc")
        self.assertEqual(rt._scene_argument(path, "source"), path)

    def test_rejects_fbx_with_t11_hint(self):
        with self.assertRaises(rt._ArgumentError) as caught:
            rt._scene_argument("C:/tmp/backflip.fbx", "source")
        self.assertIn("does not accept", str(caught.exception))
        self.assertIn("import_animation", caught.exception.hint)

    def test_rejects_other_animation_formats(self):
        for suffix in (".glb", ".bvh", ".usdz"):
            with self.subTest(suffix=suffix), self.assertRaises(rt._ArgumentError):
                rt._scene_argument("C:/tmp/take" + suffix, "target")

    def test_rejects_relative_paths(self):
        with self.assertRaises(rt._ArgumentError) as caught:
            rt._scene_argument("samples/rig.casc", "source")
        self.assertIn("absolute", str(caught.exception))

    def test_rejects_non_casc_paths(self):
        with self.assertRaises(rt._ArgumentError) as caught:
            rt._scene_argument("C:/tmp/notes.txt", "source")
        self.assertIn("must end with '.casc'", str(caught.exception))

    def test_rejects_empty_values(self):
        for value in ("", "  ", None, 3):
            with self.subTest(value=value), self.assertRaises(rt._ArgumentError):
                rt._scene_argument(value, "source")


class FrameRangeTests(unittest.TestCase):
    def test_defaults_to_whole_boundary(self):
        self.assertEqual(rt.resolve_frame_range(0, 123), (0, 123))

    def test_explicit_range(self):
        self.assertEqual(rt.resolve_frame_range(0, 123, 10, 20), (10, 20))
        self.assertEqual(rt.resolve_frame_range(0, 123, start=10), (10, 123))
        self.assertEqual(rt.resolve_frame_range(0, 123, end=20), (0, 20))

    def test_rejects_reversed_or_out_of_bounds_ranges(self):
        with self.assertRaises(rt._ArgumentError) as caught:
            rt.resolve_frame_range(0, 123, 20, 10)
        self.assertIn("after end", str(caught.exception))
        with self.assertRaises(rt._ArgumentError) as caught:
            rt.resolve_frame_range(0, 123, -1, 20)
        self.assertIn("outside", str(caught.exception))
        with self.assertRaises(rt._ArgumentError) as caught:
            rt.resolve_frame_range(0, 123, 0, 124)
        self.assertIn("outside", str(caught.exception))

    def test_rejects_empty_animation(self):
        with self.assertRaises(rt._ArgumentError) as caught:
            rt.resolve_frame_range(10, 5)
        self.assertIn("no animation range", str(caught.exception))

    def test_frame_numbers_must_be_whole(self):
        self.assertEqual(rt._frame_number(2.0, "start"), 2)
        for value in (True, 2.5, "2"):
            with self.subTest(value=value), self.assertRaises(rt._ArgumentError):
                rt._frame_number(value, "start")


class SceneLookupTests(unittest.TestCase):
    def setUp(self):
        self.a = FakeView("A.casc", "C:/scenes/A.casc")
        self.b = FakeView("B.casc", "C:/scenes/B.casc")
        self.scenes = [self.a, self.b]

    def test_finds_by_exact_name_and_normalized_path(self):
        self.assertIs(rt.find_open_scene(self.scenes, "A.casc", "target"), self.a)
        self.assertIs(rt.find_open_scene(self.scenes, "c:\\scenes\\a.CASC", "target"), self.a)

    def test_missing_source_path_returns_none_for_opening(self):
        self.assertIsNone(rt.find_open_scene(self.scenes, "C:/scenes/missing.casc", "source"))

    def test_missing_target_names_open_scenes(self):
        with self.assertRaises(rt._ArgumentError) as caught:
            rt.find_open_scene(self.scenes, "C:/scenes/missing.casc", "target")
        self.assertIn("No open target", str(caught.exception))
        self.assertIn("Open the target scene first", caught.exception.hint)

    def test_duplicate_matches_are_rejected(self):
        duplicate = FakeView("A.casc", "C:/other/A.casc")
        with self.assertRaises(rt._ArgumentError) as caught:
            rt.find_open_scene([self.a, duplicate], "A.casc", "source")
        self.assertIn("2 open scene tabs", str(caught.exception))


class LogParsingTests(unittest.TestCase):
    def test_copy_and_paste_markers(self):
        self.assertTrue(rt.retarget_event(["Retargeting: Copied"], rt.COPY_MARKER)["ok"])
        self.assertTrue(rt.retarget_event(["2026 Retargeting: Pasted"], rt.PASTE_MARKER)["ok"])

    def test_error_line_wins_over_an_older_success(self):
        event = rt.retarget_event(
            ["Retargeting: Copied", "Retargeting: Error: no frames selected"],
            rt.COPY_MARKER,
        )
        self.assertFalse(event["ok"])
        self.assertEqual(event["message"], "no frames selected")

    def test_known_error_without_prefix_is_detected(self):
        event = rt.retarget_event(["native failure: different model types"], rt.PASTE_MARKER)
        self.assertFalse(event["ok"])
        self.assertIn("different model types", event["message"])

    def test_no_marker_returns_none(self):
        self.assertIsNone(rt.retarget_event(["Command: Change layers selection"], rt.COPY_MARKER))

    def test_wait_returns_the_marker(self):
        common = FakeCommon(["Retargeting: Copied"])
        event, lines = rt.wait_for_retarget_event(common, 0, rt.COPY_MARKER, timeout=0.01)
        self.assertTrue(event["ok"])
        self.assertEqual(lines, ["Retargeting: Copied"])


class ErrorHintTests(unittest.TestCase):
    def test_known_errors(self):
        self.assertIn("no keyed animation", rt.error_hint("no frames selected"))
        self.assertIn("RigInfo", rt.error_hint("no rig info"))
        self.assertIn("same model type", rt.error_hint("different model types"))
        self.assertIn("does not support", rt.error_hint("layout not found"))
        self.assertIn("quadruped", rt.error_hint("retargeting.quadruped.fingers is not supported"))
        self.assertIn("Pro or Studio", rt.error_hint(rt.LICENSE_MARKER))
        self.assertIn("session failed", rt.error_hint("session failed").lower())

    def test_unknown_error_is_generic(self):
        self.assertIn("Cascadeur rejected", rt.error_hint("something new"))


class ProbeJointTests(unittest.TestCase):
    class JointId:
        def __init__(self, name):
            self.name = name

        def is_null(self):
            return False

        def __str__(self):
            return self.name

    class Model:
        def __init__(self, names):
            self.names = names
            self.behaviours = self.Behaviours(self.names)

        def get_object_name(self, object_id):
            return str(object_id)

        def behaviour_viewer(self):
            return self.behaviours

        class Behaviours:
            def __init__(self, names):
                self.names = names

            def get_behaviours(self, name):
                return list(self.names) if name == "Joint" else []

            def get_behaviour_owner(self, object_id):
                return ProbeJointTests.JointId(object_id)

    class View:
        def __init__(self, names):
            self.model = ProbeJointTests.Model(names)

        def domain_scene(self):
            return self

        def model_viewer(self):
            return self.model

    def test_pelvis_right_hand_and_left_foot_are_preferred(self):
        view = self.View(["pelvis", "hand_r", "hand_l", "foot_l", "foot_r", "f_index1_r", "chest"])
        names = [name for _object, name in rt._probe_joints(view)]
        self.assertEqual(names[:3], ["pelvis", "hand_r", "foot_l"])

    def test_falls_back_to_the_first_joints(self):
        view = self.View(["chest", "neck", "head"])
        names = [name for _object, name in rt._probe_joints(view)]
        self.assertEqual(names, ["chest", "neck", "head"])

    def test_mixamo_style_names_are_understood_without_the_rig_module(self):
        view = self.View(["mixamorig:LeftHand", "mixamorig:RightHand", "mixamorig:LeftFoot", "pelvis"])
        names = [name for _object, name in rt._probe_joints(view)]
        self.assertEqual(names, ["pelvis", "mixamorig:RightHand", "mixamorig:LeftFoot"])

    def test_pose_changes_compares_every_probe_joint(self):
        joints = [("1", "pelvis"), ("2", "hand_r")]
        before = {"pelvis": {"global_position": {"0": [0, 0, 0]}}, "hand_r": {"global_position": {"0": [1, 1, 1]}}}
        after = {"pelvis": {"global_position": {"0": [0, 0, 0]}}, "hand_r": {"global_position": {"0": [2, 2, 2]}}}
        changed, all_changed = rt.pose_changes(before, after, joints)
        self.assertEqual(changed, ["hand_r"])
        self.assertFalse(all_changed)
        self.assertEqual(rt.pose_changes(before, after, [("2", "hand_r")]), (["hand_r"], True))

    def test_source_and_target_coverage_checks(self):
        self.assertTrue(rt._source_has_animation({"animation_size": 124, "last_key": 123}, 0, 123))
        self.assertFalse(rt._source_has_animation({"animation_size": 1, "last_key": 0}, 0, 100))
        self.assertTrue(rt._source_has_animation({"animation_size": 1, "last_key": 0}, 0, 0))
        self.assertTrue(rt._source_has_animation({}, 0, 100))
        self.assertFalse(rt._target_has_animation({"animation_size": 1, "last_key": 0}, 100))
        self.assertFalse(rt._target_has_animation({"animation_size": 101, "last_key": 0}, 100))
        self.assertTrue(rt._target_has_animation({"animation_size": 124, "last_key": 123}, 123))

    def test_unavailable_sample_is_detected(self):
        self.assertFalse(rt.sample_has_unavailable({"pelvis": {"global_position": {"0": [0, 0, 0]}}}))
        self.assertTrue(rt.sample_has_unavailable({"pelvis": {"global_position": {"50": "unavailable: no data"}}}))


class RigAndSelectionTests(unittest.TestCase):
    class BehaviourViewer:
        def __init__(self, counts):
            self.counts = counts

        def is_valid_behaviour_type(self, name):
            return name in ("RigInfo", "AnimationInfo", "Joint")

        def get_behaviours(self, name):
            return [object() for _ in range(self.counts.get(name, 0))]

    class Scene:
        def __init__(self, counts):
            self.behaviours = RigAndSelectionTests.BehaviourViewer(counts)

        def model_viewer(self):
            return self

        def behaviour_viewer(self):
            return self.behaviours

    class View(FakeView):
        def __init__(self, counts):
            super().__init__("rig.casc")
            self._domain = RigAndSelectionTests.Scene(counts)

        def domain_scene(self):
            return self._domain

    def test_requires_exactly_one_rig_info(self):
        info = rt._require_rig(self.View({"RigInfo": 1, "AnimationInfo": 0}), "source")
        self.assertEqual(info, {"rig_info": 1, "animation_info": 0})
        with self.assertRaises(rt._ArgumentError) as caught:
            rt._require_rig(self.View({"RigInfo": 0}), "target")
        self.assertIn("0 RigInfo", str(caught.exception))
        with self.assertRaises(rt._ArgumentError) as caught:
            rt._require_rig(self.View({"RigInfo": 0, "AnimationInfo": 1}), "target")
        self.assertIn("Rig mode", caught.exception.hint)

    class Selector:
        def __init__(self):
            self.calls = []

        def all_included_layer_ids(self):
            return ["layer-a", "layer-b"]

        def set_full_selection_by_parts(self, ids, first, last):
            self.calls.append((list(ids), first, last))
            return True

    class SelectionScene:
        def __init__(self):
            self.selector = RigAndSelectionTests.Selector()

        def get_layers_selector(self):
            return self.selector

    class SelectionView(FakeView):
        def __init__(self):
            super().__init__("source.casc")
            self._domain = RigAndSelectionTests.SelectionScene()

        def domain_scene(self):
            return self._domain

    def test_selects_all_layers_over_the_range(self):
        view = self.SelectionView()
        self.assertEqual(rt._select_frames(view, 0, 123), 2)
        self.assertEqual(view._domain.selector.calls, [(["layer-a", "layer-b"], 0, 123)])

    class BoundaryView:
        def __init__(self, first, last):
            self.boundary = FakeBoundary(first, last)

        def name(self):
            return "target.casc"

        def animation_boundary(self):
            return self.boundary

    def test_extends_only_a_short_target_boundary(self):
        view = self.BoundaryView(0, 100)
        self.assertEqual(rt._set_last_frame(view, 123), (100, 123))
        self.assertEqual(view.boundary.last_frame, 123)
        stable = self.BoundaryView(0, 123)
        self.assertEqual(rt._set_last_frame(stable, 100), (123, 123))
        self.assertEqual(stable.boundary.last_frame, 123)


class StagePrepareSourceTests(unittest.TestCase):
    def state(self, start=None, end=None):
        return {
            "source": FakeView("source.casc"),
            "target": FakeView("target.casc"),
            "manager": FakeManager([], None),
            "requested_start": start,
            "requested_end": end,
        }

    def test_uses_requested_range_and_reads_target_before_switching(self):
        state = self.state(10, 20)
        seen = []
        metrics = [{"animation_size": 124, "last_key": 123}, {"animation_size": 1, "last_key": 0}]
        with (
            mock.patch.object(rt, "_require_rig", return_value={"rig_info": 1, "animation_info": 0}),
            mock.patch.object(rt, "_boundary", return_value=(0, 123)),
            mock.patch.object(rt, "scene_metrics", side_effect=metrics),
            mock.patch.object(rt, "_switch_to", side_effect=lambda manager, view: seen.append(view.name())),
            mock.patch.object(rt, "_select_frames", return_value=14),
        ):
            result = rt._stage_prepare_source(state)
        self.assertEqual((result["start"], result["end"]), (10, 20))
        self.assertEqual(result["source_layers"], 14)
        self.assertEqual(seen, ["source.casc"])
        self.assertEqual(result["target_before"], {"animation_size": 1, "last_key": 0})

    def test_defaults_to_the_source_boundary(self):
        state = self.state()
        metrics = [{"animation_size": 38, "last_key": 42}, {"animation_size": 1, "last_key": 0}]
        with (
            mock.patch.object(rt, "_require_rig", return_value={"rig_info": 1, "animation_info": 0}),
            mock.patch.object(rt, "_boundary", return_value=(5, 42)),
            mock.patch.object(rt, "scene_metrics", side_effect=metrics),
            mock.patch.object(rt, "_switch_to"),
            mock.patch.object(rt, "_select_frames", return_value=3),
        ):
            result = rt._stage_prepare_source(state)
        self.assertEqual((result["start"], result["end"]), (5, 42))

    def test_static_source_is_rejected_as_no_frames_selected(self):
        state = self.state()
        metrics = [{"animation_size": 1, "last_key": 0}, {"animation_size": 1, "last_key": 0}]
        with (
            mock.patch.object(rt, "_require_rig", return_value={"rig_info": 1, "animation_info": 0}),
            mock.patch.object(rt, "_boundary", return_value=(0, 100)),
            mock.patch.object(rt, "scene_metrics", side_effect=metrics),
            self.assertRaises(rt._ArgumentError) as caught,
        ):
            rt._stage_prepare_source(state)
        self.assertIn("no frames selected", str(caught.exception))
        self.assertEqual(caught.exception.extra["source_metrics"]["animation_size"], 1)

    def test_out_of_bounds_request_is_rejected(self):
        state = self.state(0, 999)
        with (
            mock.patch.object(rt, "_require_rig", return_value={"rig_info": 1, "animation_info": 0}),
            mock.patch.object(rt, "_boundary", return_value=(0, 123)),
            self.assertRaises(rt._ArgumentError) as caught,
        ):
            rt._stage_prepare_source(state)
        self.assertIn("outside", str(caught.exception))


class StageFinishTests(unittest.TestCase):
    def state(self):
        return {
            "source": FakeView("source.casc", "C:/scenes/source.casc"),
            "target": FakeView("target.casc", "C:/scenes/target.casc"),
            "start": 0,
            "end": 123,
            "frames": [0, 61, 123],
            "joints": [("1", "pelvis")],
            "source_first": 0,
            "source_last": 123,
            "source_metrics": {"joints": 58},
            "source_layers": 14,
            "source_info": {"rig_info": 1, "animation_info": 0},
            "target_info": {"rig_info": 1, "animation_info": 0},
            "target_before": {"first_frame": 0, "last_frame": 100, "animation_size": 1, "last_key": 0},
            "last_frame_after": 123,
            "target_layers": 14,
            "pose_before": {"pelvis": {"global_position": {"0": [0, 0, 0]}}},
        }

    def test_reports_changed_pose_and_length(self):
        common = FakeCommon()
        after = {"first_frame": 0, "last_frame": 123, "animation_size": 124, "last_key": 123, "joints": 58}
        with (
            mock.patch.object(rt, "_common", return_value=common),
            mock.patch.object(rt, "scene_metrics", return_value=after),
            mock.patch.object(
                rt,
                "_sample_pose",
                return_value={"pelvis": {"global_position": {"0": [1, 2, 3]}}},
            ),
        ):
            result = rt._stage_finish(self.state())
        self.assertTrue(result["ok"])
        self.assertEqual(result["target"]["animation_size"], 124)
        self.assertEqual(result["target"]["last_key"], 123)
        self.assertEqual(result["target"]["pose"]["changed_joints"], ["pelvis"])
        self.assertTrue(result["target"]["pose"]["all_changed"])
        self.assertNotIn("hint", result)

    def test_unchanged_pose_returns_a_hint(self):
        common = FakeCommon()
        after = {"first_frame": 0, "last_frame": 123, "animation_size": 124, "last_key": 123, "joints": 58}
        state = self.state()
        with (
            mock.patch.object(rt, "_common", return_value=common),
            mock.patch.object(rt, "scene_metrics", return_value=after),
            mock.patch.object(rt, "_sample_pose", return_value=state["pose_before"]),
        ):
            result = rt._stage_finish(state)
        self.assertTrue(result["ok"])
        self.assertFalse(result["target"]["pose"]["all_changed"])
        self.assertIn("did not change", result["hint"])

    def test_static_target_after_paste_is_an_error(self):
        common = FakeCommon()
        after = {"first_frame": 0, "last_frame": 100, "animation_size": 1, "last_key": 0, "joints": 68}
        with (
            mock.patch.object(rt, "_common", return_value=common),
            mock.patch.object(rt, "scene_metrics", return_value=after),
            self.assertRaises(rt._ArgumentError) as caught,
        ):
            rt._stage_finish(self.state())
        self.assertIn("no multi-frame animation", str(caught.exception))
        self.assertEqual(caught.exception.extra["action"], "paste")

    def test_unreadable_target_frames_are_an_error(self):
        common = FakeCommon()
        after = {"first_frame": 0, "last_frame": 123, "animation_size": 124, "last_key": 123, "joints": 58}
        with (
            mock.patch.object(rt, "_common", return_value=common),
            mock.patch.object(rt, "scene_metrics", return_value=after),
            mock.patch.object(
                rt,
                "_sample_pose",
                return_value={"pelvis": {"global_position": {"61": "unavailable: no data"}}},
            ),
            self.assertRaises(rt._ArgumentError) as caught,
        ):
            rt._stage_finish(self.state())
        self.assertIn("could not be read", str(caught.exception))


class StageFinalizeTests(unittest.TestCase):
    def test_temp_source_is_closed_after_success(self):
        source = FakeView("source.casc")
        target = FakeView("target.casc")
        manager = FakeManager([source, target], source)
        state = {"manager": manager, "source": source, "target": target, "original": target, "opened": True}
        scene_files = FakeSceneFiles(manager)
        with mock.patch.object(rt, "_scene_files", return_value=scene_files):
            result = rt._stage_finalize(state, success=True)
        self.assertTrue(result["closed"])
        self.assertEqual(manager.switches, ["target.casc"])
        self.assertEqual(scene_files.close_calls, [("source.casc", True)])

    def test_failed_run_restores_the_original_tab(self):
        original = FakeView("user.casc")
        other = FakeView("other.casc")
        manager = FakeManager([original, other], other)
        state = {"manager": manager, "source": other, "target": other, "original": original, "opened": False}
        with mock.patch.object(rt, "_scene_files") as scene_files:
            result = rt._stage_finalize(state, success=False)
        self.assertTrue(result["restored"])
        self.assertEqual(manager.switches, ["user.casc"])
        scene_files.assert_not_called()


class StagePrepareTests(unittest.TestCase):
    def setUp(self):
        self.original = FakeView("user.casc", "C:/scenes/user.casc")
        self.target = FakeView("target.casc", "C:/scenes/target.casc")
        self.source = FakeView("source.casc", "C:/scenes/source.casc")
        self.manager = FakeManager([self.original, self.target], self.original)
        self.app = FakeApp(self.manager)
        self.csc = types.ModuleType("csc")
        self.csc.app = types.SimpleNamespace(get_application=lambda: self.app)
        self.previous = sys.modules.get("csc")
        sys.modules["csc"] = self.csc
        self.addCleanup(self._restore_csc)

    def _restore_csc(self):
        if self.previous is None:
            sys.modules.pop("csc", None)
        else:
            sys.modules["csc"] = self.previous

    def test_source_path_is_opened_in_a_temporary_tab(self):
        manager = self.manager
        scene_files = FakeSceneFiles(manager, open_view=self.source)

        def add_source(path):
            manager._scenes.append(self.source)
            manager._current = self.source
            return {"ok": True}

        scene_files.open_scene = add_source
        with (
            mock.patch.object(rt, "_scene_files", return_value=scene_files),
            mock.patch.object(rt, "_actionable_dialogs", return_value=[]),
            mock.patch.object(rt.os.path, "isfile", return_value=True),
        ):
            state = rt._stage_prepare("C:/scenes/source.casc", "target.casc")
        self.assertTrue(state["opened"])
        self.assertIs(state["source"], self.source)
        self.assertIs(state["target"], self.target)

    def test_same_scene_is_rejected(self):
        with mock.patch.object(rt, "_actionable_dialogs", return_value=[]):
            with self.assertRaises(rt._ArgumentError) as caught:
                rt._stage_prepare("user.casc", "user.casc")
        self.assertIn("same scene tab", str(caught.exception))

    def test_basic_license_is_rejected_before_opening_anything(self):
        self.app._pro = False
        with self.assertRaises(rt._ArgumentError) as caught:
            rt._stage_prepare("source.casc", "target.casc")
        self.assertIn("basic and indie", str(caught.exception))
        self.assertIn("Pro or Studio", caught.exception.hint)

    def test_missing_target_lists_open_scenes(self):
        with self.assertRaises(rt._ArgumentError) as caught:
            rt._stage_prepare("source.casc", "missing.casc")
        self.assertIn("No open target", str(caught.exception))


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.common = FakeCommon()
        self.state = {
            "opened": False,
            "source": {},
            "target": {},
            "manager": None,
            "original": None,
        }
        self.calls = []

    def fake_dispatch(self):
        calls = self.calls

        class Dispatcher:
            def call_on_gui(self, fn, args, timeout):
                calls.append((getattr(fn, "__name__", type(fn).__name__), args))
                return fn(**args)

        return Dispatcher()

    def run_handler(self, final=None, events=None, **arguments):
        dispatch = self.fake_dispatch()
        final = final or {"opened": False, "closed": True, "restored": False}
        events = events or {
            "copy": ({"ok": True, "message": rt.COPY_MARKER}, ["Retargeting: Copied"]),
            "paste": ({"ok": True, "message": rt.PASTE_MARKER}, ["Retargeting: Pasted"]),
        }
        poll = mock.Mock(side_effect=[events["copy"], events["paste"]])
        with (
            mock.patch.object(rt, "_common", return_value=self.common),
            mock.patch.object(rt, "_dispatch", return_value=dispatch),
            mock.patch.object(rt, "_stage_prepare", return_value=self.state),
            mock.patch.object(rt, "_stage_prepare_source", return_value={"start": 0, "end": 123}),
            mock.patch.object(rt, "_stage_action", return_value={"offset": 10, "dialogs": []}),
            mock.patch.object(rt, "_stage_prepare_target", return_value={"joints": []}),
            mock.patch.object(rt, "_stage_finish", return_value={"ok": True, "source": {}, "target": {}}),
            mock.patch.object(rt, "_stage_finalize", return_value=final),
            mock.patch.object(rt, "wait_for_retarget_event", poll),
        ):
            return rt.retarget_animation(**arguments)

    def test_successful_call_returns_the_result_and_seconds(self):
        result = self.run_handler(source="source.casc", target="target.casc")
        self.assertTrue(result["ok"])
        self.assertIn("seconds", result)

    def test_failed_copy_uses_the_finalizer_and_reports_error(self):
        result = self.run_handler(
            events={
                "copy": ({"ok": False, "message": "no frames selected"}, ["Retargeting: Error: no frames selected"]),
                "paste": (None, []),
            },
            source="source.casc",
            target="target.casc",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "copy")
        self.assertIn("no frames selected", result["error"])

    def test_open_temp_source_close_failure_is_reported(self):
        self.state["opened"] = True
        result = self.run_handler(
            final={"opened": True, "closed": False, "close": {"ok": False, "error": "still open"}},
            source="C:/scenes/source.casc",
            target="target.casc",
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["retargeted"])
        self.assertIn("could not be closed", result["error"])

    def test_bad_fbx_is_rejected_without_dispatch(self):
        with (
            mock.patch.object(rt, "_common", return_value=FakeCommon()),
            mock.patch.object(rt, "_dispatch") as dispatch,
        ):
            result = rt.retarget_animation("C:/tmp/take.fbx")
        self.assertFalse(result["ok"])
        self.assertIn("does not accept", result["error"])
        dispatch.assert_not_called()


class ToolMetadataTests(unittest.TestCase):
    def test_tool_is_registered_as_a_handler(self):
        self.assertEqual([tool["name"] for tool in rt.TOOLS], ["retarget_animation"])
        tool = rt.TOOLS[0]
        self.assertIn("handler", tool)
        self.assertNotIn("fn", tool)
        self.assertNotIn("gui", tool)
        self.assertEqual(tool["inputSchema"]["required"], ["source"])
        self.assertEqual(tool["inputSchema"]["properties"]["start"]["type"], "integer")
        self.assertGreater(len(tool["description"]), 300)
        self.assertIn("not an 'MCP: ' step", tool["description"])
        self.assertIn(".fbx", tool["description"])


if __name__ == "__main__":
    unittest.main()
