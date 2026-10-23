"""Live tests for the animation layer tools (T23).

Run with: python tests/live/run.py test_layers.py

Every test opens Cascy.casc in its own tab; the fixture discards it and restores the user's
scene in tearDown. The tests drive the MCP tools and only use the layer APIs Cascadeur's own
bundled scripts use (layers_map()/folders_map(), header() and layer objects' obj_ids).
Viewer.item(), Folder.children_ordered() and ItemVariant.header() are never called: on
Cascadeur 2026.2.3 they corrupt the heap and crash the application (measured 2026-10-24).

The completion condition of T23 asks whether locking a layer stops a data write to the
control point in it (set_pose does not exist yet). test_locked_layer_write checks exactly
that and reports Cascadeur's real behaviour.
"""

from __future__ import annotations

import json
import time
import unittest

try:  # imported as live.test_layers by unittest discovery
    from . import client
    from .fixture import LiveTestCase
except ImportError:  # executed by run.py
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]

LEFT_HAND = "hand_MainPoint_l"
LAYER = "T23 probe"
RENAMED = "T23 renamed"
FOLDER = "T23 Folder"
INNER = "inner"

SNAPSHOT = """
import json

lv = scene.layers_viewer()
mv = scene.model_viewer()
root = lv.root_id()
layers = lv.layers_map()
folders = lv.folders_map()
wanted = {wanted}
objects = dict()
for guid, layer in layers.items():
    if str(lv.header(guid).name) in wanted:
        objects[str(lv.header(guid).name)] = len(layer.obj_ids)
hand_ids = mv.get_objects({hand})
hand = {{}}
if hand_ids:
    layer_id = lv.layer_id_by_obj_id(hand_ids[0])
    hand = {{
        "object_id": hand_ids[0].to_string(),
        "layer_id": layer_id.to_string(),
        "layer_name": str(lv.header(layer_id).name),
    }}
print("__RESULT__" + json.dumps({{
    "root": root.to_string(),
    "root_in_folders": any(guid == root for guid in folders.keys()),
    "layers": len(layers),
    "folders": len(folders),
    "objects": objects,
    "hand": hand,
}}))
"""

LOCKED_WRITE = """
import json

import numpy as np

from cascadeur_mcp_plus import common, rig

ids = rig.resolve(["left_hand"])
mv = scene.model_viewer()
bv = mv.behaviour_viewer()
dv = mv.data_viewer()
transform = bv.get_behaviour_by_name(ids[0], "Transform")
data_id = bv.get_behaviour_data(transform, "global_position")
before = dv.get_data_value(data_id, 0)
target = before + np.array([5.0, 0.0, 0.0], dtype=before.dtype)


def change(model_editor, update_editor, scene_updater):
    model_editor.data_editor().set_data_value(data_id, 0, target)
    scene_updater.run_update({data_id}, 0)


result = common.edit("T23 locked write", change)
after = [float(value) for value in dv.get_data_value(data_id, 0)]
print("__RESULT__" + json.dumps({"edit": result, "before": [float(value) for value in before], "after": after}))
"""


