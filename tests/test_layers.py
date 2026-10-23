"""Tests for cascadeur_mcp_plus/tools/layers.py (no Cascadeur needed).

The module imports only the standard library at import time, so it is loaded straight from its
file. The tests cover the pure helpers that build and query the layer tree; the scene-facing
tools are covered by tests/live/test_layers.py, which needs a running Cascadeur.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_layers():
    spec = importlib.util.spec_from_file_location(
        "layers_under_test", os.path.join(ROOT, "cascadeur_mcp_plus", "tools", "layers.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


layers = load_layers()

ROOT_ID = "root-guid"
FOLDER_A = "folder-a"
FOLDER_B = "folder-b"
PELVIS = "layer-pelvis"
HAND_L = "layer-hand-l"
JAW = "layer-jaw"
ROOT_LAYER = "layer-root"


def sample_entries():
    """A two-folder tree with a duplicate name in different folders."""
    return [
        layers.make_entry(layers.FOLDER, FOLDER_A, "Rig", ROOT_ID, 0),
        layers.make_entry(layers.LAYER, PELVIS, "pelvis", FOLDER_A, 0, objects=1, visible=True, locked=False),
        layers.make_entry(layers.LAYER, HAND_L, "hand_l", FOLDER_A, 1, objects=1, visible=True, locked=False),
        layers.make_entry(layers.FOLDER, FOLDER_B, "Face", ROOT_ID, 1),
        layers.make_entry(layers.LAYER, JAW, "jaw", FOLDER_B, 0, objects=2, visible=False, locked=True),
        layers.make_entry(layers.LAYER, ROOT_LAYER, "root layer", ROOT_ID, 2),
    ]


class NameTests(unittest.TestCase):
    def test_valid_names_are_stripped(self):
        self.assertEqual(layers.validate_name("  Rig  "), "Rig")
        self.assertEqual(layers.validate_name("Layer 1", "new_name"), "Layer 1")

    def test_invalid_names_are_rejected(self):
        for value in ("", "   ", "a/b", None, 5, []):
            with self.subTest(value=value):
                with self.assertRaises(layers.LayerError):
                    layers.validate_name(value)

    def test_slash_explains_paths(self):
        with self.assertRaises(layers.LayerError) as caught:
            layers.validate_name("Rig/hand")
        self.assertIn("separates folders", str(caught.exception))

    def test_bool_argument(self):
        self.assertIs(layers.bool_arg(True, "flag"), True)
        self.assertIs(layers.bool_arg(False, "flag"), False)
        for value in ("true", 1, 0, None):
            with self.subTest(value=value):
                with self.assertRaises(layers.LayerError):
                    layers.bool_arg(value, "flag")


class EntryTests(unittest.TestCase):
    def test_make_entry_normalizes_and_copies(self):
        entry = layers.make_entry(layers.LAYER, 5, "Name", 7, order="2", objects="3", visible=1, locked=0)
        self.assertEqual(
            entry,
            {
                "kind": layers.LAYER,
                "id": "5",
                "name": "Name",
                "parent": "7",
                "order": 2,
                "objects": 3,
                "visible": True,
                "locked": False,
            },
        )
        self.assertIsNone(layers.make_entry(layers.FOLDER, "f", "F")["visible"])

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            layers.make_entry("track", "x", "X")


class PathTests(unittest.TestCase):
    def test_nested_paths_stop_at_the_root(self):
        paths = layers.entry_paths(sample_entries(), ROOT_ID)
        self.assertEqual(paths[FOLDER_A], "Rig")
        self.assertEqual(paths[HAND_L], "Rig/hand_l")
        self.assertEqual(paths[JAW], "Face/jaw")
        self.assertEqual(paths[ROOT_LAYER], "root layer")

    def test_unknown_parent_is_treated_as_top_level(self):
        entries = [layers.make_entry(layers.LAYER, "x", "orphan", "missing-folder", 0, objects=0)]
        self.assertEqual(layers.entry_paths(entries, ROOT_ID)["x"], "orphan")

    def test_cycle_does_not_loop_forever(self):
        entries = [
            layers.make_entry(layers.FOLDER, "a", "A", "b", 0),
            layers.make_entry(layers.FOLDER, "b", "B", "a", 0),
        ]
        paths = layers.entry_paths(entries, ROOT_ID)
        self.assertEqual(set(paths), {"a", "b"})


class TreeTests(unittest.TestCase):
    def test_tree_nests_and_orders_like_the_parents(self):
        tree = layers.build_tree(sample_entries(), ROOT_ID)
        self.assertEqual([node["name"] for node in tree], ["Rig", "Face", "root layer"])
        rig = tree[0]
        self.assertEqual(rig["kind"], layers.FOLDER)
        self.assertEqual(rig["path"], "Rig")
        self.assertEqual([node["name"] for node in rig["children"]], ["pelvis", "hand_l"])
        self.assertEqual(rig["objects"], 2)
        self.assertEqual(rig["children"][1]["visible"], True)
        self.assertEqual(rig["children"][1]["locked"], False)
        face = tree[1]
        self.assertEqual(face["objects"], 2)
        self.assertEqual(face["children"][0]["visible"], False)
        self.assertEqual(face["children"][0]["locked"], True)
        self.assertEqual(tree[2]["objects"], 0)

    def test_folder_object_count_totals_the_subtree(self):
        entries = sample_entries() + [
            layers.make_entry(layers.FOLDER, "nested", "Nested", FOLDER_A, 2),
            layers.make_entry(layers.LAYER, "nested-layer", "fingers", "nested", 0, objects=3),
        ]
        tree = layers.build_tree(entries, ROOT_ID)
        rig = tree[0]
        nested = next(node for node in rig["children"] if node["name"] == "Nested")
        self.assertEqual(nested["objects"], 3)
        self.assertEqual(nested["children"][0]["path"], "Rig/Nested/fingers")
        self.assertEqual(rig["objects"], 5)

    def test_unknown_parent_entries_are_kept(self):
        entries = sample_entries() + [layers.make_entry(layers.LAYER, "lost", "lost layer", "gone", 0, objects=0)]
        tree = layers.build_tree(entries, ROOT_ID)
        names = [node["name"] for node in tree]
        self.assertIn("lost layer", names)

    def test_cycles_are_broken(self):
        entries = [
            layers.make_entry(layers.FOLDER, "a", "A", "b", 0),
            layers.make_entry(layers.FOLDER, "b", "B", "a", 0),
        ]
        tree = layers.build_tree(entries, ROOT_ID)

        ids = []

        def collect(nodes):
            for node in nodes:
                ids.append(node["id"])
                collect(node.get("children", []))

        collect(tree)
        self.assertEqual(sorted(ids), ["a", "b"])


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.entries = sample_entries()

    def test_exact_name(self):
        self.assertEqual(layers.find_entry(self.entries, "hand_l")["id"], HAND_L)
        self.assertEqual(layers.find_entry(self.entries, "Rig")["id"], FOLDER_A)

    def test_path_disambiguates_and_is_case_insensitive(self):
        entries = self.entries + [
            layers.make_entry(layers.LAYER, "hand-face", "hand_l", FOLDER_B, 1, objects=0),
        ]
        with self.assertRaises(layers.LayerError) as caught:
            layers.find_entry(entries, "hand_l")
        self.assertEqual(caught.exception.extra["matched"], 2)
        self.assertEqual(len(caught.exception.candidates), 2)
        self.assertEqual(layers.find_entry(entries, "face/HAND_L")["id"], "hand-face")
        self.assertEqual(layers.find_entry(entries, "rig/hand_l")["id"], HAND_L)

    def test_top_level_name_does_not_hide_a_nested_namesake(self):
        entries = self.entries + [layers.make_entry(layers.LAYER, "top-hand", "hand_l", ROOT_ID, 2)]
        with self.assertRaises(layers.LayerError) as caught:
            layers.find_entry(entries, "hand_l")
        self.assertEqual(caught.exception.extra["matched"], 2)
        self.assertEqual(layers.find_entry(entries, "top-hand")["id"], "top-hand")
        self.assertEqual(layers.find_entry(entries, "Rig/hand_l")["id"], HAND_L)

    def test_id(self):
        self.assertEqual(layers.find_entry(self.entries, PELVIS)["id"], PELVIS)
        self.assertEqual(layers.find_entry(self.entries, PELVIS.upper())["id"], PELVIS)

    def test_kind_filter(self):
        entry = layers.find_entry(self.entries, "Rig", kind=layers.FOLDER)
        self.assertEqual(entry["kind"], layers.FOLDER)
        with self.assertRaises(layers.LayerError):
            layers.find_entry(self.entries, "Rig", kind=layers.LAYER)

    def test_partial_name(self):
        self.assertEqual(layers.find_entry(self.entries, "pelv")["id"], PELVIS)
        with self.assertRaises(layers.LayerError) as caught:
            layers.find_entry(self.entries, "a")
        self.assertGreaterEqual(len(caught.exception.candidates), 2)

    def test_unknown_name_lists_closest(self):
        with self.assertRaises(layers.LayerError) as caught:
            layers.find_entry(self.entries, "pelvis_layer")
        message = str(caught.exception)
        self.assertIn("not found", message)
        self.assertIn("closest", message)
        self.assertTrue(caught.exception.hint)

    def test_empty_query_is_rejected(self):
        for value in ("", "  ", None):
            with self.subTest(value=value):
                with self.assertRaises(layers.LayerError):
                    layers.find_entry(self.entries, value)

    def test_error_result_shape(self):
        try:
            layers.find_entry(self.entries, "a")
        except layers.LayerError as exc:
            result = exc.result()
        else:  # pragma: no cover - the lookup must fail
            self.fail("find_entry did not raise")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)
        self.assertIn("hint", result)
        self.assertEqual(len(result["candidates"]), result["matched"] if "matched" in result else 2)
        for item in result["candidates"]:
            self.assertEqual(set(item), {"name", "path", "id", "kind"})


class SiblingTests(unittest.TestCase):
    def setUp(self):
        self.entries = sample_entries()

    def test_name_taken_is_case_insensitive_and_per_parent(self):
        self.assertIsNotNone(layers.name_taken(self.entries, FOLDER_A, "HAND_L"))
        self.assertIsNone(layers.name_taken(self.entries, FOLDER_B, "hand_l"))
        self.assertIsNotNone(layers.name_taken(self.entries, ROOT_ID, "rig"))
        self.assertIsNone(layers.name_taken(self.entries, ROOT_ID, "rig", exclude_id=FOLDER_A))

    def test_descendant_count_includes_a_layer_itself(self):
        self.assertEqual(layers.descendant_object_count(self.entries, PELVIS), 1)
        self.assertEqual(layers.descendant_object_count(self.entries, JAW), 2)
        self.assertEqual(layers.descendant_object_count(self.entries, FOLDER_A), 2)
        self.assertEqual(layers.descendant_object_count(self.entries, ROOT_ID), 4)

    def test_rescue_layer_prefers_the_default_layer_outside_the_doomed_set(self):
        entries = sample_entries()
        self.assertEqual(layers.rescue_layer(entries, {"x"}, "default"), "default")
        # The default is doomed, so the first layer outside the doomed set wins.
        self.assertEqual(layers.rescue_layer(entries, {PELVIS, HAND_L}, PELVIS), JAW)
        self.assertEqual(layers.rescue_layer(entries, {PELVIS, HAND_L, FOLDER_A}), JAW)
        self.assertEqual(layers.rescue_layer(entries, {PELVIS, HAND_L, FOLDER_A}, FOLDER_A), JAW)
        self.assertIsNone(layers.rescue_layer(entries, {PELVIS, HAND_L, JAW, ROOT_LAYER}))

    def test_delete_plan_is_bottom_up(self):
        folder_a = layers.find_entry(self.entries, "Rig")
        self.assertEqual(
            layers.delete_plan(self.entries, folder_a),
            [(layers.LAYER, PELVIS), (layers.LAYER, HAND_L), (layers.FOLDER, FOLDER_A)],
        )
        layer = layers.find_entry(self.entries, "jaw")
        self.assertEqual(layers.delete_plan(self.entries, layer), [(layers.LAYER, JAW)])


class LockedGroupsTests(unittest.TestCase):
    def setUp(self):
        self.entries = sample_entries()

    def test_objects_are_grouped_by_their_locked_layer(self):
        groups = layers.locked_groups(
            self.entries,
            {"one": JAW, "two": JAW, "three": PELVIS, "four": None},
            {"one": "jaw_MainPoint", "two": "head_MainPoint", "three": "pelvis_MainPoint", "four": "loose"},
        )
        self.assertEqual(len(groups), 1)
        entry, names = groups[0]
        self.assertEqual(entry["id"], JAW)
        self.assertEqual(names, ["jaw_MainPoint", "head_MainPoint"])

    def test_no_locked_layer_returns_nothing(self):
        self.assertEqual(layers.locked_groups(self.entries, {"a": PELVIS, "b": None}), [])
        self.assertEqual(layers.locked_groups(self.entries, {}), [])

    def test_unknown_layer_id_is_ignored(self):
        self.assertEqual(layers.locked_groups(self.entries, {"a": "missing-layer"}), [])


class EnsureUnlockedTests(unittest.TestCase):
    """ensure_unlocked with the scene-facing helpers replaced by a small fake."""

    def setUp(self):
        self.entries = sample_entries()
        self.object_layers = {"pelvis-obj": PELVIS, "jaw-obj": JAW, "loose-obj": None}
        self.object_names = {
            "pelvis-obj": "pelvis_MainPoint",
            "jaw-obj": "jaw_MainPoint",
            "loose-obj": "loose",
        }
        self.originals = {
            name: getattr(layers, name) for name in ("_common", "_scene", "read_entries", "_layer_of", "_object_name")
        }
        for name, value in self.originals.items():
            self.addCleanup(setattr, layers, name, value)
        self.fake_common = types.SimpleNamespace(fail=self._fail, jsonable=lambda value: value)
        layers._common = lambda: self.fake_common
        layers._scene = lambda: types.SimpleNamespace(layers_viewer=lambda: "viewer", model_viewer=lambda: "model")
        layers.read_entries = lambda scene=None: (self.entries, {}, ROOT_ID, "Scene")
        layers._layer_of = lambda viewer, object_id: self.object_layers.get(str(object_id))
        layers._object_name = lambda model_viewer, object_id: self.object_names.get(str(object_id), str(object_id))

    @staticmethod
    def _fail(error, hint=None, **data):
        result = {"ok": False, "error": error}
        if hint:
            result["hint"] = hint
        result.update(data)
        return result

    def test_unlocked_and_empty_inputs_return_none(self):
        self.assertIsNone(layers.ensure_unlocked([]))
        self.assertIsNone(layers.ensure_unlocked(None))
        self.assertIsNone(layers.ensure_unlocked(["pelvis-obj"]))
        self.assertIsNone(layers.ensure_unlocked(["loose-obj"]))
        self.assertIsNone(layers.ensure_unlocked("pelvis-obj"))

    def test_locked_object_returns_the_failure_payload(self):
        result = layers.ensure_unlocked(["jaw-obj"])
        self.assertIsNotNone(result)
        self.assertFalse(result["ok"])
        self.assertIn("locked", result["error"].lower())
        self.assertIn("jaw_MainPoint", result["error"])
        self.assertEqual(result["objects"], ["jaw_MainPoint"])
        self.assertEqual(len(result["layers"]), 1)
        layer = result["layers"][0]
        self.assertEqual(layer["name"], "jaw")
        self.assertEqual(layer["path"], "Face/jaw")
        self.assertEqual(layer["kind"], layers.LAYER)
        self.assertTrue(layer["locked"])
        self.assertEqual(layer["objects"], ["jaw_MainPoint"])
        self.assertIn("set_layer_locked", result["hint"])
        self.assertIn("locked=false", result["hint"])

    def test_mixed_input_reports_only_the_locked_object(self):
        result = layers.ensure_unlocked(["pelvis-obj", "jaw-obj"])
        self.assertEqual(result["objects"], ["jaw_MainPoint"])
        self.assertNotIn("pelvis_MainPoint", result["objects"])

    def test_string_argument_is_one_object_id(self):
        result = layers.ensure_unlocked("jaw-obj")
        self.assertEqual(result["objects"], ["jaw_MainPoint"])


class ToolDefinitionTests(unittest.TestCase):
    def test_tools_follow_the_registry_contract(self):
        expected = [
            "list_layers",
            "create_layer",
            "create_layer_folder",
            "delete_layer",
            "set_layer_visible",
            "set_layer_locked",
            "rename_layer",
            "move_to_layer",
        ]
        self.assertEqual([tool["name"] for tool in layers.TOOLS], expected)
        for tool in layers.TOOLS:
            self.assertIn("description", tool)
            self.assertGreater(len(tool["description"]), 40, tool["name"])
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertIn("fn", tool)
            self.assertNotIn("handler", tool)

    def test_required_arguments(self):
        by_name = {tool["name"]: tool for tool in layers.TOOLS}
        self.assertEqual(by_name["list_layers"]["inputSchema"]["properties"], {})
        self.assertEqual(by_name["list_layers"]["inputSchema"]["required"], [])
        self.assertEqual(by_name["create_layer"]["inputSchema"]["required"], ["name"])
        self.assertEqual(by_name["create_layer_folder"]["inputSchema"]["required"], ["name"])
        self.assertEqual(by_name["delete_layer"]["inputSchema"]["required"], ["name"])
        self.assertEqual(by_name["set_layer_visible"]["inputSchema"]["required"], ["name", "visible"])
        self.assertEqual(by_name["set_layer_locked"]["inputSchema"]["required"], ["name", "locked"])
        self.assertEqual(by_name["rename_layer"]["inputSchema"]["required"], ["name", "new_name"])
        self.assertEqual(by_name["move_to_layer"]["inputSchema"]["required"], ["controllers", "layer"])

    def test_descriptions_are_english_and_mention_undo(self):
        for tool in layers.TOOLS:
            self.assertNotIn("圖層", tool["description"])
        for name in ("create_layer", "delete_layer", "set_layer_visible", "set_layer_locked"):
            self.assertIn("undo", {tool["name"]: tool for tool in layers.TOOLS}[name]["description"].lower())


if __name__ == "__main__":
    unittest.main()
