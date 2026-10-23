"""Unit tests for the selection tools (T13); no Cascadeur needed.

cascadeur_mcp_plus/tools/selection.py imports csc, common and the real rig.py. The tests load the
module behind a throw-away package with a stand-in csc module, a fake common that returns a fake
scene, and the real rig.py, so the selection arithmetic and the tool functions can be exercised
without Cascadeur. The fake package never touches sys.modules["cascadeur_mcp_plus"], so this file
cannot disturb the other unit tests.

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
PKG = "t13_selection_test_pkg"


# ---------------------------------------------------------------------------
# Fake objects and module loading
# ---------------------------------------------------------------------------


class ObjectKey:
    """Stand-in for csc.model.ObjectId: hashable, with a readable string form."""

    def __init__(self, name):
        self.name = name

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"ObjectKey({self.name!r})"

    def __eq__(self, other):
        return isinstance(other, ObjectKey) and other.name == self.name

    def __hash__(self):
        return hash(self.name)


class ToolKey:
    """Stand-in for csc.domain.Tool_object_id: no model name."""

    def __init__(self, name):
        self.name = name

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"ToolKey({self.name!r})"

    def __eq__(self, other):
        return isinstance(other, ToolKey) and other.name == self.name

    def __hash__(self):
        return hash(self.name)


class FakeModelViewer:
    def __init__(self, names, types):
        self.names = names
        self.types = types

    def get_object_name(self, object_id):
        if isinstance(object_id, ToolKey):
            raise TypeError("tool objects have no model name")
        return self.names[object_id]

    def get_object_type_name(self, object_id):
        if isinstance(object_id, ToolKey):
            raise TypeError("tool objects have no model type")
        return self.types.get(object_id, "")


class FakeSelection:
    def __init__(self, ids):
        self.ids = list(ids)
        self.ordered_ids = list(ids)


class FakeSelector:
    def __init__(self, ids=()):
        self.ids = list(ids)
        self.calls = []

    def selected(self):
        return FakeSelection(self.ids)

    def select(self, ids, pivot=None):
        self.calls.append((list(ids), pivot))
        self.ids = list(ids)


class FakeScene:
    def __init__(self, model_viewer, selector):
        self._model_viewer = model_viewer
        self._selector = selector

    def model_viewer(self):
        return self._model_viewer

    def selector(self):
        return self._selector


def _load():
    """Import selection.py and its helpers behind the throw-away package."""
    if PKG in sys.modules:
        return sys.modules[f"{PKG}.tools.selection"]

    package = types.ModuleType(PKG)
    package.__path__ = []
    sys.modules[PKG] = package

    tools_package = types.ModuleType(f"{PKG}.tools")
    tools_package.__path__ = [TOOLS_DIR]
    sys.modules[f"{PKG}.tools"] = tools_package
    package.tools = tools_package

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
    common.scene = None

    def scenes():
        return None, None, common.scene

    common.scenes = scenes
    sys.modules[f"{PKG}.common"] = common
    package.common = common

    rig_spec = importlib.util.spec_from_file_location(f"{PKG}.rig", os.path.join(PACKAGE_DIR, "rig.py"))
    rig = importlib.util.module_from_spec(rig_spec)
    sys.modules[f"{PKG}.rig"] = rig
    package.rig = rig
    rig_spec.loader.exec_module(rig)

    csc = types.ModuleType("csc")
    csc_model = types.ModuleType("csc.model")

    class ObjectId:
        @staticmethod
        def null():
            return ObjectKey("null")

    csc_model.ObjectId = ObjectId
    csc.model = csc_model

    previous_csc = sys.modules.get("csc")
    sys.modules["csc"] = csc
    try:
        spec = importlib.util.spec_from_file_location(f"{PKG}.tools.selection", os.path.join(TOOLS_DIR, "selection.py"))
        selection = importlib.util.module_from_spec(spec)
        sys.modules[f"{PKG}.tools.selection"] = selection
        spec.loader.exec_module(selection)
    finally:
        if previous_csc is None:
            sys.modules.pop("csc", None)
        else:
            sys.modules["csc"] = previous_csc
    return selection


selection = _load()


# ---------------------------------------------------------------------------
# Argument helpers and selection arithmetic
# ---------------------------------------------------------------------------


class ArgumentTests(unittest.TestCase):
    def test_names_accept_string_list_and_trim_duplicates(self):
        self.assertEqual(selection._names_argument("left_hand"), ["left_hand"])
        self.assertEqual(
            selection._names_argument([" left_hand ", "right_hand", "left_hand"]), ["left_hand", "right_hand"]
        )
        self.assertEqual(selection._names_argument(("pelvis", "")), ["pelvis"])

    def test_names_reject_missing_or_empty_values(self):
        for value in (None, [], "  ", 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                selection._names_argument(value)

    def test_modes_case_and_default(self):
        self.assertEqual(selection._mode_argument(None), "replace")
        self.assertEqual(selection._mode_argument("replace"), "replace")
        self.assertEqual(selection._mode_argument(" ADD "), "add")
        self.assertEqual(selection._mode_argument("remove"), "remove")

    def test_bad_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            selection._mode_argument("toggle")


class ApplyModeTests(unittest.TestCase):
    def setUp(self):
        self.left = ObjectKey("left")
        self.right = ObjectKey("right")
        self.head = ObjectKey("head")

    def test_replace_uses_only_the_requested_ids(self):
        result = selection._apply_mode([self.head], [self.left, self.right], "replace")
        self.assertEqual(result, [self.left, self.right])

    def test_replace_with_empty_ids_clears(self):
        self.assertEqual(selection._apply_mode([self.head], [], "replace"), [])

    def test_add_keeps_order_and_skips_duplicates(self):
        result = selection._apply_mode([self.left, self.head], [self.head, self.right, self.left], "add")
        self.assertEqual(result, [self.left, self.head, self.right])

    def test_remove_keeps_the_rest_in_order(self):
        result = selection._apply_mode([self.left, self.head, self.right], [self.head], "remove")
        self.assertEqual(result, [self.left, self.right])

    def test_remove_of_unselected_ids_changes_nothing(self):
        self.assertEqual(selection._apply_mode([self.left], [self.right], "remove"), [self.left])


# ---------------------------------------------------------------------------
# get_selection / select_objects / clear_selection with a fake scene
# ---------------------------------------------------------------------------


class SelectionToolTests(unittest.TestCase):
    def setUp(self):
        self.left = ObjectKey("hand_MainPoint_l")
        self.right = ObjectKey("hand_MainPoint_r")
        self.head = ObjectKey("head_MainPoint")
        self.tool = ToolKey("autoposing-point-1")
        self.names = {
            self.left: "hand_MainPoint_l",
            self.right: "hand_MainPoint_r",
            self.head: "head_MainPoint",
        }
        self.types = {self.left: "Point", self.right: "Point", self.head: "Point"}
        self.controllers = [
            {
                "name": "hand_MainPoint_l",
                "alias": "left_hand",
                "kind": "main_point",
                "type": "Point",
                "character": "Rig(0)",
            },
            {
                "name": "hand_MainPoint_r",
                "alias": "right_hand",
                "kind": "main_point",
                "type": "Point",
                "character": "Rig(0)",
            },
            {"name": "head_MainPoint", "alias": "head", "kind": "main_point", "type": "Point", "character": "Rig(0)"},
        ]
        for entry, object_id in zip(self.controllers, (self.left, self.right, self.head), strict=True):
            entry["id"] = object_id
        self.selector = FakeSelector()
        self.scene = FakeScene(FakeModelViewer(self.names, self.types), self.selector)
        selection.common.scene = self.scene
        self._real_controllers = selection.rig.controllers
        self._real_resolve = selection.rig.resolve
        selection.rig.controllers = lambda character=None: list(self.controllers)
        aliases = {"left_hand": self.left, "right_hand": self.right, "head": self.head}
        selection.rig.resolve = lambda names, character=None: [aliases[name] for name in names]

    def tearDown(self):
        selection.rig.controllers = self._real_controllers
        selection.rig.resolve = self._real_resolve

    def test_get_selection_lists_alias_kind_type_and_character(self):
        self.selector.ids = [self.left, self.right]
        result = selection.get_selection()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["names"], ["hand_MainPoint_l", "hand_MainPoint_r"])
        self.assertEqual([entry["alias"] for entry in result["selection"]], ["left_hand", "right_hand"])
        self.assertEqual({entry["kind"] for entry in result["selection"]}, {"main_point"})
        self.assertEqual({entry["type"] for entry in result["selection"]}, {"Point"})
        self.assertEqual({entry["character"] for entry in result["selection"]}, {"Rig(0)"})

    def test_get_selection_lists_tool_objects(self):
        self.selector.ids = [self.tool]
        result = selection.get_selection()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        entry = result["selection"][0]
        self.assertTrue(entry["tool_object"])
        self.assertEqual(entry["name"], "<ToolKey autoposing-point-1>")
        self.assertEqual(entry["type"], "ToolKey")
        self.assertIsNone(entry["alias"])

    def test_select_objects_replaces_the_selection(self):
        self.selector.ids = [self.head]
        result = selection.select_objects(["left_hand", "right_hand"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["mode"], "replace")
        self.assertEqual(result["requested"], ["left_hand", "right_hand"])
        self.assertEqual(sorted(result["names"]), ["hand_MainPoint_l", "hand_MainPoint_r"])
        self.assertEqual(len(self.selector.calls), 1)
        self.assertEqual(set(self.selector.calls[0][0]), {self.left, self.right})
        self.assertIn(self.selector.calls[0][1], (self.left, self.right))

    def test_select_objects_add_and_remove(self):
        self.selector.ids = [self.left]
        added = selection.select_objects(["right_hand"], mode="add")
        self.assertTrue(added["ok"], added)
        self.assertEqual(sorted(added["names"]), ["hand_MainPoint_l", "hand_MainPoint_r"])
        removed = selection.select_objects(["left_hand"], mode="remove")
        self.assertTrue(removed["ok"], removed)
        self.assertEqual(removed["names"], ["hand_MainPoint_r"])
        self.assertEqual(sorted(self.selector.ids, key=str), sorted([self.right], key=str))

    def test_clear_selection_empties_the_selection(self):
        self.selector.ids = [self.left, self.right]
        result = selection.clear_selection()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["count"], 0)
        self.assertEqual(self.selector.ids, [])
        self.assertEqual(self.selector.calls[0][1], ObjectKey("null"))

    def test_invalid_mode_does_not_touch_the_selection(self):
        self.selector.ids = [self.head]
        result = selection.select_objects(["left_hand"], mode="toggle")
        self.assertFalse(result["ok"])
        self.assertIn("mode must be one of", result["error"])
        self.assertEqual(self.selector.calls, [])
        self.assertEqual(self.selector.ids, [self.head])

    def test_rig_errors_are_passed_through(self):
        def ambiguous(names, character=None):
            raise selection.rig.AmbiguousName(
                "'hand' matches 2 control points",
                hint="Use one of the candidate names.",
                candidates=[{"name": "hand_MainPoint_l"}, {"name": "hand_MainPoint_r"}],
            )

        self.selector.ids = [self.head]
        selection.rig.resolve = ambiguous
        result = selection.select_objects(["hand"])
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(self.selector.calls, [])
        self.assertEqual(self.selector.ids, [self.head])


class ToolDeclarationTests(unittest.TestCase):
    def test_tool_dicts_and_schemas(self):
        self.assertEqual(
            [tool["name"] for tool in selection.TOOLS], ["select_objects", "get_selection", "clear_selection"]
        )
        for tool in selection.TOOLS:
            with self.subTest(tool=tool["name"]):
                self.assertTrue(tool["description"])
                self.assertIn("fn", tool)
                self.assertEqual(tool["inputSchema"]["type"], "object")

        select_schema = selection.TOOLS[0]["inputSchema"]
        self.assertEqual(select_schema["required"], ["names"])
        self.assertEqual(select_schema["properties"]["names"]["type"], "array")
        self.assertEqual(select_schema["properties"]["mode"]["enum"], ["replace", "add", "remove"])


if __name__ == "__main__":
    unittest.main()
