"""Tests for the api_search/api_describe index (no Cascadeur needed).

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STUB = """\
class Scene:
    \"\"\"A view scene.\"\"\"

    def name(self) -> str:
        \"\"\"Return the scene name.\"\"\"
"""

_TOOLS = '''\
\"\"\"csc.tools\"\"\"


class RenderParameters:
    width: int
    height: int


class RenderToFile:
    """Render to file tool class."""

    def play_to_video_file(self, scene_view: csc.view.Scene, render_parameters: RenderParameters, file_name: str) -> None:
        """Render the scene to a video file."""

    def play_to_images_sequence(self, scene_view: csc.view.Scene, render_parameters: RenderParameters, folder_name: str) -> None:
        """Render the scene to an image sequence."""

    def take_image(self, scene_view: csc.view.Scene, render_parameters: RenderParameters, file_name: str) -> None:
        """Take one image."""


class VideoHelper:
    """Wraps play_to_video_file in a helper."""

    def run(self) -> None:
        """Run the helper."""
'''

_VIEW = '''\
class SphericalCameraStruct:
    """SphericalCameraStruct class.

    :ivar position: Get set Vector3f
    :ivar target: Get set Vector3f
    :ivar type: Get set CameraType
    """

    position: object
    target: object
    type: object

    def __init__(self) -> None:
        ...
'''

_MODEL = '''\
class DataId:
    ...


class DataEditor:
    def set_data_value(self, id: DataId, frame: int, value: object) -> None:
        """Set one data value at a frame."""
'''

_MOVE_JOINTS = """\
import csc


def move_joints(scene):
    de = scene.model_viewer().data_editor()
    de.set_data_value(1, 0, [1.0, 1.0, 1.0])
"""

_PYCSC = '''\
"""High-level wrappers."""


def wrap_scene(scene):
    """Wrap a scene for easier access."""
    return scene
'''

_COMMANDS = """\
def move():
    de = object()
    de.set_data_value(1, 0, [0.0, 0.0, 0.0])
