"""Unit tests for the scene-file tools (no Cascadeur needed).

The module under test imports csc, numpy (through common.py) and PySide6 (lazily through ui).
These tests install small stand-ins for those modules, then exercise the real common.py helpers
and the real scene_files.py functions against fake SceneManager / DataSourceManager objects.

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
PACKAGE = "cascadeur_mcp_plus"
TOOLS_PACKAGE = f"{PACKAGE}.tools"


# ---------------------------------------------------------------------------
# Import the real modules behind small csc/numpy/ui stand-ins
# ---------------------------------------------------------------------------


def _package(name: str, path: str) -> types.ModuleType:
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        module.__path__ = [path]
        sys.modules[name] = module
    return module


def _load_real(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _Runtime:
    app = None


def _install_stand_ins() -> None:
    _package(PACKAGE, os.path.join(ROOT, PACKAGE))
    _package(TOOLS_PACKAGE, os.path.join(ROOT, PACKAGE, "tools"))

    try:
        import numpy  # noqa: F401
    except ImportError:
        numpy = types.ModuleType("numpy")

        class _Array:
            pass

        class _Generic:
            pass

        numpy.ndarray = _Array
        numpy.generic = _Generic
        sys.modules["numpy"] = numpy

    if "csc" not in sys.modules:
        csc = types.ModuleType("csc")
        csc.app = types.ModuleType("csc.app")
        csc.app.get_application = lambda: _Runtime.app
        csc.model = types.ModuleType("csc.model")
        csc.math = types.ModuleType("csc.math")
        sys.modules["csc"] = csc


_install_stand_ins()
_load_real(f"{PACKAGE}.errors", os.path.join(ROOT, PACKAGE, "errors.py"))
_load_real(f"{TOOLS_PACKAGE}._schema", os.path.join(ROOT, PACKAGE, "tools", "_schema.py"))
_load_real(f"{PACKAGE}.common", os.path.join(ROOT, PACKAGE, "common.py"))
scene_files = _load_real(
    f"{TOOLS_PACKAGE}.scene_files",
    os.path.join(ROOT, PACKAGE, "tools", "scene_files.py"),
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeDomain:
    pass


class FakeScene:
    def __init__(self, name: str, path: str = "", modified=None, flag: str = "modified") -> None:
        self._name = name
        self._path = path
        self._modified = modified
        self.save_calls: list[str] = []
        self.save_result = True
        self.save_error: Exception | None = None
        self._domain = FakeDomain()
        if flag and modified is not None:
            if flag == "method":
                self.is_modified = lambda: self._modified
            else:
                setattr(self, flag, modified if isinstance(modified, bool) else modified)

    def name(self) -> str:
        return self._name

    def get_path_name(self) -> str:
        return self._path

    def domain_scene(self):
        return self._domain

    def save(self, path: str):
        self.save_calls.append(path)
        if self.save_error is not None:
            raise self.save_error
        if self.save_result is False:
            return False
        self._path = path
        self._name = os.path.basename(path)
        if self._modified is not None:
            self._modified = False
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("fake casc", encoding="utf-8")
        return self.save_result


class FakeSceneManager:
    def __init__(self, app: FakeApp) -> None:
        self.app = app

    def _touch(self, name: str) -> None:
        if self.app.after_close:
            self.app.manager_calls_after_close.append(name)

    def scenes(self):
        self._touch("scenes")
        return list(self.app.scenes)

    def current_scene(self):
        self._touch("current_scene")
        return self.app.current

    def create_application_scene(self):
        self._touch("create_application_scene")
        self.app.created += 1
        scene = FakeScene(f"untitled-{self.app.created}.casc", "", modified=None, flag="")
        self.app.scenes.append(scene)
        return scene

    def set_current_scene(self, scene):
        self._touch("set_current_scene")
        self.app.current = scene
        self.app.switched.append(scene)

    def remove_application_scene(self, scene):  # pragma: no cover - must never be called
        self._touch("remove_application_scene")
        raise AssertionError("scene_files must never call remove_application_scene")


def _close_dialog(app: FakeApp) -> dict:
    return {
        "kind": "dialog",
        "window": 0,
        "type": "Package::ClosingSceneDialog",
        "texts": ["Closing scene", "Do you want to save changes to " + app.current.name() + "?"],
        "buttons": [
            {"ref": "d0:0", "text": "Save"},
            {"ref": "d0:1", "text": "Save as..."},
            {"ref": "d0:2", "text": "Don't Save"},
            {"ref": "d0:3", "text": "Cancel"},
        ],
    }


class FakeDataSourceManager:
    def __init__(self, app: FakeApp) -> None:
        self.app = app
        self.load_calls: list[str] = []
        self.close_calls: list[FakeScene] = []
        self.load_result = True
        self.close_error: Exception | None = None

    def load_scene(self, path: str):
        self.load_calls.append(path)
        if self.load_result is False:
            return False
        self.app.current._name = os.path.basename(path)
        self.app.current._path = path
        return True

    def close_scene(self, scene):
        self.close_calls.append(scene)
        if self.close_error is not None:
            raise self.close_error
        if scene._modified is True and self.app.prompt_on_modified:
            self.app.dialog = _close_dialog(self.app)
            self.app.pending_close = scene
            return None
        if scene in self.app.scenes:
            self.app.scenes.remove(scene)
        self.app.after_close = True
        return None


class FakeApp:
    def __init__(self, scenes: list[FakeScene], current: FakeScene) -> None:
        self.scenes = list(scenes)
        self.current = current
        self.created = 0
        self.switched: list[FakeScene] = []
        self.after_close = False
        self.manager_calls_after_close: list[str] = []
        self.dialog = None
        self.pending_close = None
        self.prompt_on_modified = True
        self.sm = FakeSceneManager(self)
        self.dsm = FakeDataSourceManager(self)

    def get_scene_manager(self):
        return self.sm

    def get_data_source_manager(self):
        return self.dsm

    def current_scene(self):
        return self.current


def _fake_click(ref=None, **kwargs):
    app = _Runtime.app
    dialog = app.dialog
    if dialog is None:
        raise AssertionError("ui.click called with no dialog open")
    button = next((item for item in dialog["buttons"] if item["ref"] == ref), None)
    if button is None:
        raise AssertionError(f"unknown dialog ref {ref!r}")
    if button["text"] == "Don't Save":
        if app.pending_close in app.scenes:
            app.scenes.remove(app.pending_close)
        app.after_close = True
    elif button["text"] != "Cancel":
        raise AssertionError(f"test refused to press {button['text']!r}")
    app.dialog = None
    app.pending_close = None
    return {"clicked": {"text": button["text"]}}


def _fake_dialogs():
    app = _Runtime.app
    return {"dialogs": [app.dialog] if app.dialog else []}


ui = types.ModuleType(f"{PACKAGE}.ui")
ui.click = _fake_click
ui.dialogs = _fake_dialogs
sys.modules[ui.__name__] = ui


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class SceneFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.temp.name) / "out"
        self.out_dir.mkdir()
        self.user = FakeScene("untitled.casc", "", modified=None, flag="")
        self.app = FakeApp([self.user], self.user)
        _Runtime.app = self.app
        self.sf = scene_files

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _add_scene(self, name: str, path: str = "", modified=None, flag: str = "modified") -> FakeScene:
        scene = FakeScene(name, path, modified=modified, flag=flag)
        self.app.scenes.append(scene)
        return scene

    # -- new / open ---------------------------------------------------------

    def test_new_scene_creates_and_switches(self):
        result = self.sf.new_scene()
        self.assertTrue(result["ok"])
        self.assertEqual(result["scene"]["index"], 1)
        self.assertEqual(result["scene"]["name"], "untitled-1.casc")
        self.assertTrue(result["scene"]["current"])
        self.assertIs(self.app.current, self.app.scenes[1])

    def test_open_scene_rejects_missing_relative_and_wrong_extension(self):
        relative = self.sf.open_scene("Cascy.casc")
        self.assertFalse(relative["ok"])
        self.assertIn("absolute", relative["error"])
        missing = self.sf.open_scene(str(self.out_dir / "missing.casc"))
        self.assertFalse(missing["ok"])
        self.assertIn("does not exist", missing["error"])
        wrong = self.out_dir / "file.fbx"
        wrong.write_text("x", encoding="utf-8")
        extension = self.sf.open_scene(str(wrong))
        self.assertFalse(extension["ok"])
        self.assertIn(".casc", extension["error"])
        self.assertEqual(self.app.created, 0)

    def test_open_scene_loads_into_a_new_current_tab(self):
        sample = self.out_dir / "Cascy.casc"
        sample.write_text("fake casc", encoding="utf-8")
        result = self.sf.open_scene(str(sample))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["scene"]["index"], 1)
        self.assertEqual(result["scene"]["path"], str(sample))
        self.assertEqual(result["scene"]["name"], "Cascy.casc")
        self.assertTrue(result["scene"]["current"])
        self.assertEqual(self.app.dsm.load_calls, [str(sample)])
        self.assertIs(self.app.current, self.app.scenes[1])

    def test_open_scene_load_failure_closes_the_empty_tab_only(self):
        sample = self.out_dir / "bad.casc"
        sample.write_text("fake casc", encoding="utf-8")
        self.app.dsm.load_result = False
        result = self.sf.open_scene(str(sample))
        self.assertFalse(result["ok"])
        self.assertEqual(self.app.dsm.load_calls, [str(sample)])
        self.assertEqual(len(self.app.dsm.close_calls), 1)
        self.assertEqual(self.app.manager_calls_after_close, [])
        self.assertIs(self.app.current, self.user)

    # -- save ---------------------------------------------------------------

    def test_save_scene_to_new_path(self):
        target = self.out_dir / "saved.casc"
        scene = self.app.current
        scene._modified = True
        result = self.sf.save_scene(path=str(target))
        self.assertTrue(result["ok"], result)
        self.assertTrue(target.is_file())
        self.assertEqual(scene.save_calls, [str(target)])
        self.assertEqual(result["saved_to"], str(target))
        self.assertFalse(result["overwritten"])
        self.assertEqual(scene.get_path_name(), str(target))

    def test_save_scene_refuses_existing_path_without_overwrite(self):
        target = self.out_dir / "existing.casc"
        target.write_text("old", encoding="utf-8")
        result = self.sf.save_scene(path=str(target))
        self.assertFalse(result["ok"])
        self.assertIn("overwrite=true", result["error"])
        self.assertEqual(self.app.current.save_calls, [])

    def test_save_scene_overwrites_with_flag(self):
        target = self.out_dir / "existing.casc"
        target.write_text("old", encoding="utf-8")
        result = self.sf.save_scene(path=str(target), overwrite=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["overwritten"])
        self.assertEqual(self.app.current.save_calls, [str(target)])

    def test_save_scene_current_file_requires_overwrite(self):
        current = self.out_dir / "current.casc"
        current.write_text("old", encoding="utf-8")
        scene = self._add_scene("current.casc", str(current), modified=False)
        self.app.current = scene
        result = self.sf.save_scene()
        self.assertFalse(result["ok"])
        self.assertIn("overwrite=true", result["hint"])
        self.assertIn("path=", result["hint"])
        self.assertEqual(scene.save_calls, [])
        saved = self.sf.save_scene(overwrite=True)
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(scene.save_calls, [str(current)])

    def test_save_scene_never_saved_needs_a_path(self):
        result = self.sf.save_scene()
        self.assertFalse(result["ok"])
        self.assertIn("never been saved", result["error"])
        self.assertIn("path=", result["hint"])

    def test_save_scene_reports_cascadeur_failure(self):
        target = self.out_dir / "failed.casc"
        self.app.current.save_result = False
        result = self.sf.save_scene(path=str(target))
        self.assertFalse(result["ok"])
        self.assertIn("did not save", result["error"])

    # -- list / switch ------------------------------------------------------

    def test_list_open_scenes_marks_current_and_modified(self):
        other = self._add_scene("other.casc", str(self.out_dir / "other.casc"), modified=True)
        third = self._add_scene("third.casc", "", modified=None, flag="")
        self.app.current = other
        result = self.sf.list_open_scenes()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["current"], 1)
        self.assertFalse(result["scenes"][0]["current"])
        self.assertIsNone(result["scenes"][0]["modified"])
        self.assertTrue(result["scenes"][1]["current"])
        self.assertTrue(result["scenes"][1]["modified"])
        self.assertIsNone(result["scenes"][2]["modified"])
        self.assertEqual(result["scenes"][2]["name"], third.name())

    def test_switch_scene_by_index_and_name(self):
        first = self.user
        second = self._add_scene("second.casc", str(self.out_dir / "second.casc"), modified=False)
        by_index = self.sf.switch_scene(1)
        self.assertTrue(by_index["ok"], by_index)
        self.assertIs(self.app.current, second)
        self.assertTrue(by_index["scene"]["current"])
        by_name = self.sf.switch_scene("untitled.casc")
        self.assertTrue(by_name["ok"], by_name)
        self.assertIs(self.app.current, first)
        self.assertFalse(by_name["was_current"])
        # Cascadeur reports paths with forward slashes; callers often pass backslashes.
        path = str(second.get_path_name())
        by_other_path = self.sf.switch_scene(path.replace("\\", "/").upper())
        self.assertTrue(by_other_path["ok"], by_other_path)
        self.assertIs(self.app.current, second)

    def test_switch_scene_reports_bad_index_name_and_duplicates(self):
        self._add_scene("dup.casc")
        self._add_scene("dup.casc")
        bad_index = self.sf.switch_scene(9)
        self.assertFalse(bad_index["ok"])
        self.assertIn("out of range", bad_index["error"])
        missing = self.sf.switch_scene("nope.casc")
        self.assertFalse(missing["ok"])
        self.assertIn("No open scene", missing["error"])
        duplicate = self.sf.switch_scene("dup.casc")
        self.assertFalse(duplicate["ok"])
        self.assertIn("Pass the index", duplicate["error"])

    # -- close --------------------------------------------------------------

    def test_close_scene_unmodified_uses_close_scene_not_remove(self):
        target = self._add_scene("second.casc", str(self.out_dir / "second.casc"), modified=False)
        result = self.sf.close_scene(target.name())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["closed"]["name"], "second.casc")
        self.assertFalse(result["discarded"])
        self.assertEqual(self.app.dsm.close_calls, [target])
        self.assertNotIn(target, self.app.scenes)
        self.assertEqual(self.app.manager_calls_after_close, [])

    def test_close_scene_modified_without_discard_is_refused_without_closing(self):
        target = self._add_scene("modified.casc", str(self.out_dir / "modified.casc"), modified=True)
        result = self.sf.close_scene(target.name())
        self.assertFalse(result["ok"])
        self.assertIn("unsaved changes", result["error"])
        self.assertEqual(self.app.dsm.close_calls, [])
        self.assertIn(target, self.app.scenes)

    def test_close_scene_discard_answers_dont_save(self):
        target = self._add_scene("modified.casc", str(self.out_dir / "modified.casc"), modified=True)
        result = self.sf.close_scene(target.name(), discard_changes=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["discarded"])
        self.assertIsNone(self.app.dialog)
        self.assertNotIn(target, self.app.scenes)
        self.assertEqual(self.app.manager_calls_after_close, [])

    def test_close_scene_unknown_flag_cancels_prompt_and_keeps_scene(self):
        target = self._add_scene("unknown.casc", str(self.out_dir / "unknown.casc"), modified=None, flag="")
        # The fake close prompt only appears when the flag is True, so make the fake see a
        # modified scene while the tool's probe cannot read a flag.
        target._modified = True
        result = self.sf.close_scene(target.name())
        self.assertFalse(result["ok"])
        self.assertIn("unsaved changes", result["error"])
        self.assertIsNone(self.app.dialog)
        self.assertIn(target, self.app.scenes)
        self.assertEqual(self.app.manager_calls_after_close, [])

    def test_close_scene_unknown_discard_answers_dont_save(self):
        target = self._add_scene("unknown.casc", str(self.out_dir / "unknown.casc"), modified=None, flag="")
        target._modified = True
        result = self.sf.close_scene(target.name(), discard_changes=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["discarded"])
        self.assertIsNone(self.app.dialog)
        self.assertNotIn(target, self.app.scenes)
        self.assertEqual(self.app.manager_calls_after_close, [])

    def test_close_scene_unknown_unmodified_closes_normally(self):
        self._add_scene("clean.casc", str(self.out_dir / "clean.casc"), modified=None, flag="")
        result = self.sf.close_scene(0)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["discarded"])
        self.assertIsNone(result["prompt"])
        self.assertNotIn(self.user, self.app.scenes)
        self.assertEqual(self.app.manager_calls_after_close, [])

    # -- registry -----------------------------------------------------------

    def test_tools_registry(self):
        names = [tool["name"] for tool in self.sf.TOOLS]
        self.assertEqual(
            names,
            ["new_scene", "open_scene", "save_scene", "list_open_scenes", "switch_scene", "close_scene"],
        )
        for tool in self.sf.TOOLS:
            self.assertIn("description", tool)
            self.assertIn("inputSchema", tool)
            self.assertTrue(callable(tool["fn"]))


if __name__ == "__main__":
    unittest.main()
