"""Unit tests for the import/export tool module (no Cascadeur needed).

The module under test imports Cascadeur and common.py lazily, so plain Python can load it and
exercise every argument/path check. Calls that would reach Cascadeur's native loaders are left
to tests/live/test_import_export.py.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_NAME = "cascadeur_mcp_plus.tools.import_export"


def load_module():
    """Load tools/import_export.py without importing the Cascadeur-only package __init__."""
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
    spec = importlib.util.spec_from_file_location(MODULE_NAME, os.path.join(ROOT, package, "tools", "import_export.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


ie = load_module()
ToolError = sys.modules["cascadeur_mcp_plus.errors"].ToolError


class FakeCommon:
    """Stands in for cascadeur_mcp_plus.common, which needs csc and numpy to import."""

    def __init__(self, existing=()):
        self.existing = {os.path.normpath(path) for path in existing}
        self.output_calls = []
        self.failures = []

    def output_path(self, path, *, overwrite=False, extensions=None):
        self.output_calls.append({"path": path, "overwrite": overwrite, "extensions": extensions})
        allowed = [ext.lower() for ext in (extensions or [])]
        if allowed and os.path.splitext(path)[1].lower() not in allowed:
            raise ToolError(f"Output path must end with {' or '.join(allowed)} (got {path!r}).")
        if os.path.normpath(path) in self.existing and overwrite is not True:
            raise ToolError(f"File already exists: {path!r}. Pass overwrite=true to replace it.")
        return os.path.normpath(path)

    def fail(self, error, hint=None, **data):
        self.failures.append({"error": str(error), "hint": hint, "data": data})
        result = {"ok": False, "error": str(error)}
        if hint:
            result["hint"] = str(hint)
        result.update(data)
        return result

    def ok(self, **data):
        return {"ok": True, **data}

    def log_tail(self, since=None, contains=None, lines=40):
        return []


class ImportExportTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    def touch(self, name, content=b"x"):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(content)
        return path

    def patch_common(self, fake=None):
        fake = fake or FakeCommon()
        self.addCleanup(setattr, ie, "_common", ie._common)
        ie._common = lambda: fake
        return fake


class ToolMetadataTests(unittest.TestCase):
    def test_tools_are_registered_with_fn_and_schema(self):
        names = [tool["name"] for tool in ie.TOOLS]
        self.assertEqual(names, ["import_model", "import_animation", "list_fbx_takes", "export_scene"])
        for tool in ie.TOOLS:
            self.assertIn("fn", tool)
            self.assertNotIn("handler", tool)
            self.assertGreater(len(tool["description"]), 80, tool["name"])
            self.assertIn("path", tool["inputSchema"]["required"])
            self.assertEqual(tool["inputSchema"]["type"], "object")
        by_name = {tool["name"]: tool for tool in ie.TOOLS}
        self.assertTrue(by_name["import_model"]["idle"])
        self.assertTrue(by_name["import_animation"]["idle"])
        self.assertNotIn("idle", by_name["export_scene"])
        self.assertTrue(by_name["export_scene"]["timeout_arg"])


class FormatTests(ImportExportTestCase):
    def test_model_format_by_extension(self):
        self.assertEqual(
            ie.model_format(self.touch("character.fbx")), (os.path.join(self.root, "character.fbx"), "fbx")
        )
        self.assertEqual(
            ie.model_format(self.touch("character.GLB")), (os.path.join(self.root, "character.GLB"), "glb")
        )
        self.assertEqual(
            ie.model_format(self.touch("character.gltf")), (os.path.join(self.root, "character.gltf"), "glb")
        )

    def test_model_format_rejects_usd_with_evidence_hint(self):
        path = self.touch("character.usda")
        with self.assertRaises(ie._ArgumentError) as caught:
            ie.model_format(path)
        self.assertIn("USD", str(caught.exception))
        self.assertIn("no Python USD importer", caught.exception.hint)

    def test_model_format_rejects_unknown_extension(self):
        with self.assertRaises(ie._ArgumentError) as caught:
            ie.model_format(self.touch("character.obj"))
        self.assertIn("Unsupported model file", str(caught.exception))
        self.assertIn(".fbx", caught.exception.hint)

    def test_import_paths_must_be_absolute_and_exist(self):
        with self.assertRaises(ie._ArgumentError) as caught:
            ie.model_format("character.fbx")
        self.assertIn("absolute", str(caught.exception))
        with self.assertRaises(ie._ArgumentError) as caught:
            ie.model_format(os.path.join(self.root, "missing.fbx"))
        self.assertIn("not found", str(caught.exception))

    def test_animation_format_by_extension(self):
        self.assertEqual(ie.animation_format(self.touch("take.FBX"))[1], "fbx")
        self.assertEqual(ie.animation_format(self.touch("take.bvh"))[1], "bvh")

    def test_animation_format_rejects_usd_and_models(self):
        with self.assertRaises(ie._ArgumentError) as caught:
            ie.animation_format(self.touch("take.usdc"))
        self.assertIn("USD", str(caught.exception))
        with self.assertRaises(ie._ArgumentError) as caught:
            ie.animation_format(self.touch("take.glb"))
        self.assertEqual(caught.exception.hint, "import_animation accepts .fbx, .bvh files.")

    def test_export_format_from_extension(self):
        self.assertEqual(ie.export_format("C:/out/take.fbx"), "fbx")
        self.assertEqual(ie.export_format("C:/out/take.FBX"), "fbx")
        self.assertEqual(ie.export_format("C:/out/take.glb"), "glb")
        self.assertEqual(ie.export_format("C:/out/take.gltf"), "glb")

    def test_export_format_rejects_usd_and_unknown(self):
        with self.assertRaises(ie._ArgumentError) as caught:
            ie.export_format("C:/out/take.usdz")
        self.assertIn("USD export is not available", str(caught.exception))
        self.assertIn("no Python USD exporter", caught.exception.hint)
        with self.assertRaises(ie._ArgumentError) as caught:
            ie.export_format("C:/out/take.txt")
        self.assertIn("Cannot tell the export format", str(caught.exception))

    def test_export_format_argument_must_match_extension(self):
        self.assertEqual(ie.export_format("C:/out/take.fbx", "fbx"), "fbx")
        self.assertEqual(ie.export_format("C:/out/take.glb", "GLB"), "glb")
        with self.assertRaises(ie._ArgumentError) as caught:
            ie.export_format("C:/out/take.fbx", "glb")
        self.assertIn("does not match", str(caught.exception))
        with self.assertRaises(ie._ArgumentError) as caught:
            ie.export_format("C:/out/take.fbx", "usd")
        self.assertIn("USD export is not available", str(caught.exception))
        with self.assertRaises(ie._ArgumentError) as caught:
            ie.export_format("C:/out/take.fbx", "dae")
        self.assertIn("Unknown format", str(caught.exception))

    def test_cascadeur_path_uses_forward_slashes(self):
        path = ie.cascadeur_path(os.path.join(self.root, "a", "b.fbx"))
        self.assertNotIn("\\", path)
        self.assertTrue(path.endswith("/a/b.fbx"))


class ExportTargetTests(ImportExportTestCase):
    def test_method_per_objects_and_frames(self):
        fake = FakeCommon()
        cases = {
            ("all", "all"): "export_all_objects",
            ("selected", "all"): "export_scene_selected_objects",
            ("all", "selected"): "export_scene_selected_frames",
            ("selected", "selected"): "export_scene_selected",
        }
        for (objects, frames), method in cases.items():
            target, fmt, _, _, actual = ie.export_target(fake, "C:/out/take.fbx", None, objects, frames, False)
            self.assertEqual((fmt, actual), ("fbx", method))
            self.assertEqual(target, os.path.normpath("C:/out/take.fbx"))

    def test_output_path_gets_extension_and_overwrite_flag(self):
        fake = FakeCommon()
        ie.export_target(fake, "C:/out/take.glb", None, "all", "all", True)
        self.assertEqual(fake.output_calls[-1]["extensions"], [".glb"])
        self.assertIs(fake.output_calls[-1]["overwrite"], True)
        ie.export_target(fake, "C:/out/take.fbx", None, "all", "all", False)
        self.assertEqual(fake.output_calls[-1]["extensions"], [".fbx"])
        self.assertIs(fake.output_calls[-1]["overwrite"], False)

    def test_existing_file_is_rejected_without_overwrite(self):
        existing = self.touch("done.fbx")
        fake = FakeCommon(existing=[existing])
        with self.assertRaises(ToolError) as caught:
            ie.export_target(fake, existing, None, "all", "all", False)
        self.assertIn("already exists", str(caught.exception))
        result = ie.export_target(fake, existing, None, "all", "all", True)
        self.assertEqual(result[0], os.path.normpath(existing))

    def test_objects_and_frames_values_are_validated(self):
        fake = FakeCommon()
        with self.assertRaises(ie._ArgumentError) as caught:
            ie.export_target(fake, "C:/out/take.fbx", None, "everything", "all", False)
        self.assertIn("Unknown objects", str(caught.exception))
        with self.assertRaises(ie._ArgumentError) as caught:
            ie.export_target(fake, "C:/out/take.fbx", None, "all", 3, False)
        self.assertIn("frames must be one of", str(caught.exception))
        self.assertEqual(fake.output_calls, [])


class TakeTests(unittest.TestCase):
    def test_none_selects_the_loader_default(self):
        self.assertEqual(ie.take_index([], None), -1)
        self.assertEqual(ie.take_index(["Take 001"], None), -1)

    def test_name_and_index(self):
        takes = ["Take 001", "Run"]
        self.assertEqual(ie.take_index(takes, "Take 001"), 0)
        self.assertEqual(ie.take_index(takes, " run "), 1)
        self.assertEqual(ie.take_index(takes, 1), 1)

    def test_bad_take_name_lists_available_takes(self):
        with self.assertRaises(ie._ArgumentError) as caught:
            ie.take_index(["Take 001"], "Walk")
        self.assertIn("was not found", str(caught.exception))
        self.assertIn("Take 001", caught.exception.hint)

    def test_bad_take_index_and_bool(self):
        with self.assertRaises(ie._ArgumentError) as caught:
            ie.take_index(["Take 001"], 3)
        self.assertIn("out of range", str(caught.exception))
        with self.assertRaises(ie._ArgumentError) as caught:
            ie.take_index(["Take 001"], True)
        self.assertIn("must be a name or an index", str(caught.exception))


class PublicFunctionTests(ImportExportTestCase):
    def test_import_model_reports_a_missing_file_without_touching_cascadeur(self):
        fake = self.patch_common()
        result = ie.import_model(os.path.join(self.root, "missing.fbx"))
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"])
        self.assertTrue(result["hint"])
        self.assertEqual(fake.failures[0]["error"], result["error"])

    def test_import_model_rejects_usd(self):
        self.patch_common()
        result = ie.import_model(self.touch("hero.usdz"))
        self.assertFalse(result["ok"])
        self.assertIn("USD import is not available", result["error"])
        self.assertIn("no Python USD importer", result["hint"])

    def test_import_animation_validates_target_before_cascadeur(self):
        self.patch_common()
        result = ie.import_animation(self.touch("take.fbx"), target="everything")
        self.assertFalse(result["ok"])
        self.assertIn("Unknown target", result["error"])

    def test_bvh_only_supports_all_joints(self):
        self.patch_common()
        result = ie.import_animation(self.touch("take.bvh"), target="selected_objects")
        self.assertFalse(result["ok"])
        self.assertIn("only be applied to all joints", result["error"])

    def test_export_scene_rejects_existing_file_without_overwrite(self):
        existing = self.touch("scene.fbx")
        self.patch_common(FakeCommon(existing=[existing]))
        result = ie.export_scene(existing)
        self.assertFalse(result["ok"])
        self.assertIn("already exists", result["error"])
        self.assertIn("overwrite=true", result["error"])

    def test_export_scene_rejects_usd_and_unknown_objects(self):
        self.patch_common()
        result = ie.export_scene(os.path.join(self.root, "scene.usd"))
        self.assertFalse(result["ok"])
        self.assertIn("USD export is not available", result["error"])
        result = ie.export_scene(os.path.join(self.root, "scene.fbx"), objects="some")
        self.assertFalse(result["ok"])
        self.assertIn("Unknown objects", result["error"])

    def test_list_fbx_takes_rejects_non_fbx(self):
        self.patch_common()
        result = ie.list_fbx_takes(self.touch("scene.glb"))
        self.assertFalse(result["ok"])
        self.assertIn(".fbx files only", result["error"])


if __name__ == "__main__":
    unittest.main()