"""


def load_api():
    """Load tools/api.py without importing the Cascadeur-only package __init__."""
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
    name = tools_package + ".api"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, package, "tools", "api.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


api = load_api()


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ApiToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        write(cls.root, "resources/scripts/stubs/csc/view/__init__.pyi", _STUB + _VIEW)
        write(cls.root, "resources/scripts/stubs/csc/tools/__init__.pyi", _TOOLS)
        write(cls.root, "resources/scripts/stubs/csc/model.pyi", _MODEL)
        write(cls.root, "resources/scripts/stubs/csc/extra.pyi", 'class Dup:\n    """First duplicate."""\n')
        write(cls.root, "resources/scripts/stubs/csc/second.pyi", 'class Dup:\n    """Second duplicate."""\n')
        write(cls.root, "resources/scripts/python/pycsc/__init__.py", _PYCSC)
        write(cls.root, "resources/scripts/python/samples/move_joints.py", _MOVE_JOINTS)
        write(cls.root, "resources/scripts/python/commands/demo.py", _COMMANDS)
        write(cls.root, "resources/scripts/python/scripts/demo.py", "VALUE = 1\n")
        write(cls.root, "resources/scripts/python/rig_mode/demo.py", "VALUE = 1\n")
        cls.old_dir = os.environ.get("CASCADEUR_DIR")
        os.environ["CASCADEUR_DIR"] = str(cls.root)
        api._clear_cache()

    @classmethod
    def tearDownClass(cls):
        if cls.old_dir is None:
            os.environ.pop("CASCADEUR_DIR", None)
        else:
            os.environ["CASCADEUR_DIR"] = cls.old_dir
        api._clear_cache()
        cls.temp.cleanup()

    def setUp(self):
        api._clear_cache()
        os.environ["CASCADEUR_DIR"] = str(self.root)

    def test_search_prefers_name_hit_over_docstring_hit(self):
        result = api.api_search("play_to_video_file")
        self.assertTrue(result["ok"])
        symbols = [item["symbol"] for item in result["results"]]
        self.assertEqual(symbols[0], "csc.tools.RenderToFile.play_to_video_file")
        self.assertIn("csc.tools.VideoHelper", symbols)

    def test_search_finds_stub_and_sample_usage(self):
        result = api.api_search("set_data_value")
        self.assertTrue(result["ok"])
        self.assertTrue(any(item["symbol"] == "csc.model.DataEditor.set_data_value" for item in result["results"]))
        self.assertTrue(any(Path(item["file"]).name == "move_joints.py" for item in result["results"]))
        self.assertTrue(any(item["source"] == "samples" for item in result["results"]))

    def test_search_docstring(self):
        result = api.api_search("take one image")
        self.assertTrue(result["ok"])
        self.assertTrue(any(item["symbol"] == "csc.tools.RenderToFile.take_image" for item in result["results"]))

    def test_search_sources_filter(self):
        stubs = api.api_search("wrap_scene", sources=["stubs"])
        self.assertEqual(stubs["results"], [])
        pycsc = api.api_search("wrap_scene", sources=["pycsc"])
        self.assertEqual([item["symbol"] for item in pycsc["results"]], ["pycsc.wrap_scene"])

    def test_search_limit_and_validation(self):
        result = api.api_search("value", limit=2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertGreaterEqual(result["total"], 2)
        self.assertFalse(api.api_search("value", limit=0)["ok"])
        self.assertFalse(api.api_search("")["ok"])
        self.assertIn("Unknown source group", api.api_search("value", sources=["nope"])["error"])

    def test_describe_class_returns_fields_methods_and_source(self):
        result = api.api_describe("csc.view.SphericalCameraStruct")
        self.assertTrue(result["ok"])
        self.assertEqual([field["name"] for field in result["fields"]], ["position", "target", "type"])
        self.assertIn("__init__", [method["name"] for method in result["methods"]])
        self.assertIn("class SphericalCameraStruct:", result["source"])
        self.assertIn(":ivar target:", result["docstring"])

    def test_describe_accepts_short_suffix(self):
        result = api.api_describe("RenderToFile")
        self.assertTrue(result["ok"])
        self.assertEqual(result["symbol"], "csc.tools.RenderToFile")
        self.assertEqual(result["kind"], "class")

    def test_describe_unknown_and_ambiguous(self):
        unknown = api.api_describe("NoSuchSymbol")
        self.assertFalse(unknown["ok"])
        self.assertIn("hint", unknown)
        ambiguous = api.api_describe("Dup")
        self.assertFalse(ambiguous["ok"])
        self.assertEqual(len(ambiguous["candidates"]), 2)

    def test_index_is_cached(self):
        first = api._get_index()
        second = api._get_index()
        self.assertIs(first, second)
        self.assertGreater(len(first.entries), 0)
        self.assertEqual(first.skipped, [])

    def test_bad_cascadeur_dir(self):
        with tempfile.TemporaryDirectory() as broken:
            os.environ["CASCADEUR_DIR"] = broken
            api._clear_cache()
            try:
                result = api.api_search("Scene")
                self.assertFalse(result["ok"])
                self.assertIn("CASCADEUR_DIR", result["error"])
            finally:
                os.environ["CASCADEUR_DIR"] = str(self.root)

    def test_runtime_only_members_are_listed(self):
        _, definitions, _ = api._find_definitions(api._get_index(), "csc.view.SphericalCameraStruct")
        entry = definitions[0]

        class FakeCamera:
            position = None
            target = None
            type = None

            def __init__(self):
                pass

            def extra_member(self):
                pass

        csc_module = types.ModuleType("csc")
        view_module = types.ModuleType("csc.view")
        view_module.SphericalCameraStruct = FakeCamera
        csc_module.view = view_module
        saved = {name: sys.modules.get(name) for name in ("csc", "csc.view")}
        sys.modules["csc"] = csc_module
        sys.modules["csc.view"] = view_module
        try:
            info = api._runtime_info(entry)
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        self.assertTrue(info["available"])
        self.assertIn("extra_member", info["missing_from_stub"])


if __name__ == "__main__":
    unittest.main()
