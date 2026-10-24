"""Unit tests for the AutoPosing helpers and tools (T15); no Cascadeur needed.

cascadeur_mcp_plus/autoposing.py keeps its pure helpers free of Cascadeur imports, and
cascadeur_mcp_plus/tools/autoposing.py is loaded behind a throw-away package with a fake
common, dispatch and rig, so the prompt handling, idempotent switching and argument validation
are exercised without Cascadeur. The fake package never touches
sys.modules["cascadeur_mcp_plus"], so this file cannot disturb the other unit tests.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_DIR = os.path.join(ROOT, "cascadeur_mcp_plus")
TOOLS_DIR = os.path.join(PACKAGE_DIR, "tools")
PKG = "t15_autoposing_test_pkg"
TOOL_NAMES = ["get_autoposing_state", "autoposing_enable", "autoposing_update", "lock_controllers"]


class FakeObjectId:
    """Stand-in for csc.model.ObjectId in the real set_lock tests."""

    def __init__(self, name):
        self.name = name

    def __str__(self):
        return self.name

    def __eq__(self, other):
        return isinstance(other, FakeObjectId) and other.name == self.name

    def __hash__(self):
        return hash(self.name)


# ---------------------------------------------------------------------------
# Module loading with fakes
# ---------------------------------------------------------------------------


def _load():
    if PKG in sys.modules:
        return sys.modules[f"{PKG}.autoposing"], sys.modules[f"{PKG}.tools.autoposing"]

    package = types.ModuleType(PKG)
    package.__path__ = []
    sys.modules[PKG] = package

    errors = types.ModuleType(f"{PKG}.errors")

    class ToolError(Exception):
        """Same shape as cascadeur_mcp_plus.errors.ToolError."""

    errors.ToolError = ToolError
    sys.modules[f"{PKG}.errors"] = errors
    package.errors = errors

    common = types.ModuleType(f"{PKG}.common")

    def ok(**data):
        return {"ok": True, **data}

    def fail(error, hint=None, **data):
        result = {"ok": False, "error": str(error)}
        if hint:
            result["hint"] = str(hint)
        result.update(data)
        return result

    common.ok = ok
    common.fail = fail
    common.jsonable = lambda value: value
    sys.modules[f"{PKG}.common"] = common
    package.common = common

    dispatch = types.ModuleType(f"{PKG}.dispatch")
    calls = []

    def call_on_gui(fn, args, timeout, idle=False):
        calls.append((fn, dict(args)))
        return fn(**args)

    dispatch.call_on_gui = call_on_gui
    dispatch.calls = calls
    sys.modules[f"{PKG}.dispatch"] = dispatch
    package.dispatch = dispatch

    rig = types.ModuleType(f"{PKG}.rig")

    class RigError(Exception):
        def __init__(self, message, hint=None, candidates=None, **extra):
            super().__init__(message)
            self.hint = hint
            self.candidates = list(candidates or [])
            self.extra = extra

        def result(self):
            data = {"ok": False, "error": str(self)}
            if self.hint:
                data["hint"] = self.hint
            if self.candidates:
                data["candidates"] = self.candidates
            data.update(self.extra)
            return data

    class UnknownName(RigError):
        pass

    rig.RigError = RigError
    rig.UnknownName = UnknownName
    rig.resolve = lambda names, character=None: []
    sys.modules[f"{PKG}.rig"] = rig
    package.rig = rig

    autoposing_spec = importlib.util.spec_from_file_location(
        f"{PKG}.autoposing", os.path.join(PACKAGE_DIR, "autoposing.py")
    )
    autoposing = importlib.util.module_from_spec(autoposing_spec)
    sys.modules[f"{PKG}.autoposing"] = autoposing
    package.autoposing = autoposing
    autoposing_spec.loader.exec_module(autoposing)

    tools_package = types.ModuleType(f"{PKG}.tools")
    tools_package.__path__ = [TOOLS_DIR]
    sys.modules[f"{PKG}.tools"] = tools_package
    package.tools = tools_package

    schema_spec = importlib.util.spec_from_file_location(f"{PKG}.tools._schema", os.path.join(TOOLS_DIR, "_schema.py"))
    schema_module = importlib.util.module_from_spec(schema_spec)
    sys.modules[f"{PKG}.tools._schema"] = schema_module
    tools_package._schema = schema_module
    schema_spec.loader.exec_module(schema_module)

    tools_spec = importlib.util.spec_from_file_location(
        f"{PKG}.tools.autoposing", os.path.join(TOOLS_DIR, "autoposing.py")
    )
    tools_module = importlib.util.module_from_spec(tools_spec)
    sys.modules[f"{PKG}.tools.autoposing"] = tools_module
    tools_package.autoposing = tools_module
    tools_spec.loader.exec_module(tools_module)
    return autoposing, tools_module


autoposing, tools = _load()
ToolError = sys.modules[f"{PKG}.errors"].ToolError
FakeDispatch = sys.modules[f"{PKG}.dispatch"]

PROMPT = {
    "kind": "dialog",
    "type": "MessageDialog_QMLTYPE_12",
    "texts": [
        "Pose in the current frame is different from the AutoPosing prediction.",
        "Adjust lock state?",
    ],
    "buttons": [{"text": "Yes"}, {"text": "No"}, {"text": "Cancel"}],
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class AnswerArgumentTests(unittest.TestCase):
    def test_default_is_yes(self):
        self.assertEqual(autoposing.answer_argument(None), "yes")
        self.assertEqual(autoposing.answer_argument(""), "yes")

    def test_choices_are_case_and_space_insensitive(self):
        self.assertEqual(autoposing.answer_argument(" YES "), "yes")
        self.assertEqual(autoposing.answer_argument("No"), "no")
        self.assertEqual(autoposing.answer_argument("cancel"), "cancel")

    def test_invalid_answer_lists_the_choices(self):
        with self.assertRaises(ToolError) as caught:
            autoposing.answer_argument("maybe")
        self.assertIn("yes, no, cancel", str(caught.exception))


class LabelAndButtonTests(unittest.TestCase):
    def test_normalize_label_drops_ellipsis_mnemonic_and_spacing(self):
        self.assertEqual(autoposing.normalize_label("&Yes"), "yes")
        self.assertEqual(autoposing.normalize_label("Autolock\u2026"), "autolock")
        self.assertEqual(autoposing.normalize_label("  Don't  change. "), "don't change")
        self.assertEqual(autoposing.normalize_label(None), "")

    def test_yes_prefers_an_exact_yes_over_a_longer_label(self):
        buttons = [{"text": "Yes, always"}, {"text": "No"}, {"text": "Yes"}]
        self.assertEqual(autoposing.choose_button(buttons, "yes")["text"], "Yes")

    def test_yes_falls_back_to_autolock(self):
        buttons = [{"text": "Autolock"}, {"text": "Cancel"}]
        self.assertEqual(autoposing.choose_button(buttons, "yes")["text"], "Autolock")

    def test_no_and_cancel(self):
        buttons = [{"text": "Yes"}, {"text": "No"}, {"text": "Cancel"}]
        self.assertEqual(autoposing.choose_button(buttons, "no")["text"], "No")
        self.assertEqual(autoposing.choose_button(buttons, "cancel")["text"], "Cancel")

    def test_no_accepts_keep_labels(self):
        buttons = [{"text": "Keep current locks"}, {"text": "Change locks"}]
        self.assertEqual(autoposing.choose_button(buttons, "no")["text"], "Keep current locks")

    def test_no_matching_button(self):
        self.assertIsNone(autoposing.choose_button([{"text": "Whatever"}], "yes"))
        self.assertIsNone(autoposing.choose_button([], "yes"))
        self.assertIsNone(autoposing.choose_button([{"text": ""}], "yes"))

    def test_invalid_answer_raises(self):
        with self.assertRaises(ToolError):
            autoposing.choose_button([{"text": "Yes"}], "sometimes")


class LockDialogTests(unittest.TestCase):
    def test_autoposing_lock_prompt_is_recognised(self):
        self.assertTrue(autoposing.is_lock_dialog(PROMPT))
        self.assertTrue(
            autoposing.is_lock_dialog(
                {
                    "kind": "dialog",
                    "type": "MessageDialog",
                    "texts": ["Adjust AutoPosing lock state?"],
                    "buttons": [{"text": "Autolock"}, {"text": "Cancel"}],
                }
            )
        )

    def test_tooltip_popup_is_not_the_prompt(self):
        # The AutoPosing toolbar button's tooltip is "Adjust AutoPosing lock state", and Qt
        # shows tooltips as popups, so the same text without buttons must not count.
        tooltip = {
            "kind": "dialog",
            "type": "QQuickPopupItem",
            "texts": ["Adjust AutoPosing lock state"],
            "buttons": [],
        }
        self.assertFalse(autoposing.is_lock_dialog(tooltip))
        self.assertFalse(
            autoposing.is_lock_dialog({**tooltip, "type": "ToolTip_QMLTYPE_3", "buttons": [{"text": "Yes"}]})
        )

    def test_menus_and_windows_are_not_the_prompt(self):
        self.assertFalse(autoposing.is_lock_dialog({**PROMPT, "type": "Menu_QMLTYPE_9"}))
        self.assertFalse(autoposing.is_lock_dialog({**PROMPT, "kind": "window"}))
        self.assertFalse(autoposing.is_lock_dialog({**PROMPT, "type": None}))
        self.assertFalse(autoposing.is_lock_dialog({**PROMPT, "type": "ToolTip"}))

    def test_prompt_needs_an_answer_button(self):
        self.assertFalse(autoposing.is_lock_dialog({**PROMPT, "buttons": []}))
        self.assertFalse(autoposing.is_lock_dialog({**PROMPT, "buttons": [{"text": "Learn more"}]}))

    def test_other_dialogs_are_ignored(self):
        self.assertFalse(
            autoposing.is_lock_dialog(
                {
                    "kind": "dialog",
                    "type": "MessageDialog",
                    "texts": ["Do you want to save changes?"],
                    "buttons": [{"text": "Don't Save"}, {"text": "Save"}],
                }
            )
        )
        self.assertFalse(
            autoposing.is_lock_dialog(
                {"kind": "dialog", "type": "MessageDialog", "texts": ["AutoPosing panel"], "buttons": [{"text": "OK"}]}
            )
        )
        self.assertFalse(autoposing.is_lock_dialog(None))
        self.assertFalse(autoposing.is_lock_dialog("Adjust AutoPosing lock state?"))

    def test_find_lock_dialog_skips_unrelated_dialogs(self):
        unrelated = {
            "kind": "dialog",
            "type": "MessageDialog",
            "texts": ["Closing scene"],
            "buttons": [{"text": "Don't Save"}],
        }
        self.assertIs(autoposing.find_lock_dialog([unrelated, PROMPT]), PROMPT)
        self.assertIsNone(autoposing.find_lock_dialog([unrelated]))
        self.assertIsNone(autoposing.find_lock_dialog(None))


class AnswerLockDialogTests(unittest.TestCase):
    def setUp(self):
        self.real = autoposing.lock_dialog

    def tearDown(self):
        autoposing.lock_dialog = self.real

    def test_unrecognised_button_keeps_the_dialog_open(self):
        found = {
            "source": "dialogs",
            "texts": ["Adjust AutoPosing lock state?"],
            "buttons": [{"text": "Got it", "ref": "d0:0"}],
            "dialog": PROMPT,
        }
        autoposing.lock_dialog = lambda: found
        result = autoposing.answer_lock_dialog("yes")
        self.assertFalse(result["answered"], result)
        self.assertFalse(result["closed"], result)
        self.assertIn("no button", result["error"])

    def test_missing_dialog_counts_as_closed(self):
        autoposing.lock_dialog = lambda: None
        result = autoposing.answer_lock_dialog("yes")
        self.assertFalse(result["found"])
        self.assertTrue(result["closed"])


class MergeEnabledTests(unittest.TestCase):
    def test_no_readings_is_unknown(self):
        self.assertIsNone(autoposing.merge_enabled([]))
        self.assertIsNone(autoposing.merge_enabled([{"property": "active", "value": None}]))

    def test_active_wins_over_checked(self):
        sources = [
            {"property": "active", "value": False, "visible": True},
            {"property": "checked", "value": True, "visible": True},
        ]
        self.assertIs(autoposing.merge_enabled(sources), False)

    def test_visible_copy_wins(self):
        sources = [
            {"property": "active", "value": True, "visible": False},
            {"property": "active", "value": False, "visible": True},
        ]
        self.assertIs(autoposing.merge_enabled(sources), False)

    def test_any_true_reading_is_on(self):
        sources = [
            {"property": "active", "value": False, "visible": True},
            {"property": "active", "value": True, "visible": True},
        ]
        self.assertIs(autoposing.merge_enabled(sources), True)

    def test_checked_only_falls_back(self):
        self.assertIs(autoposing.merge_enabled([{"property": "checked", "value": True}]), True)


# ---------------------------------------------------------------------------
# Tools with fake Cascadeur calls
# ---------------------------------------------------------------------------


class ToolsTestCase(unittest.TestCase):
    def setUp(self):
        self.real = {
            name: getattr(autoposing, name)
            for name in ("state", "set_enabled", "run_update", "set_lock", "finish_dialogs", "layer_block")
        }
        FakeDispatch.calls.clear()
        self.states = []
        self.settings = []
        self.updates = []
        self.locks = []
        self.dialogs = []

    def tearDown(self):
        for name, value in self.real.items():
            setattr(autoposing, name, value)
        FakeDispatch.calls.clear()

    def queue_state(self, *values):
        self.states.extend(values)

    def state_double(self):
        if len(self.states) > 1:
            return self.states.pop(0)
        if self.states:
            return self.states[0]
        return {"enabled": True, "known": True, "sources": [], "locked_controllers": None}

    def install(self):
        autoposing.state = self.state_double

        def set_enabled(enabled):
            self.settings.append(enabled)
            return {"before": not enabled, "after": enabled, "changed": True, "action_called": "toggle"}

        autoposing.set_enabled = set_enabled

        def run_update(scene=None, frame=None, session=None, name="autoposing_update"):
            self.updates.append(name)
            return {"frame": 10, "undo": "MCP: autoposing_update", "method": "editor"}

        autoposing.run_update = run_update

        def set_lock(names, locked=True):
            self.locks.append((list(names), locked))
            return {
                "ok": True,
                "controllers": [{"name": name, "id": str(index)} for index, name in enumerate(names)],
                "action": "AutoPosingTool.SwitchLock" if locked else "AutoPosingTool.AutoUnlock",
                "locked": locked,
                "undoable": False,
            }

        autoposing.set_lock = set_lock

        def finish_dialogs(answer="yes", timeout=2.0, interval=0.15):
            self.dialogs.append(answer)
            return {"found": False, "answer": answer, "closed": True}

        autoposing.finish_dialogs = finish_dialogs
        autoposing.layer_block = lambda: None


class GetStateTests(ToolsTestCase):
    def test_reports_the_interface_state(self):
        self.queue_state(
            {
                "enabled": True,
                "known": True,
                "action": autoposing.TOGGLE_ACTION,
                "frame": 4,
                "sources": [{"property": "active", "value": True, "visible": True}],
                "locked_controllers": None,
                "locked_known": False,
                "note": autoposing.LOCKED_NOTE,
            }
        )
        self.install()
        result = tools.get_autoposing_state()
        self.assertTrue(result["ok"], result)
        self.assertIs(result["enabled"], True)
        self.assertEqual(result["frame"], 4)
        self.assertIsNone(result["locked_controllers"])
        self.assertFalse(result["locked_known"])
        self.assertIn("no Python query", result["note"])

    def test_unknown_switch_is_a_failure_with_a_hint(self):
        self.queue_state({"enabled": None, "known": False, "sources": []})
        self.install()
        result = tools.get_autoposing_state()
        self.assertFalse(result["ok"])
        self.assertIn("Could not read whether AutoPosing is on", result["error"])
        self.assertIn("ui_tree", result["hint"])

    def test_read_error_is_reported(self):
        def boom():
            raise RuntimeError("no scene")

        autoposing.state = boom
        result = tools.get_autoposing_state()
        self.assertFalse(result["ok"])
        self.assertIn("no scene", result["error"])
        self.assertIn("traceback", result)


class EnableTests(ToolsTestCase):
    def test_already_on_does_not_toggle(self):
        self.queue_state({"enabled": True, "known": True, "sources": []})
        self.install()
        result = tools.autoposing_enable(True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["enabled"], True)
        self.assertFalse(result["changed"])
        self.assertIsNone(result["action_called"])
        self.assertEqual(self.settings, [])
        self.assertEqual(len(FakeDispatch.calls), 1)  # only the state read

    def test_switches_on_and_answers_the_prompt(self):
        self.queue_state(
            {"enabled": False, "known": True, "sources": []}, {"enabled": True, "known": True, "sources": []}
        )
        self.install()

        def finish_dialogs(answer="yes", timeout=2.0, interval=0.15):
            self.dialogs.append(answer)
            return {"found": True, "answer": answer, "button": "Yes", "closed": True}

        autoposing.finish_dialogs = finish_dialogs
        result = tools.autoposing_enable(True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.settings, [True])
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["enabled"], True)
        self.assertTrue(result["changed"])
        self.assertEqual(result["dialog"]["button"], "Yes")

    def test_already_off_stays_off(self):
        self.queue_state({"enabled": False, "known": True, "sources": []})
        self.install()
        result = tools.autoposing_enable(False)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["changed"])
        self.assertEqual(self.settings, [])

    def test_invalid_arguments_are_rejected_before_touching_cascadeur(self):
        self.install()
        for arguments in ({"enabled": "maybe"}, {"enabled": True, "answer": "perhaps"}):
            result = tools.autoposing_enable(**arguments)
            self.assertFalse(result["ok"])
            self.assertIn("must be", result["error"] + result.get("hint", ""))
        self.assertEqual(FakeDispatch.calls, [])

    def test_retries_once_when_the_switch_did_not_move(self):
        self.queue_state(
            {"enabled": False, "known": True, "sources": []},
            {"enabled": False, "known": True, "sources": []},
            {"enabled": False, "known": True, "sources": []},
            {"enabled": False, "known": True, "sources": []},
        )
        self.install()
        result = tools.autoposing_enable(True)
        self.assertFalse(result["ok"])
        self.assertEqual(self.settings, [True, True])
        self.assertEqual(result["attempts"], 2)
        self.assertIn("still reads", result["error"])

    def test_stuck_prompt_is_a_failure(self):
        self.queue_state(
            {"enabled": False, "known": True, "sources": []}, {"enabled": True, "known": True, "sources": []}
        )
        self.install()

        def finish_dialogs(answer="yes", timeout=2.0, interval=0.15):
            return {"found": True, "answer": answer, "closed": False, "buttons": ["Yes"]}

        autoposing.finish_dialogs = finish_dialogs
        result = tools.autoposing_enable(True)
        self.assertFalse(result["ok"])
        self.assertIn("did not close", result["hint"])


class UpdateTests(ToolsTestCase):
    def test_update_returns_the_undo_step_and_reports_the_prompt(self):
        self.install()

        def finish_dialogs(answer="yes", timeout=2.0, interval=0.15):
            return {"found": True, "answer": answer, "button": "Autolock", "closed": True}

        autoposing.finish_dialogs = finish_dialogs
        result = tools.autoposing_update()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame"], 10)
        self.assertEqual(result["undo"], "MCP: autoposing_update")
        self.assertEqual(self.updates, ["autoposing_update"])
        self.assertIn("Autolock", result["hint"])

    def test_update_without_prompt_has_no_dialog(self):
        self.install()
        result = tools.autoposing_update()
        self.assertTrue(result["ok"], result)
        self.assertIsNone(result["dialog"])
        self.assertNotIn("hint", result)

    def test_update_when_autoposing_is_off_adds_a_hint(self):
        self.queue_state({"enabled": False, "known": True, "sources": []})
        self.install()
        result = tools.autoposing_update()
        self.assertTrue(result["ok"], result)
        self.assertIs(result["enabled"], False)
        self.assertIn("AutoPosing is off", result["hint"])

    def test_locked_layers_are_refused_before_the_update(self):
        self.install()
        autoposing.layer_block = lambda: {
            "ok": False,
            "error": "Refusing to change objects on locked layer(s): 'Hand_L' holds hand_MainPoint_l.",
            "hint": "Unlock the layer first with set_layer_locked(name=<layer>, locked=false).",
        }
        result = tools.autoposing_update()
        self.assertFalse(result["ok"])
        self.assertIn("locked layer", result["error"])
        self.assertEqual(self.updates, [])
        # _clear_prompt still answers a prompt that was already open, but no update runs.
        self.assertEqual(self.dialogs, ["yes"])

    def test_stuck_prompt_fails_after_the_update(self):
        self.install()

        def finish_dialogs(answer="yes", timeout=2.0, interval=0.15):
            return {"found": True, "answer": answer, "closed": False, "error": "no button"}

        autoposing.finish_dialogs = finish_dialogs
        result = tools.autoposing_update("no")
        self.assertFalse(result["ok"])
        self.assertEqual(result["frame"], 10)
        self.assertIn("still open", result["error"])
        self.assertEqual(self.dialogs, [])

    def test_failed_update_is_reported(self):
        def run_update(scene=None, frame=None, session=None, name="autoposing_update"):
            raise RuntimeError("Cascadeur rejected the update")

        autoposing.run_update = run_update
        self.install()
        autoposing.run_update = run_update
        result = tools.autoposing_update()
        self.assertFalse(result["ok"])
        self.assertIn("Cascadeur rejected the update", result["error"])
        self.assertIn("traceback", result)


class LockTests(ToolsTestCase):
    def test_lock_lists_controllers_and_has_no_readback(self):
        self.install()
        result = tools.lock_controllers(["left_foot"], locked=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.locks, [(["left_foot"], True)])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["action"], "AutoPosingTool.SwitchLock")
        self.assertIsNone(result["readback"])
        self.assertTrue(result["selection_restored"])
        self.assertIn("no Python query", result["note"])

    def test_unlock_uses_the_unlock_action(self):
        self.install()
        result = tools.lock_controllers(["left_foot"], locked=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["action"], "AutoPosingTool.AutoUnlock")
        self.assertEqual(self.locks, [(["left_foot"], False)])

    def test_rig_errors_are_passed_through(self):
        def set_lock(names, locked=True):
            return {"ok": False, "error": "Object or alias not found", "hint": "list_controllers", "candidates": []}

        autoposing.set_lock = set_lock
        self.install()
        autoposing.set_lock = set_lock
        result = tools.lock_controllers(["not_a_bone"])
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"])

    def test_invalid_arguments_are_rejected(self):
        self.install()
        result = tools.lock_controllers(["left_foot"], locked="maybe")
        self.assertFalse(result["ok"])
        self.assertIn("must be true or false", result["error"])
        self.assertEqual(self.locks, [])


class RealSetLockTests(unittest.TestCase):
    """The real set_lock uses Cascadeur's Autolock command; only the GUI calls are faked."""

    def setUp(self):
        self.rig = sys.modules[f"{PKG}.rig"]
        self.common = sys.modules[f"{PKG}.common"]
        self.real = {
            "autolock": autoposing.autolock,
            "scene": autoposing._scene,
            "resolve": self.rig.resolve,
        }
        self.objects = {
            FakeObjectId("hand"): "hand_MainPoint_l",
            FakeObjectId("foot"): "foot_MainPoint_l",
        }

        objects = self.objects

        class Viewer:
            def get_object_name(self, object_id):
                return objects[object_id]

        class Scene:
            def model_viewer(self):
                return Viewer()

        autoposing._scene = lambda: Scene()
        autoposing.autolock = lambda ids, frame=None: {
            "action": autoposing.AUTOLOCK_ACTION,
            "undo": "AutoPosing.Autolock",
        }
        self.rig.resolve = lambda names, character=None: [FakeObjectId("hand")]

    def tearDown(self):
        autoposing.autolock = self.real["autolock"]
        autoposing._scene = self.real["scene"]
        self.rig.resolve = self.real["resolve"]

    def test_lock_runs_the_autolock_command(self):
        result = autoposing.set_lock(["left_hand"], True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["action"], autoposing.AUTOLOCK_ACTION)
        self.assertEqual(result["undo"], "AutoPosing.Autolock")
        self.assertTrue(result["undoable"])
        self.assertEqual(result["controllers"], [{"name": "hand_MainPoint_l", "id": "hand"}])
        self.assertIn("current frame", result["scope"])

    def test_unlock_is_reported_as_unavailable(self):
        result = autoposing.set_lock(["left_foot"], False)
        self.assertFalse(result["ok"])
        self.assertIn("no unlock action", result["error"])
        self.assertIn("SwitchLock", result["hint"])

    def test_unknown_names_pass_the_rig_error_through(self):
        def unknown(names, character=None):
            raise self.rig.UnknownName("Object or alias not found: 'not_a_bone'", hint="list_controllers")

        self.rig.resolve = unknown
        result = autoposing.set_lock(["not_a_bone"], True)
        self.assertFalse(result["ok"])
        self.assertIn("not_a_bone", result["error"])

    def test_empty_resolution_is_a_failure(self):
        self.rig.resolve = lambda names, character=None: []
        result = autoposing.set_lock(["left_hand"], True)
        self.assertFalse(result["ok"])
        self.assertIn("No controllers matched", result["error"])


