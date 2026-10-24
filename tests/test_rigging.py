"""Unit tests for the Quick Rigging tool module (no Cascadeur needed).

The module under test imports Cascadeur and common.py lazily, so plain Python can load it and
exercise the template parser/matcher and the Rig-mode safety gate. Calls that would reach
Cascadeur's native Quick Rigging tool are left to tests/live/test_rigging.py.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_NAME = "cascadeur_mcp_plus.tools.rigging"


def load_module():
    """Load tools/rigging.py without importing the Cascadeur-only package __init__."""
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
    spec = importlib.util.spec_from_file_location(MODULE_NAME, os.path.join(ROOT, package, "tools", "rigging.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


rigging = load_module()
ToolError = sys.modules["cascadeur_mcp_plus.errors"].ToolError


def write_template(path, joints, title="Body", section="Body", settings=None):
    document = {
        "Document": [
            {
                "Title": title,
                "Sections": [
                    {
                        "Section": section,
                        "Names": [{"Bone name": name, "Joint name": name, "Joint path": []} for name in joints],
                    }
                ],
            }
        ],
        "Settings": settings or {"Is align pelvis": True, "Is create layers": True},
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle)
    return path


# ---------------------------------------------------------------------------
# Fakes for the scene + UI orchestration
# ---------------------------------------------------------------------------


class FakeObjectId:
    def __init__(self, name, type_name="Joint"):
        self.name = name
        self.type_name = type_name

    def __str__(self):
        return self.name


class FakeBehaviourViewer:
    def __init__(self, objects, counts):
        self.objects = objects
        self.counts = counts

    def is_valid_behaviour_type(self, name):
        return name in (
            "AnimationInfo",
            "RigInfo",
            "AutoPosing",
            "AutoPosingInfo",
            "TechnicalLinks",
            "Joint",
            "MeshObject",
        )

    def get_behaviours(self, name):
        if name == "Joint":
            return [obj for obj in self.objects if obj.type_name == "Joint"]
        return [object() for _ in range(self.counts.get(name, 0))]

    def get_behaviour_owner(self, behaviour):
        if behaviour in self.objects:
            return behaviour
        joints = [obj for obj in self.objects if obj.type_name == "Joint"]
        return joints[0] if joints else FakeObjectId("pelvis")


class FakeModelViewer:
    def __init__(self, scene):
        self.scene = scene
        self.behaviours = FakeBehaviourViewer(scene.objects, scene.counts)

    def get_objects(self):
        return list(self.scene.objects)

    def get_object_name(self, object_id):
        return object_id.name

    def get_object_type_name(self, object_id):
        return object_id.type_name

    def behaviour_viewer(self):
        return self.behaviours


class FakeScene:
    def __init__(self, joint_names, counts):
        self.objects = [FakeObjectId(name, "Joint") for name in joint_names]
        self.counts = dict(counts)
        self.model = FakeModelViewer(self)

    def model_viewer(self):
        return self.model


class FakeViewScene:
    def __init__(self, name="untitled", path=""):
        self._name = name
        self._path = path

    def name(self):
        return self._name

    def get_path_name(self):
        return self._path


class FakeCommon:
    def __init__(self, app, view_scene, scene, log_lines=()):
        self.app = app
        self.view_scene = view_scene
        self.scene = scene
        self.log_lines = list(log_lines)
        self.failures = []

    def scenes(self):
        return self.app, self.view_scene, self.scene

    def ok(self, **data):
        return {"ok": True, **data}

    def fail(self, error, hint=None, **data):
        self.failures.append({"error": str(error), "hint": hint, "data": data})
        result = {"ok": False, "error": str(error)}
        if hint:
            result["hint"] = str(hint)
        result.update(data)
        return result

    def log_tail(self, since=None, contains=None, lines=40):
        return list(self.log_lines)[-lines:]

    def _log_size(self):
        return 0


class FakeTool:
    def __init__(self, scene, create_adds=0, on_create=None):
        self.scene = scene
        self.create_adds = create_adds
        self.on_create = on_create
        self.calls = []

    def open_quick_rigging_tool(self):
        self.calls.append(("open",))

    def set_is_create_autoposing(self, value):
        self.calls.append(("autoposing", value))

    def set_character_mirror_plane(self, value):
        self.calls.append(("mirror", value))

    def load_template_by_fileName(self, path):
        self.calls.append(("load", path))

    def create_from_qrt_by_fileName(self, path):
        self.calls.append(("create", path))
        if self.create_adds:
            self.scene.counts["TechnicalLinks"] = self.scene.counts.get("TechnicalLinks", 0) + self.create_adds
        if self.on_create:
            self.on_create()

    def generate_rig_elements(self):
        self.calls.append(("generate",))


class FakeUi:
    def __init__(self, scene, dialogs=None, on_click=None):
        self.scene = scene
        self.dialog_list = list(dialogs or [])
        self.clicks = []
        self.on_click = on_click

    def dialogs(self):
        return {"dialogs": list(self.dialog_list)}

    def click(self, ref):
        self.clicks.append(ref)
        if self.on_click:
            self.on_click(ref)
        for dialog in list(self.dialog_list):
            if any(button.get("ref") == ref for button in dialog.get("buttons", [])):
                self.dialog_list.remove(dialog)

    def quick_windows(self, include_hidden=False):
        return []


def generate_dialog():
    return {
        "kind": "dialog",
        "texts": ["Rig elements have been added. Generate the rig?"],
        "buttons": [{"ref": "generate", "text": "Generate rig"}, {"ref": "edit", "text": "Continue editing"}],
    }


# ---------------------------------------------------------------------------
# Template parser/matcher
# ---------------------------------------------------------------------------


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_scan_reads_json_templates_and_reports_broken_files(self):
        write_template(os.path.join(self.root, "Cascy.qrigcasc"), ["pelvis", "head"])
        write_template(os.path.join(self.root, "old.qrt"), ["pelvis"])
        with open(os.path.join(self.root, "broken.qrigcasc"), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with open(os.path.join(self.root, "notes.txt"), "w", encoding="utf-8") as handle:
            handle.write("ignore me")
        templates = rigging.scan_templates(self.root)
        self.assertEqual([item["name"] for item in templates], ["Cascy", "broken", "old"])
        self.assertTrue(templates[0]["readable"])
        self.assertFalse(templates[1]["readable"])
        self.assertIn("Could not read", templates[1]["error"])
        self.assertTrue(templates[2]["readable"])

    def test_template_info_parses_joints_sections_and_settings(self):
        path = write_template(
            os.path.join(self.root, "Cascy.qrigcasc"),
            ["pelvis", "stomach", "pelvis"],
            settings={"Is align pelvis": False},
        )
        info = rigging.template_info(path)
        self.assertTrue(info["readable"])
        self.assertEqual(info["joints"], ["pelvis", "stomach"])
        self.assertEqual(info["sections"], ["Body"])
        self.assertEqual(info["documents"], ["Body"])
        self.assertEqual(info["settings"], {"Is align pelvis": False})
        self.assertGreater(info["bytes"], 0)

    def test_missing_template_folder_names_the_path(self):
        with self.assertRaises(ToolError) as caught:
            rigging.scan_templates(os.path.join(self.root, "nope"))
        self.assertIn("nope", str(caught.exception))

    def test_match_exact_case_insensitive_namespace_and_missing(self):
        template = {"joints": ["pelvis", "mixamorig:Hips", "LeftArm", "missing"]}
        result = rigging.match_template(template, ["pelvis", "mixamorig:Hips", "LeftArm"])
        self.assertEqual(result["matched"], 3)
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["coverage"], 0.75)
        self.assertEqual(result["missing"], ["missing"])

    def test_match_simple_name_drops_namespace(self):
        template = {"joints": ["mixamorig:Hips", "mixamorig:LeftArm"]}
        result = rigging.match_template(template, ["Hips", "LeftArm"])
        self.assertEqual(result["matched"], 2)
        self.assertAlmostEqual(result["coverage"], 1.0)

    def test_choose_prefers_more_matched_joints_on_equal_coverage(self):
        small = write_template(os.path.join(self.root, "small.qrigcasc"), ["pelvis", "head"])
        large = write_template(
            os.path.join(self.root, "large.qrigcasc"),
            ["pelvis", "stomach", "chest", "neck", "head", "clavicle_l"],
        )
        templates = [rigging.template_info(small), rigging.template_info(large)]
        chosen, candidates, reason = rigging.choose_template(
            templates,
            ["pelvis", "stomach", "chest", "neck", "head", "clavicle_l", "extra"],
        )
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["name"], "large")
        self.assertEqual(reason, "")
        self.assertEqual(len(candidates), 2)

    def test_choose_uses_scene_name_hint_to_break_a_tie(self):
        joints = ["pelvis", "head", "hand_l", "foot_l", "chest"]
        a = write_template(os.path.join(self.root, "Cascy.qrigcasc"), joints)
        b = write_template(os.path.join(self.root, "Other.qrigcasc"), joints)
        templates = [rigging.template_info(a), rigging.template_info(b)]
        chosen, _candidates, reason = rigging.choose_template(templates, joints, hints=["Cascy.casc"])
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["name"], "Cascy")
        self.assertEqual(reason, "")

    def test_choose_returns_candidates_when_templates_are_tied(self):
        joints = ["pelvis", "head", "hand_l", "foot_l", "chest"]
        a = write_template(os.path.join(self.root, "A.qrigcasc"), joints)
        b = write_template(os.path.join(self.root, "B.qrigcasc"), joints)
        templates = [rigging.template_info(a), rigging.template_info(b)]
        chosen, candidates, reason = rigging.choose_template(templates, joints)
        self.assertIsNone(chosen)
        self.assertEqual(len(candidates), 2)
        self.assertIn("Several", reason)

    def test_choose_returns_candidates_when_nothing_matches(self):
        path = write_template(os.path.join(self.root, "A.qrigcasc"), ["a", "b", "c", "d", "e"])
        chosen, candidates, reason = rigging.choose_template(
            [rigging.template_info(path)], ["totally", "different", "joint", "names"]
        )
        self.assertIsNone(chosen)
        self.assertEqual(len(candidates), 1)
        self.assertIn("No Quick Rigging template", reason)

    def test_resolve_by_stem_file_and_absolute_path(self):
        path = write_template(os.path.join(self.root, "Cascy.qrigcasc"), ["pelvis", "head"])
        templates = [rigging.template_info(path)]
        self.assertEqual(rigging._resolve_by_name("Cascy", templates)["name"], "Cascy")
        self.assertEqual(rigging._resolve_by_name("cascy.qrigcasc", templates)["name"], "Cascy")
        self.assertEqual(rigging._resolve_by_name(path, templates)["name"], "Cascy")

    def test_resolve_unknown_and_ambiguous_names_list_candidates(self):
        a = write_template(os.path.join(self.root, "Mixamo_A.qrigcasc"), ["pelvis"])
        b = write_template(os.path.join(self.root, "Mixamo_B.qrigcasc"), ["pelvis"])
        templates = [rigging.template_info(a), rigging.template_info(b)]
        with self.assertRaises(ToolError) as caught:
            rigging._resolve_by_name("Mixamo", templates)
        self.assertEqual(len(caught.exception.data["candidates"]), 2)
        with self.assertRaises(ToolError) as caught:
            rigging._resolve_by_name("Nope", templates)
        self.assertEqual(len(caught.exception.data["candidates"]), 2)

    def test_autoposing_count_uses_autoposing_info_behaviour(self):
        scene = FakeScene(["pelvis"], {"AutoPosingInfo": 3})
        self.assertEqual(rigging._autoposing_count(scene), 3)

    def test_commands_since_drops_scene_and_ui_noise(self):
        common = FakeCommon(
            None,
            FakeViewScene(),
            FakeScene(["pelvis"], {}),
            log_lines=[
                "2026-10-24 06:00:00,000 Command: Rig mode on",
                "2026-10-24 06:00:00,001 Command: Select default layer",
                "2026-10-24 06:00:00,002 Command: load scene",
                "2026-10-24 06:00:00,003 Command: Create rig from qrt",
                "2026-10-24 06:00:00,004 Command: Rig mode off",
            ],
        )
        self.assertEqual(
            rigging._commands_since(common, 0),
            ["Rig mode on", "Create rig from qrt", "Rig mode off"],
        )

    def test_top_dialogs_keeps_one_per_window(self):
        ui = FakeUi(FakeScene(["pelvis"], {}))
        ui.dialog_list = [
            {"kind": "dialog", "window": 1, "type": "ButtonsDialog", "texts": ["outer"], "buttons": []},
            {"kind": "dialog", "window": 1, "type": "BaseButtonsDialog", "texts": ["inner"], "buttons": []},
            {"kind": "dialog", "window": 1, "type": "DialogButton", "texts": ["Ok"], "buttons": []},
            {"kind": "dialog", "window": 2, "type": "ButtonsDialog", "texts": ["other"], "buttons": []},
        ]
        top = rigging._top_dialogs(ui)
        self.assertEqual([dialog["texts"] for dialog in top], [["outer"], ["other"]])

    def test_dialog_button_requires_an_exact_label(self):
        dialog = {
            "buttons": [
                {"ref": "normal", "text": "Normal"},
                {"ref": "dont", "text": "Don't Save"},
                {"ref": "ok", "text": "OK"},
            ]
        }
        self.assertIsNone(rigging._dialog_button(dialog, ("no",)))
        self.assertIsNone(rigging._dialog_button(dialog, ("clos",)))
        self.assertEqual(rigging._dialog_button(dialog, ("ok",))["ref"], "ok")

    def test_pre_existing_dialogs_are_not_used_for_generation_or_recognition(self):
        ui = FakeUi(FakeScene(["pelvis"], {}))
        old_recognition = {
            "kind": "dialog",
            "window": 1,
            "type": "ButtonsDialog",
            "texts": ["The character has been fully recognized."],
            "buttons": [{"ref": "old-ok", "text": "OK"}],
        }
        old_generate = {
            "kind": "dialog",
            "window": 2,
            "type": "ButtonsDialog",
            "texts": ["Rig elements have been added. Generate the rig?"],
            "buttons": [{"ref": "old-generate", "text": "Generate rig"}],
        }
        ui.dialog_list = [old_recognition, old_generate]
        baseline = rigging._dialog_signatures(ui)
        self.assertIsNone(rigging._find_generate_dialog(ui, baseline))
        warnings = []
        rigging._answer_recognition_dialogs(ui, warnings, baseline)
        self.assertEqual(ui.clicks, [], "pre-existing dialogs must not be clicked")
        self.assertEqual(warnings, [])

    def test_dismiss_leftover_only_touches_new_dialogs_and_safe_buttons(self):
        ui = FakeUi(FakeScene(["pelvis"], {}))
        old_dialog = {
            "kind": "dialog",
            "window": 1,
            "type": "ButtonsDialog",
            "texts": ["Save changes before closing?"],
            "buttons": [{"ref": "old-dont", "text": "Don't Save"}, {"ref": "old-cancel", "text": "Cancel"}],
        }
        ui.dialog_list = [old_dialog]
        baseline = rigging._dialog_signatures(ui)
        ui.dialog_list = [
            old_dialog,
            {
                "kind": "dialog",
                "window": 2,
                "type": "ButtonsDialog",
                "texts": ["Finished"],
                "buttons": [{"ref": "new-ok", "text": "OK"}],
            },
            {
                "kind": "dialog",
                "window": 3,
                "type": "ButtonsDialog",
                "texts": ["Discard the rig?"],
                "buttons": [{"ref": "new-discard", "text": "Discard"}],
            },
        ]
        warnings = []
        left = rigging._dismiss_leftover_dialogs(ui, warnings, baseline)
        self.assertEqual(ui.clicks, ["new-ok"], "only the new safe dialog may be clicked")
        self.assertEqual([dialog["texts"] for dialog in left], [["Discard the rig?"]])
        self.assertNotIn("old-dont", ui.clicks)
        self.assertNotIn("new-discard", ui.clicks)

    def test_mirror_plane_values(self):
        self.assertEqual(rigging.mirror_plane_value("YZ"), 2)
        self.assertEqual(rigging.mirror_plane_value("xz"), 1)
        self.assertEqual(rigging.mirror_plane_value(0), 0)
        self.assertEqual(rigging.mirror_plane_value("2"), 2)
        self.assertIsNone(rigging.mirror_plane_value(None))
        for bad in ("sideways", 9, True):
            with self.assertRaises(ToolError):
                rigging.mirror_plane_value(bad)


# ---------------------------------------------------------------------------
# quick_rig safety gate and orchestration
# ---------------------------------------------------------------------------


class QuickRigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        write_template(
            os.path.join(self.tmp.name, "Cascy.qrigcasc"),
            ["pelvis", "stomach", "chest", "neck", "head", "clavicle_l", "arm_l"],
        )
        self.env = mock.patch.dict(os.environ, {rigging.TEMPLATE_DIR_ENV: self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)

    def fake_scene(self, animation_info=0, rig_info=0, auto_pose=0, proto=0):
        return FakeScene(
            ["pelvis", "stomach", "chest", "neck", "head", "clavicle_l", "arm_l"],
            {
                "AnimationInfo": animation_info,
                "RigInfo": rig_info,
                "AutoPosing": auto_pose,
                "TechnicalLinks": proto,
                "Joint": 7,
            },
        )

    def patch_enter_rig_mode(self, scene):
        def enter_rig_mode():
            scene.counts["AnimationInfo"] = 1
            scene.counts["RigInfo"] = 0  # Rig mode hides the RigInfo behaviour
            return {"rig_mode": True, "changed": True, "path": "run_raw", "animation_info": 1}

        scene_tools = types.ModuleType("cascadeur_mcp_plus.scene_tools")
        scene_tools.enter_rig_mode = enter_rig_mode
        package = sys.modules["cascadeur_mcp_plus"]
        previous = getattr(package, "scene_tools", None)
        package.scene_tools = scene_tools
        self.addCleanup(setattr, package, "scene_tools", previous)

    def test_quick_rig_rejects_bad_arguments_before_cascadeur(self):
        common = FakeCommon(None, FakeViewScene(), self.fake_scene())
        with mock.patch.object(rigging, "_common", return_value=common):
            result = rigging.quick_rig(create_autoposing="yes")
            self.assertFalse(result["ok"])
            self.assertIn("create_autoposing", result["error"])
            result = rigging.quick_rig(mirror_plane="sideways")
            self.assertFalse(result["ok"])
            self.assertIn("mirror_plane", result["error"])
            result = rigging.quick_rig(enter_rig_mode="yes")
            self.assertFalse(result["ok"])
            self.assertIn("enter_rig_mode", result["error"])
            result = rigging.quick_rig(replace="yes")
            self.assertFalse(result["ok"])
            self.assertIn("replace", result["error"])

    def test_refuses_non_rig_mode_without_entering_or_generating(self):
        scene = self.fake_scene(animation_info=0, rig_info=0)
        common = FakeCommon(object(), FakeViewScene(), scene)
        called = []

        def forbidden(*_args, **_kwargs):
            called.append(True)
            raise AssertionError("the Quick Rigging tool must not be touched")

        with mock.patch.object(rigging, "_common", return_value=common):
            with mock.patch.object(rigging, "_ui", return_value=FakeUi(scene)):
                with mock.patch.object(rigging, "_rigging_tool", forbidden):
                    result = rigging.quick_rig(enter_rig_mode=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "rig_mode_check")
        self.assertEqual(result["animation_info"], 0)
        self.assertEqual(called, [])
        self.assertIn("exactly 1", result["error"])
        self.assertIn("crashes", result["error"])

    def test_refuses_generate_when_animation_info_changes_after_elements(self):
        scene = self.fake_scene(animation_info=1, rig_info=0)
        common = FakeCommon(object(), FakeViewScene(), scene)
        tool = FakeTool(scene)
        ui = FakeUi(scene)
        called = []

        def generate():
            tool.calls.append(("generate",))
            # Provoke the crash condition between Add rig elements and Generate rig.
            scene.counts["AnimationInfo"] = 2
            ui.dialog_list.append(generate_dialog())

        tool.generate_rig_elements = generate
        with mock.patch.object(rigging, "_common", return_value=common):
            with mock.patch.object(rigging, "_ui", return_value=ui):
                with mock.patch.object(rigging, "_rigging_tool", return_value=tool):
                    result = rigging.quick_rig(template="Cascy")
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "rig_mode_check")
        self.assertNotIn("generate", called, "the Generate rig button must not be pressed")
        self.assertIn(("generate",), tool.calls)
        self.assertIn("exactly 1", result["error"])

    def test_enters_rig_mode_generates_and_returns_rig_info(self):
        scene = self.fake_scene(animation_info=0, rig_info=0)
        common = FakeCommon(object(), FakeViewScene(), scene)
        self.patch_enter_rig_mode(scene)
        ui = FakeUi(scene)
        tool = FakeTool(scene, create_adds=3, on_create=lambda: ui.dialog_list.append(generate_dialog()))

        def on_click(ref):
            if ref == "generate":
                scene.counts["AnimationInfo"] = 0
                scene.counts["RigInfo"] = 1
                scene.counts["AutoPosing"] = 1

        ui.on_click = on_click

        with mock.patch.object(rigging, "_common", return_value=common):
            with mock.patch.object(rigging, "_ui", return_value=ui):
                with mock.patch.object(rigging, "_rigging_tool", return_value=tool):
                    with mock.patch.object(rigging, "_character_summary", return_value={"name": "Rig(0)"}):
                        with mock.patch.object(rigging, "_controller_summary", return_value={"total": 99}):
                            result = rigging.quick_rig(template="Cascy")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["rig_info"], 1)
        self.assertEqual(result["animation_info"], 0)
        self.assertTrue(result["template_applied"], result)
        self.assertFalse(result["replace"], result)
        self.assertEqual(result["existing"], {"rig_info": 0, "proto": 0})
        self.assertEqual(result["rig"]["name"], "Rig(0)")
        self.assertEqual(result["controllers"], {"total": 99})
        self.assertTrue(result["autoposing"]["created"])
        self.assertEqual(result["rig_mode"]["path"], "run_raw")
        self.assertEqual(ui.clicks, ["generate"])
        self.assertIn(("load", os.path.join(self.tmp.name, "Cascy.qrigcasc").replace("\\", "/")), tool.calls)
        names = [name for name, *_ in tool.calls]
        self.assertEqual(names[:3], ["open", "autoposing", "load"])
        self.assertIn("create", names)
        self.assertNotIn("generate", names, "creating proto elements shows the Generate rig dialog")

    def test_refuses_existing_rig_without_touching_scene(self):
        scene = self.fake_scene(animation_info=0, rig_info=1)
        common = FakeCommon(object(), FakeViewScene(), scene)
        ui = FakeUi(scene)
        called = []

        def forbidden(*_args, **_kwargs):
            called.append(True)
            raise AssertionError("an existing rig must not be re-rigged without replace=true")

        with mock.patch.object(rigging, "_common", return_value=common):
            with mock.patch.object(rigging, "_ui", return_value=ui):
                with mock.patch.object(rigging, "_rigging_tool", forbidden):
                    result = rigging.quick_rig()
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "existing_rig")
        self.assertEqual(result["rig_info"], 1)
        self.assertEqual(result["proto"], 0)
        self.assertIn("replace=true", result["hint"])
        self.assertEqual(called, [])
        self.assertEqual(scene.counts["RigInfo"], 1, "the refusing call must not enter Rig mode or change the scene")

    def test_refuses_existing_proto_without_replace(self):
        scene = self.fake_scene(animation_info=1, rig_info=0, proto=3)
        common = FakeCommon(object(), FakeViewScene(), scene)
        ui = FakeUi(scene)
        called = []

        with mock.patch.object(rigging, "_common", return_value=common):
            with mock.patch.object(rigging, "_ui", return_value=ui):
                with mock.patch.object(rigging, "_rigging_tool", lambda *_args, **_kwargs: called.append(True)):
                    result = rigging.quick_rig()
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "existing_rig")
        self.assertEqual(result["proto"], 3)
        self.assertEqual(called, [])

    def test_replace_true_rebuilds_existing_proto_and_reports_applied(self):
        scene = self.fake_scene(animation_info=1, rig_info=0, proto=3)
        common = FakeCommon(object(), FakeViewScene(), scene)
        ui = FakeUi(scene)
        tool = FakeTool(scene, create_adds=2, on_create=lambda: ui.dialog_list.append(generate_dialog()))
        ui.on_click = lambda ref: scene.counts.update({"AnimationInfo": 0, "RigInfo": 1}) if ref == "generate" else None
        with mock.patch.object(rigging, "_common", return_value=common):
            with mock.patch.object(rigging, "_ui", return_value=ui):
                with mock.patch.object(rigging, "_rigging_tool", return_value=tool):
                    with mock.patch.object(rigging, "_character_summary", return_value={"name": "Rig(0)"}):
                        with mock.patch.object(rigging, "_controller_summary", return_value={"total": 1}):
                            result = rigging.quick_rig(template="Cascy", replace=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["replace"], result)
        self.assertEqual(result["existing"], {"rig_info": 0, "proto": 3})
        self.assertTrue(result["template_applied"], result)
        self.assertIn(("create", os.path.join(self.tmp.name, "Cascy.qrigcasc").replace("\\", "/")), tool.calls)

    def test_replace_true_rebuilds_existing_rig_info(self):
        scene = self.fake_scene(animation_info=0, rig_info=1)
        common = FakeCommon(object(), FakeViewScene(), scene)
        self.patch_enter_rig_mode(scene)
        ui = FakeUi(scene)
        tool = FakeTool(scene, create_adds=2, on_create=lambda: ui.dialog_list.append(generate_dialog()))
        ui.on_click = lambda ref: scene.counts.update({"AnimationInfo": 0, "RigInfo": 1}) if ref == "generate" else None
        with mock.patch.object(rigging, "_common", return_value=common):
            with mock.patch.object(rigging, "_ui", return_value=ui):
                with mock.patch.object(rigging, "_rigging_tool", return_value=tool):
                    with mock.patch.object(rigging, "_character_summary", return_value={"name": "Rig(0)"}):
                        with mock.patch.object(rigging, "_controller_summary", return_value={"total": 1}):
                            result = rigging.quick_rig(template="Cascy", replace=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["existing"], {"rig_info": 1, "proto": 0})
        self.assertTrue(result["template_applied"], result)

    def test_fails_if_still_in_rig_mode_after_generate(self):
        scene = self.fake_scene(animation_info=1, rig_info=0)
        common = FakeCommon(object(), FakeViewScene(), scene)
        ui = FakeUi(scene)
        tool = FakeTool(scene, create_adds=2, on_create=lambda: ui.dialog_list.append(generate_dialog()))
        ui.on_click = lambda ref: scene.counts.update({"RigInfo": 1}) if ref == "generate" else None
        with mock.patch.object(rigging, "_common", return_value=common):
            with mock.patch.object(rigging, "_ui", return_value=ui):
                with mock.patch.object(rigging, "_rigging_tool", return_value=tool):
                    result = rigging.quick_rig(template="Cascy")
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["stage"], "still_in_rig_mode")
        self.assertEqual(result["animation_info"], 1)
        self.assertIn("still has 1 AnimationInfo", result["error"])

    def test_fails_if_selected_template_was_not_applied(self):
        scene = self.fake_scene(animation_info=1, rig_info=0)
        common = FakeCommon(object(), FakeViewScene(), scene)
        tool = FakeTool(scene)  # create adds nothing
        ui = FakeUi(scene)

        def generate():
            tool.calls.append(("generate",))
            ui.dialog_list.append(generate_dialog())

        tool.generate_rig_elements = generate
        ui.on_click = lambda ref: scene.counts.update({"AnimationInfo": 0, "RigInfo": 1}) if ref == "generate" else None
        events = []

        def close(*_args, **_kwargs):
            events.append("close")
            return []

        def dismiss(*_args, **_kwargs):
            events.append("dismiss")
            return []

        with mock.patch.object(rigging, "_common", return_value=common):
            with mock.patch.object(rigging, "_ui", return_value=ui):
                with mock.patch.object(rigging, "_rigging_tool", return_value=tool):
                    with mock.patch.object(rigging, "_close_quick_rigging_window", close):
                        with mock.patch.object(rigging, "_dismiss_leftover_dialogs", dismiss):
                            result = rigging.quick_rig(template="Cascy")
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["stage"], "verify_template")
        self.assertIn("did not apply the selected template", result["error"])
        self.assertEqual(events[:2], ["close", "dismiss"], "close the tool before walking dialogs")

    def test_quick_rig_tool_metadata(self):
        tools = {tool["name"]: tool for tool in rigging.TOOLS}
        self.assertEqual(set(tools), {"list_rig_templates", "quick_rig"})
        self.assertTrue(tools["quick_rig"]["idle"])
        self.assertTrue(tools["quick_rig"]["timeout_arg"])
        self.assertIn("exactly", tools["quick_rig"]["description"])
        self.assertIn("AnimationInfo", tools["quick_rig"]["description"])


if __name__ == "__main__":
    unittest.main()
