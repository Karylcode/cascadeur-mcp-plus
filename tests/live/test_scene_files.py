"""Live tests for T10's scene-file tools.

Each test opens its own sample tabs with open_scene (never the user's tab), and the
LiveTestCase teardown closes anything left and checks the user's scene list, current
scene and frame are unchanged.

Run with: python tests/live/run.py test_scene_files.py
"""

from __future__ import annotations

import os
import unittest

try:
    from . import client
    from .fixture import LiveTestCase, samples_dir
except ImportError:
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase, samples_dir  # type: ignore[no-redef]


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


class SceneFilesLiveTests(LiveTestCase):
    def expect_error(self, tool: str, **arguments) -> str:
        with self.assertRaises(client.LiveError) as caught:
            client.call(tool, **arguments)
        return str(caught.exception)

    def scenes_by_path(self) -> dict[str, dict]:
        listing = client.call("list_open_scenes")
        self.assertTrue(listing["ok"], listing)
        return {_norm(scene["path"]): scene for scene in listing["scenes"] if scene["path"]}

    def current_scene(self) -> dict:
        listing = client.call("list_open_scenes")
        scene = next((item for item in listing["scenes"] if item["current"]), None)
        self.assertIsNotNone(scene, f"list_open_scenes marked no scene as current: {listing!r}")
        return scene

    def blocking_dialogs(self) -> list:
        """Dialogs our tools must answer; a buttonless QQuickPopupItem is a tooltip.

        Other agents drive the same Cascadeur, and a toolbar tooltip (observed: "Surface")
        can be left visible. It has no buttons and no Save/Cancel meaning, so it is not a
        prompt that scene tools can or should dismiss.
        """
        dialogs = client.call("dialogs").get("dialogs", [])
        return [dialog for dialog in dialogs if dialog.get("buttons") or dialog.get("type") != "QQuickPopupItem"]

    def test_open_save_shows_returns_refuse_overwrite(self):
        sample = os.path.join(samples_dir(), "Cascy.casc")
        opened = client.call("open_scene", path=sample)
        self.assertTrue(opened["ok"], opened)
        scene = opened["scene"]
        self.assertEqual(scene["name"], "Cascy.casc")
        self.assertEqual(_norm(scene["path"]), _norm(sample))
        self.assertTrue(scene["current"])

        destination = os.path.join(self.out_dir(), "Cascy-T10.casc")
        if os.path.exists(destination):
            os.remove(destination)

        saved = client.call("save_scene", path=destination)
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(_norm(saved["saved_to"]), _norm(destination))
        self.assertFalse(saved["overwritten"])
        self.assertTrue(os.path.isfile(destination), "save_scene reported success but wrote no file")

        # Saving as should make the scene's current file the new path.
        listed = self.scenes_by_path()
        self.assertIn(_norm(destination), listed, "save_scene(path=...) did not update the scene's path")
        self.assertTrue(listed[_norm(destination)]["current"])

        refused = self.expect_error("save_scene", path=destination)
        self.assertIn("overwrite=true", refused)
        overwritten = client.call("save_scene", path=destination, overwrite=True)
        self.assertTrue(overwritten["ok"], overwritten)
        self.assertTrue(overwritten["overwritten"])

        # With no path, the current file is only rewritten with overwrite=true.
        refused_current = self.expect_error("save_scene")
        self.assertIn("overwrite=true", refused_current)
        self.assertIn("path=", refused_current)
        current_save = client.call("save_scene", overwrite=True)
        self.assertTrue(current_save["ok"], current_save)
        self.assertEqual(_norm(current_save["saved_to"]), _norm(destination))

        empty = self.blocking_dialogs()
        self.assertEqual(empty, [], f"a dialog was left open: {empty!r}")

    def test_list_switch_and_close_two_test_scenes(self):
        first_path = os.path.join(samples_dir(), "Cascy.casc")
        second_path = os.path.join(samples_dir(), "Dracorex_walk_cycle.casc")
        first = client.call("open_scene", path=first_path)
        self.assertTrue(first["ok"], first)
        second = client.call("open_scene", path=second_path)
        self.assertTrue(second["ok"], second)
        self.assertTrue(second["scene"]["current"])

        scenes = self.scenes_by_path()
        self.assertIn(_norm(first_path), scenes)
        self.assertIn(_norm(second_path), scenes)
        self.assertTrue(scenes[_norm(second_path)]["current"])
        self.assertEqual(len([scene for scene in scenes.values() if scene["current"]]), 1)

        switched = client.call("switch_scene", scene=first_path)
        self.assertTrue(switched["ok"], switched)
        self.assertEqual(_norm(self.current_scene()["path"]), _norm(first_path))

        switched = client.call("switch_scene", scene=second["scene"]["name"])
        self.assertTrue(switched["ok"], switched)
        self.assertEqual(_norm(self.current_scene()["path"]), _norm(second_path))

        closed = client.call("close_scene", scene=first_path)
        self.assertTrue(closed["ok"], closed)
        self.assertEqual(_norm(closed["closed"]["path"]), _norm(first_path))
        self.assertFalse(closed["discarded"])
        remaining = self.scenes_by_path()
        self.assertNotIn(_norm(first_path), remaining)
        self.assertIn(_norm(second_path), remaining)
        self.assertEqual(_norm(self.current_scene()["path"]), _norm(second_path))

        closed = client.call("close_scene", scene=second_path)
        self.assertTrue(closed["ok"], closed)
        remaining = self.scenes_by_path()
        self.assertNotIn(_norm(second_path), remaining)
        dialogs = self.blocking_dialogs()
        self.assertEqual(dialogs, [], f"a dialog was left open: {dialogs!r}")

    def test_modified_scene_requires_discard_changes(self):
        sample = os.path.join(samples_dir(), "Cascy.casc")
        opened = client.call("open_scene", path=sample)
        self.assertTrue(opened["ok"], opened)
        path = opened["scene"]["path"]

        modification = client.call(
            "run_python",
            code="""
mv = scene.model_viewer()
bv = mv.behaviour_viewer()
dv = mv.data_viewer()
frame = scene.get_current_frame()
owner = mv.get_objects("toe_r")[0]
transform = bv.get_behaviour_by_name(owner, "Transform")
position_id = bv.get_behaviour_data(transform, "global_position")
value = dv.get_data_value(position_id, frame)

def change(model_editor, update_editor, scene_updater):
    model_editor.data_editor().set_data_value(position_id, frame, value + [1.0, 0.0, 0.0])
    scene_updater.run_update({position_id}, frame)

result = scene.modify_update("T10 live modification", change)
""",
            timeout=30,
        )
        self.assertTrue(modification["ok"], modification)
        self.assertEqual(modification.get("value"), "True", f"the test modification did not apply: {modification!r}")

        refused = self.expect_error("close_scene", scene=path)
        self.assertIn("unsaved changes", refused)
        self.assertIn(_norm(path), {key for key in self.scenes_by_path()})
        dialogs = self.blocking_dialogs()
        self.assertEqual(dialogs, [], f"the refusal left a dialog open: {dialogs!r}")

        closed = client.call("close_scene", scene=path, discard_changes=True)
        self.assertTrue(closed["ok"], closed)
        self.assertTrue(closed["discarded"])
        self.assertNotIn(_norm(path), self.scenes_by_path())
        dialogs = self.blocking_dialogs()
        self.assertEqual(dialogs, [], f"discarding left a dialog open: {dialogs!r}")


if __name__ == "__main__":
    unittest.main()