class LayerGuardTests(unittest.TestCase):
    """run_update/solve refuse locked timeline layers before touching the scene (T15-fix)."""

    def setUp(self):
        self.real = {
            "layer_block": autoposing.layer_block,
            "character_ids": autoposing._character_object_ids,
            "ensure_unlocked": autoposing._ensure_unlocked,
            "tool_editor": autoposing.tool_editor,
        }

    def tearDown(self):
        autoposing.layer_block = self.real["layer_block"]
        autoposing._character_object_ids = self.real["character_ids"]
        autoposing._ensure_unlocked = self.real["ensure_unlocked"]
        autoposing.tool_editor = self.real["tool_editor"]

    def test_layer_block_checks_every_character_control_point(self):
        ids = [FakeObjectId("hand"), FakeObjectId("foot")]
        seen = []
        autoposing._character_object_ids = lambda: ids
        autoposing._ensure_unlocked = lambda object_ids: seen.append(list(object_ids))
        self.assertIsNone(autoposing.layer_block())
        self.assertEqual(seen, [ids])

    def test_layer_block_returns_the_guard_failure(self):
        failure = {
            "ok": False,
            "error": "Refusing to change objects on locked layer(s): 'Hand_L' holds hand_MainPoint_l.",
            "hint": "Unlock",
        }
        autoposing._character_object_ids = lambda: [FakeObjectId("hand")]
        autoposing._ensure_unlocked = lambda object_ids: failure
        self.assertIs(autoposing.layer_block(), failure)

    def test_layer_block_without_control_points_is_unlocked(self):
        autoposing._character_object_ids = lambda: []

        def guard(object_ids):
            raise AssertionError("the guard must not run without control points")

        autoposing._ensure_unlocked = guard
        self.assertIsNone(autoposing.layer_block())

    def test_layer_block_fails_closed_when_the_scope_cannot_be_read(self):
        def broken():
            raise RuntimeError("no scene")

        autoposing._character_object_ids = broken
        result = autoposing.layer_block()
        self.assertFalse(result["ok"])
        self.assertIn("Could not list the character's control points", result["error"])

    def test_run_update_refuses_locked_layers_before_the_scene(self):
        autoposing.layer_block = lambda: {
            "ok": False,
            "error": "Refusing to change objects on locked layer(s): 'Hand_L' holds hand_MainPoint_l.",
            "hint": "Unlock the layer first.",
        }

        def editor():
            raise AssertionError("no editor when a layer is locked")

        autoposing.tool_editor = editor
        with self.assertRaises(ToolError) as caught:
            autoposing.run_update()
        self.assertIn("locked layer", str(caught.exception))
        self.assertIn("Unlock the layer first", str(caught.exception))

    def test_solve_refuses_locked_layers(self):
        autoposing.layer_block = lambda: {
            "ok": False,
            "error": "Refusing to change objects on locked layer(s): 'Hand_L'.",
            "hint": "Unlock",
        }

        class Scene:
            def get_current_frame(self, clamp=True):
                raise AssertionError("solve must not touch the scene when a layer is locked")

        result = autoposing.solve(Scene(), 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["frame"], 0)
        self.assertIn("locked layer", result["error"])


class ToolDeclarationTests(unittest.TestCase):
    def test_tool_names_schemas_and_handlers(self):
        self.assertEqual([tool["name"] for tool in tools.TOOLS], TOOL_NAMES)
        by_name = {tool["name"]: tool for tool in tools.TOOLS}
        for name in TOOL_NAMES:
            with self.subTest(tool=name):
                tool = by_name[name]
                self.assertTrue(tool["description"])
                self.assertEqual(tool["inputSchema"]["type"], "object")
        self.assertIn("fn", by_name["get_autoposing_state"])
        for name in ("autoposing_enable", "autoposing_update", "lock_controllers"):
            self.assertIn("handler", by_name[name])
        self.assertEqual(by_name["lock_controllers"]["inputSchema"]["required"], ["names"])
        self.assertEqual(by_name["autoposing_enable"]["inputSchema"]["properties"]["enabled"]["type"], "boolean")
        self.assertEqual(
            by_name["autoposing_update"]["inputSchema"]["properties"]["answer"]["enum"], list(autoposing.ANSWER_CHOICES)
        )


if __name__ == "__main__":
    unittest.main()
