r"""Live checks for the Quick Rigging tools (T29).

The dangerous path is exercised deliberately: export Cascy as a skeleton-only FBX, import it
into a blank scene (import_model enters Rig mode), then call quick_rig.  Every tab is opened by
the test fixture and closed in tearDown; the user's scene, tab list and frame are checked.

The safety test calls quick_rig with enter_rig_mode=false on a normally rigged scene, so the
tool must return the AnimationInfo error without ever asking Cascadeur to create rig elements.

Run with: python tests/live/run.py test_rigging.py
"""

from __future__ import annotations

import os
import unittest

try:
    from . import client
    from .fixture import LiveTestCase
except ImportError:  # executed from tests/live
    import client  # type: ignore[no-redef]
    from fixture import LiveTestCase  # type: ignore[no-redef]


NEW_SCENE_CODE = """
sm = csc.app.get_application().get_scene_manager()
tab = sm.create_application_scene()
sm.set_current_scene(tab)
print("__NEWSCENE__" + tab.name())
"""

SELECT_JOINTS_CODE = """
mv = scene.model_viewer()
bv = mv.behaviour_viewer()
ids = []
for behaviour_id in bv.get_behaviours("Joint"):
    ids.append(bv.get_behaviour_owner(behaviour_id))
scene.selector().select(set(ids))
print("__JOINTS__" + str(len(ids)))
"""


def _print_value(output: dict, prefix: str) -> str:
    for line in reversed(str(output.get("stdout", "")).splitlines()):
        if line.startswith(prefix):
            return line[len(prefix) :]
    raise AssertionError(f"run_python produced no {prefix} line: {output!r}")


