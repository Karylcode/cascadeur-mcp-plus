r"""Live checks for the import/export tools (T11).

Each test opens its own sample tab (Cascy.casc, or Backflip_animation.casc for the animation
source), writes files under %TEMP%\cascadeur-mcp-plus-tests\ImportExportTests and imports them
into fresh blank tabs. tearDown discards every tab the test opened and checks the user's
scenes, current scene and frame are unchanged.

Run with: python tests/live/run.py test_import_export.py
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

JOINTS_CODE = """
mv = scene.model_viewer()
bv = mv.behaviour_viewer()
names = []
for behaviour_id in bv.get_behaviours("Joint"):
    name = mv.get_object_name(bv.get_behaviour_owner(behaviour_id))
    if name and " " not in name and ":" not in name and name not in names:
        names.append(name)
    if len(names) >= 2:
        break
print("__JOINTS__" + "|".join(names))
"""

USD_PROBE_CODE = """
print("csc_usd=" + str(hasattr(csc, "usd")))
print("usd_modules=" + repr([name for name in dir(csc) if "usd" in name.lower()]))
print("usd_in_tools=" + str("usd" in repr(app.get_tools_manager().tools()).lower()))
for name in ("UsdSceneLoader", "USDSceneLoader", "UsdLoader"):
    try:
        print("get_tool " + name + " -> " + repr(app.get_tools_manager().get_tool(name))[:120])
    except Exception as exc:
        print("get_tool " + name + " -> " + type(exc).__name__)
"""

KEYS_CODE = """
import json
lv = scene.layers_viewer()
positions = []
for layer_id in lv.all_layer_ids():
    if layer_id.is_null():
        continue
    try:
        positions.append(int(lv.layer(layer_id).last_key_pos()))
    except Exception:
        pass