class LayerLiveTests(LiveTestCase):
    def setUp(self) -> None:
        super().setUp()
        opened = self.open_sample("Cascy.casc")
        self.assertEqual(opened["name"], "Cascy.casc")
        # The timeline is rebuilt right after a scene loads; give it a moment before reading
        # layers, because the layer viewer is backed by the timeline model.
        time.sleep(1.0)

    # -- helpers -----------------------------------------------------------

    def _run_json(self, code: str, timeout: float = 90.0) -> dict:
        result = client.call("run_python", code=code, timeout=timeout, http_timeout=timeout + 30.0)
        self.assertTrue(isinstance(result, dict) and result.get("ok"), f"run_python failed: {result!r}")
        for line in reversed(str(result.get("stdout", "")).splitlines()):
            if line.startswith("__RESULT__"):
                return json.loads(line[len("__RESULT__") :])
        self.fail(f"run_python produced no __RESULT__ line: {result!r}")

    def _snapshot(self, wanted=()) -> dict:
        return self._run_json(SNAPSHOT.format(wanted=json.dumps(list(wanted)), hand=repr(LEFT_HAND)))

    @staticmethod
    def _flatten(tree) -> list[dict]:
        nodes = []
        for node in tree:
            nodes.append(node)
            nodes.extend(LayerLiveTests._flatten(node.get("children") or []))
        return nodes

    def _listed(self) -> dict:
        listed = client.call("list_layers")
        self.assertTrue(listed.get("ok"), listed)
        return listed

    def _find(self, listed: dict, name: str, kind: str = "layer"):
        matches = [node for node in self._flatten(listed["tree"]) if node["kind"] == kind and node["name"] == name]
        return matches[0] if len(matches) == 1 else None

    def _create(self, name: str = LAYER, parent=None) -> dict:
        arguments = {"name": name}
        if parent is not None:
            arguments["parent"] = parent
        created = client.call("create_layer", **arguments)
        self.assertTrue(created.get("ok"), created)
        return created

    def _delete(self, name: str = LAYER, force=True) -> dict:
        return client.call("delete_layer", name=name, force=force)

    def _undo_expect(self, step: str) -> dict:
        undone = client.call("undo")
        self.assertEqual(undone.get("steps"), [step], undone)
        return undone

    def _call_error(self, tool: str, **arguments) -> dict:
        """Call a tool that must fail and return its parsed {"ok": false, ...} result.

        tools/live/client.call raises LiveError for an isError result, which is exactly how the
        server reports common.fail(); parse the JSON it carries.
        """
        try:
            result = client.call(tool, **arguments)
        except client.LiveError as exc:
            message = str(exc)
            prefix = f"{tool} failed: "
            self.assertTrue(message.startswith(prefix), message)
            return json.loads(message[len(prefix) :])
        self.fail(f"{tool} should have failed but returned {result!r}")

    # -- tests -------------------------------------------------------------

    def test_list_layers_matches_the_scene(self) -> None:
        listed = self._listed()
        self.assertEqual(listed["root"]["name"], "Scene")
        snapshot = self._snapshot()
        self.assertEqual(listed["counts"]["layers"], snapshot["layers"])
        folders_shown = snapshot["folders"] - (1 if snapshot["root_in_folders"] else 0)
        self.assertEqual(listed["counts"]["folders"], folders_shown)

        nodes = self._flatten(listed["tree"])
        self.assertEqual(len([node for node in nodes if node["kind"] == "layer"]), snapshot["layers"])
        for node in nodes:
            self.assertIn(node["kind"], ("layer", "folder"))
            self.assertTrue(node["id"])
            self.assertTrue(node["path"])
            self.assertIsInstance(node["objects"], int)
            if node["kind"] == "layer":
                self.assertIsInstance(node["visible"], bool)
                self.assertIsInstance(node["locked"], bool)
            else:
                self.assertIsNone(node["visible"])
                self.assertIsNone(node["locked"])

        hand = snapshot["hand"]
        self.assertTrue(hand, "Cascy has no hand_MainPoint_l object")
        node = next(node for node in nodes if node["id"] == hand["layer_id"])
        self.assertEqual(node["name"], hand["layer_name"])
        self.assertGreaterEqual(node["objects"], 1)

    def test_create_move_and_one_undo_each(self) -> None:
        original = self._snapshot([LAYER])
        original_layer = original["hand"]["layer_name"]

        created = self._create()
        self.assertEqual(created["layer"]["name"], LAYER)
        self.assertEqual(created["layer"]["path"], LAYER)
        self.assertEqual(created["layer"]["objects"], 0)
        self.assertIsNotNone(self._find(self._listed(), LAYER))

        moved = client.call("move_to_layer", controllers="left_hand", layer=LAYER)
        self.assertTrue(moved.get("ok"), moved)
        self.assertEqual(moved["moved"], [LEFT_HAND])
        self.assertGreaterEqual(self._snapshot([LAYER])["objects"].get(LAYER), 1)
        self.assertEqual(self._snapshot([LAYER])["hand"]["layer_name"], LAYER)

        self._undo_expect(f"MCP: move objects to layer {LAYER}")
        self.assertEqual(self._snapshot([LAYER])["hand"]["layer_name"], original_layer)
        self.assertEqual(self._snapshot([LAYER])["objects"].get(LAYER), 0)

        self._undo_expect(f"MCP: create layer {LAYER}")
        self.assertIsNone(self._find(self._listed(), LAYER))

    def test_visible_lock_and_rename_undo(self) -> None:
        self._create()
        self.assertTrue(self._find(self._listed(), LAYER)["visible"])

        hidden = client.call("set_layer_visible", name=LAYER, visible=False)
        self.assertTrue(hidden.get("ok"), hidden)
        self.assertFalse(hidden["visible"])
        self.assertFalse(self._find(self._listed(), LAYER)["visible"])
        self._undo_expect(f"MCP: hide layer {LAYER}")
        self.assertTrue(self._find(self._listed(), LAYER)["visible"])

        locked = client.call("set_layer_locked", name=LAYER, locked=True)
        self.assertTrue(locked.get("ok"), locked)
        self.assertTrue(locked["locked"])
        self.assertTrue(self._find(self._listed(), LAYER)["locked"])
        again = client.call("set_layer_locked", name=LAYER, locked=True)
        self.assertTrue(again.get("ok"), again)
        self.assertFalse(again["changed"])
        self._undo_expect(f"MCP: lock layer {LAYER}")
        self.assertFalse(self._find(self._listed(), LAYER)["locked"])

        renamed = client.call("rename_layer", name=LAYER, new_name=RENAMED)
        self.assertTrue(renamed.get("ok"), renamed)
        self.assertEqual(renamed["item"]["name"], RENAMED)
        self.assertIsNone(self._find(self._listed(), LAYER))
        self.assertIsNotNone(self._find(self._listed(), RENAMED))
        self._undo_expect(f"MCP: rename layer {LAYER} to {RENAMED}")
        self.assertIsNotNone(self._find(self._listed(), LAYER))

    def test_delete_needs_force_for_a_non_empty_layer(self) -> None:
        self._create()
        client.call("move_to_layer", controllers="left_hand", layer=LAYER)
        # Moving a control point also moves its boxes and direction points, so the layer holds a
        # small group of objects, not exactly one.
        self.assertGreaterEqual(self._snapshot([LAYER])["objects"].get(LAYER, 0), 1)
        self.assertEqual(self._snapshot([LAYER])["hand"]["layer_name"], LAYER)

        refused = self._call_error("delete_layer", name=LAYER, force=False)
        self.assertIn("force=true", refused.get("hint", ""))
        self.assertIsNotNone(self._find(self._listed(), LAYER))

        deleted = self._delete(LAYER, force=True)
        self.assertTrue(deleted.get("ok"), deleted)
        self.assertGreaterEqual(deleted["objects"], 1)
        self.assertIsNone(self._find(self._listed(), LAYER))

        self._undo_expect(f"MCP: delete layer {LAYER}")
        restored = self._find(self._listed(), LAYER)
        self.assertIsNotNone(restored)
        self.assertGreaterEqual(restored["objects"], 1)
        self.assertEqual(self._snapshot([LAYER])["hand"]["layer_name"], LAYER)

    def test_folders_paths_and_ambiguous_names(self) -> None:
        folder = client.call("create_layer_folder", name=FOLDER)
        self.assertTrue(folder.get("ok"), folder)
        self.assertEqual(folder["folder"]["path"], FOLDER)
        # Cascadeur refuses a folder without layers, so the tool asks for the default layer
        # (named Track0 by Cascadeur) and returns it.
        default_layer = folder["default_layer"]
        self.assertEqual(default_layer["kind"], "layer")
        self.assertEqual(default_layer["parent"], folder["folder"]["id"])
        inner = client.call("create_layer", name=INNER, parent=FOLDER)
        self.assertTrue(inner.get("ok"), inner)
        self.assertEqual(inner["layer"]["path"], f"{FOLDER}/{INNER}")

        listed = self._listed()
        folder_node = self._find(listed, FOLDER, kind="folder")
        self.assertIsNotNone(folder_node)
        child_names = {child["name"] for child in folder_node["children"]}
        self.assertEqual(child_names, {default_layer["name"], INNER})
        self.assertEqual(folder_node["objects"], 0)

        duplicate = self._call_error("create_layer", name=INNER, parent=FOLDER)
        self.assertIn("already exists", duplicate["error"])

        # The same name in a different folder is allowed; lookups then need the path.
        root_inner = self._create(INNER)
        self.assertEqual(root_inner["layer"]["path"], INNER)
        ambiguous = self._call_error("move_to_layer", controllers="left_hand", layer=INNER)
        self.assertEqual(len(ambiguous["candidates"]), 2)

        moved = client.call("move_to_layer", controllers="left_hand", layer=f"{FOLDER}/{INNER}")
        self.assertTrue(moved.get("ok"), moved)
        self.assertEqual(moved["layer"]["path"], f"{FOLDER}/{INNER}")

        refused = self._call_error("delete_layer", name=FOLDER)
        self.assertIn("not empty", refused["error"])
        deleted = client.call("delete_layer", name=FOLDER, force=True)
        self.assertTrue(deleted.get("ok"), deleted)
        listed = self._listed()
        self.assertIsNone(self._find(listed, FOLDER, kind="folder"))
        remaining = self._find(listed, INNER)
        self.assertIsNotNone(remaining)
        self.assertEqual(remaining["path"], INNER)

    def test_locked_layer_write_is_refused_or_ineffective(self) -> None:
        self._create("T23 locked")
        client.call("move_to_layer", controllers="left_hand", layer="T23 locked")
        locked = client.call("set_layer_locked", name="T23 locked", locked=True)
        self.assertTrue(locked.get("ok"), locked)
        self.assertTrue(self._find(self._listed(), "T23 locked")["locked"])
        self.assertEqual(self._snapshot(["T23 locked"])["hand"]["layer_name"], "T23 locked")

        result = self._run_json(LOCKED_WRITE)
        edit = result["edit"]
        moved = max(abs(after - before) for before, after in zip(result["before"], result["after"], strict=False)) > 0.5
        if edit.get("ok") and moved:
            self.fail(
                "Cascadeur 2026.2.3 did not block a global_position write to a control point on a locked "
                f"layer: {json.dumps(result)[:1500]}"
            )
        # Either the write was refused (edit not ok) or Cascadeur accepted the command but left
        # the value unchanged (moved is false); both are the "locked or ineffective" outcome of
        # the ticket. A successful, effective write fails the test above.

        unlocked = client.call("set_layer_locked", name="T23 locked", locked=False)
        self.assertTrue(unlocked.get("ok"), unlocked)
        deleted = self._delete("T23 locked", force=True)
        self.assertTrue(deleted.get("ok"), deleted)


if __name__ == "__main__":
    unittest.main()