class RiggingLiveTests(LiveTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.case_dir = self.out_dir()
        self.created: list[str] = []

    def tearDown(self) -> None:
        for path in self.created:
            try:
                os.remove(path)
            except OSError:
                pass
        super().tearDown()

    def new_scene(self) -> str:
        output = client.call("run_python", code=NEW_SCENE_CODE)
        self.assertTrue(output.get("ok"), output)
        return _print_value(output, "__NEWSCENE__")

    def export_path(self, name: str) -> str:
        path = os.path.join(self.case_dir, name)
        if os.path.exists(path):
            os.remove(path)
        self.created.append(path)
        return path

    def test_list_rig_templates(self) -> None:
        result = client.call("list_rig_templates")
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(result["count"], 10, result)
        names = {template["name"] for template in result["templates"]}
        self.assertIn("Cascy", names, result)
        cascy = next(template for template in result["templates"] if template["name"] == "Cascy")
        self.assertTrue(cascy["readable"], cascy)
        self.assertGreater(cascy["joints"], 0, cascy)
        self.assertTrue(os.path.isfile(cascy["path"]), cascy)
        self.assertTrue(cascy["path"].lower().endswith((".qrigcasc", ".qrt")), cascy)

    def test_quick_rig_from_exported_skeleton(self) -> None:
        # 1. A skeleton-only FBX from Cascy. Selecting every joint and using objects=selected
        #    keeps the mesh and the existing rig out of the export.
        self.open_sample("Cascy.casc")
        selected = client.call("run_python", code=SELECT_JOINTS_CODE)
        self.assertTrue(selected.get("ok"), selected)
        joints = int(_print_value(selected, "__JOINTS__"))
        self.assertGreater(joints, 0)
        fbx = self.export_path("cascy-skeleton.fbx")
        exported = client.call("export_scene", path=fbx, objects="selected", timeout=300, http_timeout=330)
        self.assertTrue(exported["ok"], exported)
        self.assertGreater(exported["bytes"], 0, exported)
        self.assertEqual(exported["objects"], "selected", exported)

        # 2. Import the skeleton into a blank tab. import_model enters Rig mode itself, so
        #    quick_rig starts from exactly one AnimationInfo (the state the guard requires).
        self.new_scene()
        imported = client.call("import_model", path=fbx, timeout=300, http_timeout=330)
        self.assertTrue(imported["ok"], imported)
        self.assertGreater(imported["joints"], 0, imported)
        self.assertEqual(imported["animation_info"], 1, imported)
        self.assertTrue(imported["rig_mode"], imported)
        self.assertEqual(imported["meshes"], 0, f"the export should contain the skeleton only: {imported!r}")

        # 3. The actual Quick Rigging call.
        rigged = client.call(
            "quick_rig",
            create_autoposing=True,
            timeout=300,
            http_timeout=330,
        )
        self.assertTrue(rigged["ok"], rigged)
        self.assertEqual(rigged["template"]["name"], "Cascy", rigged)
        self.assertGreaterEqual(rigged["rig_info"], 1, rigged)
        self.assertEqual(rigged["animation_info"], 0, rigged)
        self.assertIsNotNone(rigged["rig"], rigged)
        self.assertGreater(rigged["controllers"]["total"], 0, rigged)
        self.assertGreater(rigged["controllers"]["main_points"], 0, rigged)
        self.assertTrue(rigged["autoposing"]["requested"], rigged)
        self.assertTrue(rigged["autoposing"]["created"], rigged)
        # AutoPosing in 2026.2.3 is a tool state, not an AutoPosingInfo behaviour on every rig:
        # the toolbar's active property is the authoritative check (T15's research).
        self.assertIs(rigged["autoposing"]["active"], True, rigged)
        self.assertIs(rigged["autoposing"]["tool_setting"], True, rigged)
        self.assertNotIn("dialogs", rigged, rigged)
        self.assertNotIn("windows", rigged, rigged)

        # 4. The scene is a normal rigged scene again, and T12 sees the controllers.
        info = client.call("get_scene_info")
        self.assertGreaterEqual(info["behaviours"].get("RigInfo", 0), 1, info["behaviours"])
        self.assertEqual(info["behaviours"].get("AnimationInfo", 0), 0, info["behaviours"])
        controllers = client.call("list_controllers", kinds=["main_point"])
        self.assertTrue(controllers["ok"], controllers)
        self.assertGreater(controllers["count"], 0, controllers)
        aliases = {controller["alias"] for controller in controllers["controllers"] if controller["alias"]}
        self.assertIn("left_hand", aliases, controllers)
        self.assertIn("pelvis", aliases, controllers)
        characters = client.call("list_characters")
        self.assertGreaterEqual(characters["count"], 1, characters)

        # 5. Quick Rigging must not leave its window or a dialog open.
        dialogs = client.call("dialogs")
        open_dialogs = [
            dialog
            for dialog in dialogs["dialogs"]
            if dialog.get("kind") == "dialog" and (dialog.get("buttons") or dialog.get("type") != "QQuickPopupItem")
        ]
        self.assertEqual(open_dialogs, [], dialogs)
        tree = client.call("ui_tree", query="Quick Rigging")
        titles = [window["title"] for window in tree["windows"]]
        self.assertNotIn("Quick Rigging", titles, tree["windows"])
        self.assertNotIn("Rig mode helper", titles, tree["windows"])

    def test_quick_rig_refuses_outside_rig_mode(self) -> None:
        # Cascy.casc has a RigInfo but is not in Rig mode (AnimationInfo 0).  With
        # enter_rig_mode=false, quick_rig must stop at the guard instead of adding rig elements:
        # that is the exact operation that crashed Cascadeur twice.
        self.open_sample("Cascy.casc")
        before = client.call("get_scene_info")
        with self.assertRaises(client.LiveError) as caught:
            client.call(
                "quick_rig",
                template="Cascy",
                enter_rig_mode=False,
                timeout=300,
                http_timeout=330,
            )
        message = str(caught.exception)
        self.assertIn("exactly 1", message)
        self.assertIn("crashes", message)
        self.assertIn("rig_mode_check", message)

        after = client.call("get_scene_info")
        self.assertEqual(after["objects"]["count"], before["objects"]["count"], "the refusing call changed the scene")
        self.assertEqual(after["behaviours"].get("AnimationInfo"), 0, after["behaviours"])
        self.assertGreaterEqual(after["behaviours"].get("RigInfo", 0), 1, after["behaviours"])
        leftover = client.call("dialogs")["dialogs"]
        open_dialogs = [
            dialog
            for dialog in leftover
            if dialog.get("kind") == "dialog" and (dialog.get("buttons") or dialog.get("type") != "QQuickPopupItem")
        ]
        strays = [
            window
            for window in leftover
            if window.get("kind") == "window"
            and any(
                needle in str(window.get("title", "")).casefold() for needle in ("quick rigging", "rig mode helper")
            )
        ]
        self.assertEqual(open_dialogs, [], "the refusing call left a dialog open")
        self.assertEqual(strays, [], "the refusing call left a rigging window open")


if __name__ == "__main__":
    unittest.main()