print("__KEYS__" + json.dumps(max(positions) if positions else None))
"""


def _print_value(output: dict, prefix: str) -> str:
    for line in reversed(str(output.get("stdout", "")).splitlines()):
        if line.startswith(prefix):
            return line[len(prefix) :]
    raise AssertionError(f"run_python produced no {prefix} line: {output!r}")


def write_bvh(path: str, joints: list[str], frames: int = 12) -> None:
    """Write a minimal two-joint BVH so the official importer can key 12 frames."""
    root, child = joints[0], joints[1]
    lines = [
        "HIERARCHY",
        f"ROOT {root}",
        "{",
        "  OFFSET 0 0 0",
        "  CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation",
        f"  JOINT {child}",
        "  {",
        "    OFFSET -10 -10 0",
        "    CHANNELS 3 Zrotation Xrotation Yrotation",
        "    End Site",
        "    {",
        "      OFFSET 0 0 -20",
        "    }",
        "  }",
        "}",
        "MOTION",
        f"Frames: {frames}",
        "Frame Time: 0.0333333",
    ]
    for index in range(frames):
        angle = 5.0 * index
        lines.append(f"0 0 0 0 0 0 {angle:.3f} 0 0")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


class ImportExportTests(LiveTestCase):
    def setUp(self) -> None:
        super().setUp()
        # Every file this test writes is disposable; stale files from an earlier run would
        # make the overwrite checks fail for the wrong reason.
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
        """Create a blank tab and make it current; tearDown closes it."""
        output = client.call("run_python", code=NEW_SCENE_CODE)
        self.assertTrue(output.get("ok"), output)
        return _print_value(output, "__NEWSCENE__")

    def assert_tool_error(self, tool: str, *fragments: str, **arguments) -> str:
        """Call a tool that must fail; the server reports ok:false as an MCP error."""
        with self.assertRaises(client.LiveError) as caught:
            client.call(tool, **arguments)
        message = str(caught.exception)
        for fragment in fragments:
            self.assertIn(fragment, message)
        return message

    def last_key(self):
        """Highest keyed frame on the current scene's animation layers, or None."""
        output = client.call("run_python", code=KEYS_CODE)
        self.assertTrue(output.get("ok"), output)
        value = _print_value(output, "__KEYS__")
        return None if value == "null" else int(value)

    def export_path(self, name: str) -> str:
        path = os.path.join(self.case_dir, name)
        if os.path.exists(path):
            os.remove(path)
        self.created.append(path)
        return path

    def test_export_and_reimport_fbx_then_glb(self) -> None:
        self.open_sample("Cascy.casc")
        exports = {}
        for suffix in ("fbx", "glb"):
            path = self.export_path(f"cascy.{suffix}")
            result = client.call("export_scene", path=path, timeout=300, http_timeout=330)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["format"], suffix, result)
            self.assertGreater(result["bytes"], 0, result)
            self.assertTrue(os.path.isfile(path), path)
            exports[suffix] = path

        for suffix, path in exports.items():
            self.new_scene()
            imported = client.call("import_model", path=path, timeout=300, http_timeout=330)
            self.assertTrue(imported["ok"], imported)
            self.assertGreater(imported["objects_added"], 0, imported)
            self.assertEqual(imported["format"], suffix, imported)
            self.assertTrue(imported["rig_mode"], imported)
            self.assertEqual(imported["animation_info"], 1, imported)
            # The native import plus "Rig mode on": the tool reports how many undos revert it.
            # (The suite does not call Scene.Undo here: after an undo run on 2026-10-24 a later
            # tab close crashed Cascadeur, and the field itself is what the review asked for.)
            self.assertEqual(imported["undo_steps"], 2, imported)
            info = client.call("get_scene_info")
            self.assertEqual(info["behaviours"].get("AnimationInfo"), 1, info["behaviours"])
            self.dismiss_dialogs(timeout=2.0)

    def test_import_animation_matches_source_length(self) -> None:
        # Dracorex_walk_cycle.casc and Dracorex.casc are two copies of the same 100-joint rig, and
        # only the walk cycle has keys. That is the case FbxLoader.import_animation supports: it
        # maps a take onto objects with the same joint names. Applying a take to a different
        # skeleton is retargeting (T28); Cascy's 66 joints share only 24 names with the 58-joint
        # Backflip sample and Cascadeur rejects that paste ("Import animation: failed" in the log).
        source = self.open_sample("Dracorex_walk_cycle.casc")
        source_info = client.call("get_scene_info")
        source_frames = source_info["frames"]
        source_size = source_frames["animation_size"]
        self.assertGreater(source_size, 1, f"{source['name']} has no animation: {source_frames!r}")
        source_key = self.last_key()
        self.assertIsNotNone(source_key, f"{source['name']} has no keyed animation: {source_frames!r}")

        fbx = self.export_path("dracorex_walk.fbx")
        exported = client.call("export_scene", path=fbx, timeout=300, http_timeout=330)
        self.assertTrue(exported["ok"], exported)

        takes = client.call("list_fbx_takes", path=fbx)
        self.assertTrue(takes["ok"], takes)
        self.assertGreaterEqual(takes["count"], 1, takes)

        self.open_sample("Dracorex.casc")
        before = client.call("get_scene_info")
        self.assertEqual(before["frames"]["animation_size"], 1, f"Dracorex.casc should have no keys: {before!r}")
        imported = client.call(
            "import_animation",
            path=fbx,
            take=takes["takes"][0],
            target="all",
            timeout=300,
            http_timeout=330,
        )
        self.assertTrue(imported["ok"], imported)
        self.assertTrue(imported["changed"], f"the take wrote nothing: {imported!r}")
        self.assertEqual(imported["take"], takes["takes"][0], imported)
        self.assertEqual(imported["undo_steps"], 1, imported)

        after = client.call("get_scene_info")
        self.assertEqual(imported["after"]["last_key"], source_key, f"keyed length differs: {imported!r}")
        self.assertEqual(
            after["frames"]["animation_size"],
            source_size,
            f"animation length differs: source {source_frames!r} vs imported {after['frames']!r}",
        )

    def test_animation_import_reports_a_native_failure(self) -> None:
        # Backflip (58 joints) -> Cascy (66 joints, 24 shared names) is Cascadeur's silent failure
        # path: the loader only writes "Import animation: failed" to the log and returns. The tool
        # must turn that into ok:false with the T28 hint instead of reporting a success.
        self.open_sample("Backflip_animation.casc")
        fbx = self.export_path("backflip.fbx")
        exported = client.call("export_scene", path=fbx, timeout=300, http_timeout=330)
        self.assertTrue(exported["ok"], exported)

        self.open_sample("Cascy.casc")
        self.assert_tool_error(
            "import_animation",
            "Import animation: failed",
            "retargeting (T28",
            path=fbx,
            target="all",
            timeout=300,
            http_timeout=330,
        )

    def test_export_refuses_existing_file_without_overwrite(self) -> None:
        self.open_sample("Cascy.casc")
        path = self.export_path("overwrite.fbx")
        first = client.call("export_scene", path=path, timeout=300, http_timeout=330)
        self.assertTrue(first["ok"], first)

        self.assert_tool_error("export_scene", "already exists", "overwrite=true", path=path, timeout=300)

        replaced = client.call("export_scene", path=path, overwrite=True, timeout=300, http_timeout=330)
        self.assertTrue(replaced["ok"], replaced)
        self.assertGreater(replaced["bytes"], 0, replaced)

    def test_usd_is_rejected_and_bvh_imports(self) -> None:
        self.open_sample("Cascy.casc")

        self.assert_tool_error(
            "export_scene",
            "USD export is not available",
            "no Python USD exporter",
            path=os.path.join(self.case_dir, "scene.usd"),
        )

        dummy = self.export_path("dummy.usdc")
        with open(dummy, "wb") as handle:
            handle.write(b"#usda 1.0\n")
        self.assert_tool_error(
            "import_model",
            "USD import is not available",
            "no Python USD importer",
            path=dummy,
        )

        probe = client.call("run_python", code=USD_PROBE_CODE)
        self.assertIn("csc_usd=False", probe["stdout"], probe)
        self.assertIn("usd_modules=[]", probe["stdout"], probe)
        self.assertIn("usd_in_tools=False", probe["stdout"], probe)

        joints_output = client.call("run_python", code=JOINTS_CODE)
        joints = [name for name in _print_value(joints_output, "__JOINTS__").split("|") if name]
        if len(joints) < 2:
            self.skipTest(f"Cascy.casc has no two named joints to build a BVH from: {joints!r}")
        bvh = self.export_path("synthetic.bvh")
        write_bvh(bvh, joints, frames=12)

        before = client.call("get_scene_info")["frames"]
        imported = client.call("import_animation", path=bvh, target="all", timeout=300, http_timeout=330)
        self.assertTrue(imported["ok"], imported)
        self.assertTrue(imported["changed"], f"BVH import changed nothing: {imported!r}")
        self.assertEqual(imported["undo_steps"], 1, imported)
        after = client.call("get_scene_info")["frames"]
        self.assertGreaterEqual(imported["after"]["last_key"], 11, f"BVH frames missing: {imported!r}")
        self.assertGreaterEqual(after["last"] - after["first"], 11, f"BVH frames missing: {before!r} -> {after!r}")


if __name__ == "__main__":
    unittest.main()
