"""Unit tests for the video-mocap tool (no Cascadeur needed).

cascadeur_mcp_plus/tools/mocap.py imports common/rig/timeline, so this module installs fake
common and rig modules, the real (pure-Python) timeline helper and a fake UI surface before
loading the tool module. The pure helpers are tested for real; the begin/poll/cancel workflow is
driven through every phase with fake Load video / Mocap dialogs, fake native file and folder
pickers and a fake scene, so the T03 job contract is checked without Qt.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "cascadeur_mcp_plus"


def _package(name: str, path: str) -> types.ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    module = types.ModuleType(name)
    module.__path__ = [path]
    sys.modules[name] = module
    return module


def _load(name: str, filename: str):
    full = PACKAGE + "." + name
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, os.path.join(ROOT, PACKAGE, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


_package(PACKAGE, os.path.join(ROOT, PACKAGE))
errors = _load("errors", "errors.py")
timeline = None
mocap = None
REAL_CHECK_MODEL = None
_REPLACED = {}


# ---------------------------------------------------------------------------
# Fake Cascadeur side
# ---------------------------------------------------------------------------


class FakeObjectId(str):
    """A hashable ObjectId stand-in with Cascadeur's is_null()."""

    def is_null(self) -> bool:
        return len(self) == 0


class FakeLayersViewer:
    def __init__(self):
        self.layers = {}
        self.mapping = {}

    def add_layer(self, layer_id, keys, objects=()):
        self.layers[layer_id] = FakeLayer(keys)
        for object_id in objects:
            self.mapping[object_id] = layer_id

    def all_layer_ids(self):
        return list(self.layers)

    def layer(self, layer_id):
        return self.layers[layer_id]

    def layer_id_by_obj_id_or_null(self, object_id):
        return self.mapping.get(object_id, FakeObjectId(""))


class FakeLayer:
    def __init__(self, keys):
        self._keys = list(keys)

    def key_frame_indices(self):
        return list(self._keys)


class FakeFramesInterval:
    def __init__(self, first, last):
        self.first = first
        self.last = last


class FakeLayersSelector:
    def __init__(self):
        self.ids = ["layer1"]
        self.first = 0
        self.last = 10
        self.calls = []

    def all_included_layer_ids(self):
        return list(self.ids)

    def selection(self):
        return SimpleNamespace(frames_interval=lambda: FakeFramesInterval(self.first, self.last))

    def set_full_selection_by_parts(self, ids, first, last):
        self.calls.append((list(ids), first, last))
        self.ids = list(ids)
        self.first, self.last = first, last


class FakeSelection:
    def __init__(self, ids):
        self.ordered_ids = list(ids)
        self.ids = set(ids)


class FakeSelector:
    def __init__(self, ids=("old",)):
        self.ids = list(ids)
        self.calls = []

    def selected(self):
        return FakeSelection(self.ids)

    def select(self, ids, pivot=None):
        self.ids = list(ids)
        self.calls.append((set(ids), pivot))


class FakeModelViewer:
    def __init__(self):
        self.objects = []

    def add(self, object_id, name, type_name="Object"):
        self.objects.append((FakeObjectId(object_id), name, type_name))

    def get_objects(self, name=None):
        if name is None:
            return [row[0] for row in self.objects]
        return [row[0] for row in self.objects if row[1] == name]

    def get_object_name(self, object_id):
        for row in self.objects:
            if row[0] == object_id:
                return row[1]
        return ""

    def get_object_type_name(self, object_id):
        for row in self.objects:
            if row[0] == object_id:
                return row[2]
        return ""


class FakeScene:
    def __init__(self):
        self.model_viewer_value = FakeModelViewer()
        self.viewer = FakeLayersViewer()
        self.selector_value = FakeSelector()
        self.layers_selector = FakeLayersSelector()
        self.frame = 3
        self.frames = []

    def model_viewer(self):
        return self.model_viewer_value

    def layers_viewer(self):
        return self.viewer

    def selector(self):
        return self.selector_value

    def get_layers_selector(self):
        return self.layers_selector

    def get_current_frame(self, clamp_animation=True):
        return self.frame

    def set_current_frame(self, frame):
        self.frames.append(frame)
        self.frame = frame


class FakeModelEditor:
    def __init__(self):
        self.deleted = []

    def delete_objects(self, ids):
        self.deleted.extend(sorted(str(item) for item in ids))


class FakeSceneUpdater:
    def __init__(self):
        self.generated = 0
        self.ran = []

    def generate_update(self):
        self.generated += 1

    def run_update(self, ids, frame):
        self.ran.append((set(ids), frame))


class FakeActionManager:
    def __init__(self):
        self.calls = []
        self.fail_keys = set()

    def call_action(self, key):
        if key in self.fail_keys:
            raise RuntimeError("no such action: " + key)
        self.calls.append(key)


SCENE = FakeScene()
MODEL_EDITOR = FakeModelEditor()
SCENE_UPDATER = FakeSceneUpdater()
ACTION_MANAGER = FakeActionManager()
APP = SimpleNamespace(get_action_manager=lambda: ACTION_MANAGER)
VIEW = SimpleNamespace()
CHARACTERS = []
DIALOGS = []
NATIVE = []
CLICKS = []
NATIVE_TEXTS = []
NATIVE_OK = []
NATIVE_CANCEL = []
CLOSES = []


class _Clock:
    def __init__(self):
        self.value = 1000.0

    def monotonic(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


CLOCK = _Clock()


def _fail(error, hint=None, **data):
    result = {"ok": False, "error": str(error)}
    if hint:
        result["hint"] = str(hint)
    result.update(data)
    return result


def fake_edit(name, fn, update=True):
    fn(MODEL_EDITOR, object(), SCENE_UPDATER)
    return {"ok": True, "undo": "MCP: " + name}


def fake_temp_dir(*parts):
    path = os.path.join(tempfile.gettempdir(), "T26-unit-frames", *[str(part) for part in parts])
    os.makedirs(path, exist_ok=True)
    return path


def setUpModule():
    """Install the fake package modules and load the tool module for this test module only."""
    global mocap, timeline, REAL_CHECK_MODEL, VIDEO_PATH
    for name in (
        PACKAGE + ".common",
        PACKAGE + ".rig",
        PACKAGE + ".timeline",
        PACKAGE + ".tools.mocap",
    ):
        _REPLACED[name] = sys.modules.pop(name, None)
    timeline = _load("timeline", "timeline.py")

    fake_common = types.ModuleType(PACKAGE + ".common")
    fake_common.ToolError = errors.ToolError
    fake_common.LOG_PATH = os.path.join(tempfile.gettempdir(), "cascadeur-mcp-plus-T26-no-such-log.log")
    fake_common.ok = lambda **data: {"ok": True, **data}
    fake_common.fail = _fail
    fake_common.jsonable = lambda value: value
    fake_common.scenes = lambda: (APP, VIEW, SCENE)
    fake_common.edit = fake_edit
    fake_common.log_tail = lambda since=None, contains=None, lines=40: []
    fake_common.temp_dir = fake_temp_dir
    sys.modules[PACKAGE + ".common"] = fake_common

    fake_rig = types.ModuleType(PACKAGE + ".rig")
    fake_rig.RigError = type("RigError", (Exception,), {})
    fake_rig.characters = lambda: [dict(entry) for entry in CHARACTERS]
    fake_rig.controllers = lambda character=None: [{"id": FakeObjectId("Cascy")}]
    sys.modules[PACKAGE + ".rig"] = fake_rig

    _package(PACKAGE + ".tools", os.path.join(ROOT, PACKAGE, "tools"))
    _load("tools._schema", os.path.join("tools", "_schema.py"))
    mocap = _load("tools.mocap", os.path.join("tools", "mocap.py"))
    REAL_CHECK_MODEL = mocap.check_model
    mocap._schedule = lambda callback: callback()
    mocap._now = CLOCK.monotonic
    mocap._dialogs = lambda: list(DIALOGS)
    mocap._native_dialogs = lambda: list(NATIVE)
    mocap._click = lambda ref: CLICKS.append(ref) or True
    mocap._close_load_window = lambda: CLOSES.append("load") or True
    mocap._set_native_text = lambda dialog, text: NATIVE_TEXTS.append((dialog["kind"], text)) or True
    mocap._click_native_ok = fake_native_ok
    mocap._click_native_cancel = fake_native_cancel
    mocap._set_video_range = lambda dialog, first, last, state: True
    mocap.check_model = lambda path=None: "C:/fake/mocap.pt"
    VIDEO_PATH = os.path.join(tempfile.gettempdir(), "cascadeur-mcp-plus-T26-clip.mp4")
    with open(VIDEO_PATH, "wb") as handle:
        handle.write(b"\x00\x00\x00\x18ftypmp42")


VIDEO_PATH = ""


def fake_native_ok(dialog):
    NATIVE_OK.append(dialog["kind"])
    if dialog in NATIVE:
        NATIVE.remove(dialog)
    if dialog["kind"] == "file" and DIALOGS:
        texts = [
            text
            for text in DIALOGS[0].get("texts", [])
            if not str(text).startswith(("C:", "frames:", "fps:", "duration:"))
        ]
        texts.extend([VIDEO_PATH.replace("\\", "/"), "duration: 00:01.0", "fps: 30", "frames: 30"])
        DIALOGS[0]["texts"] = texts
    elif dialog["kind"] == "folder" and DIALOGS:
        DIALOGS[0]["texts"].append(NATIVE_TEXTS[-1][1].replace("\\", "/"))
    return True


def fake_native_cancel(dialog):
    NATIVE_CANCEL.append(dialog["kind"])
    if dialog in NATIVE:
        NATIVE.remove(dialog)
    return True


def load_dialog():
    return {
        "kind": "dialog",
        "window": 1,
        "type": "LoadVideoDialog",
        "texts": [
            "Load video",
            "Source",
            "Choose video file",
            "Choose...",
            "Video info",
            "duration: 00:00.0",
            "fps: 0",
            "frames: 0",
            "frames",
            "Choose...",
            "Targets",
        ],
        "buttons": [
            {"ref": "load:0", "text": "Choose..."},
            {"ref": "load:1", "text": "Choose..."},
            {"ref": "load:2", "text": "Import"},
        ],
    }


def mocap_dialog(state_text="Model checking..."):
    return {
        "kind": "dialog",
        "window": 2,
        "type": "MocapDialog",
        "texts": ["Mocap Cascy.casc", state_text],
        "buttons": [{"ref": "mocap:0", "text": "Cancel"}],
    }


def confirmation_dialog():
    return {
        "kind": "dialog",
        "window": 3,
        "type": "ButtonsDialog",
        "texts": [
            "Mocap",
            "Video-mocap for the selected interval of 11 frames will take approximately 44s. Proceed?",
            "Yes",
            "No",
        ],
        "buttons": [{"ref": "confirm:0", "text": "Yes"}, {"ref": "confirm:1", "text": "No"}],
    }


def result_dialog(text, buttons=("Ok",)):
    return {
        "kind": "dialog",
        "window": 4,
        "type": "ButtonsDialog",
        "texts": [text],
        "buttons": [{"ref": "result:" + str(index), "text": label} for index, label in enumerate(buttons)],
    }


def reset_world(characters=None, keys=(0, 10)):
    CLOCK.value = 1000.0
    mocap._clear_active()
    ACTION_MANAGER.calls.clear()
    ACTION_MANAGER.fail_keys.clear()
    DIALOGS[:] = [load_dialog()]
    NATIVE.clear()
    CLICKS.clear()
    NATIVE_TEXTS.clear()
    NATIVE_OK.clear()
    NATIVE_CANCEL.clear()
    CLOSES.clear()
    MODEL_EDITOR.deleted.clear()
    SCENE_UPDATER.generated = 0
    SCENE_UPDATER.ran.clear()
    CHARACTERS.clear()
    CHARACTERS.extend(
        characters
        if characters is not None
        else [
            {
                "name": "Cascy",
                "mode": "rig",
                "root_joint": "root",
                "info": "Cascy",
                "meshes": ["Cascy_mesh"],
            }
        ]
    )
    SCENE.model_viewer_value.objects = []
    SCENE.model_viewer_value.add("Cascy", "Cascy", "Mesh Object")
    SCENE.viewer = FakeLayersViewer()
    SCENE.viewer.add_layer("layer1", keys, objects=["Cascy"])
    SCENE.selector_value = FakeSelector(["old"])
    SCENE.layers_selector = FakeLayersSelector()
    SCENE.frame = 3
    SCENE.frames.clear()


def poll_once(step=1.0):
    CLOCK.advance(step)
    return mocap._poll()


def poll_until_phase(phase, limit=40):
    update = {}
    for _ in range(limit):
        update = poll_once()
        if update.get("done") or update.get("ok") is False:
            return update
        if mocap._active() is not None and mocap._active().get("phase") == phase:
            return update
    raise AssertionError(f"phase {phase!r} was not reached; last update {update!r}")


def poll_until_done(limit=80):
    update = {}
    for _ in range(limit):
        update = poll_once()
        if update.get("done") or update.get("ok") is False:
            return update
    raise AssertionError(f"the job did not finish; last update {update!r}")


def poll_until(predicate, limit=25, step=1.0):
    """Poll until predicate(update) is true (for signals only checked every few ticks)."""
    update = {}
    for _ in range(limit):
        update = poll_once(step)
        if update.get("done") or update.get("ok") is False:
            return update
        if predicate(update):
            return update
    raise AssertionError(f"the expected signal did not arrive; last update {update!r}")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class VideoPathTests(unittest.TestCase):
    def test_valid_mp4_is_normalized(self):
        self.assertEqual(mocap.validate_video(VIDEO_PATH), os.path.normpath(VIDEO_PATH))

    def test_relative_path_is_rejected(self):
        with self.assertRaises(errors.ToolError) as caught:
            mocap.validate_video("clip.mp4")
        self.assertIn("absolute", str(caught.exception))

    def test_non_mp4_is_rejected(self):
        with self.assertRaises(errors.ToolError) as caught:
            mocap.validate_video(os.path.join(tempfile.gettempdir(), "clip.avi"))
        self.assertIn(".mp4", str(caught.exception))

    def test_missing_file_is_rejected(self):
        with self.assertRaises(errors.ToolError) as caught:
            mocap.validate_video(os.path.join(tempfile.gettempdir(), "T26-does-not-exist.mp4"))
        self.assertIn("does not exist", str(caught.exception))

    def test_empty_value_is_rejected(self):
        for value in (None, "", 3):
            with self.assertRaises(errors.ToolError):
                mocap.validate_video(value)

    def test_model_file_and_install_check(self):
        folder = tempfile.mkdtemp(prefix="T26-model-")
        path = os.path.join(folder, "Nekki Limited", "Cascadeur", "resources")
        os.makedirs(path)
        model = os.path.join(path, "mocap.pt")
        with open(model, "wb") as handle:
            handle.write(b"model")
        self.assertEqual(mocap.model_file(folder), os.path.normpath(model))
        self.assertEqual(REAL_CHECK_MODEL(folder), os.path.normpath(model))
        with open(model, "wb"):
            pass  # an empty model is not installed
        with self.assertRaises(mocap._Error) as caught:
            REAL_CHECK_MODEL(folder)
        self.assertIn("not installed", str(caught.exception))
        self.assertIn("mocap_010725.pt", caught.exception.data["download_url"])


class IntervalTests(unittest.TestCase):
    def test_both_or_neither(self):
        self.assertEqual(mocap.parse_interval(None, None), (None, None))
        self.assertEqual(mocap.parse_interval(0, 10), (0, 10))
        self.assertEqual(mocap.parse_interval("5", "15"), (5, 15))
        for start, end in ((0, None), (None, 10)):
            with self.assertRaises(errors.ToolError) as caught:
                mocap.parse_interval(start, end)
            self.assertIn("both start and end", str(caught.exception))

    def test_reversed_and_invalid_frames(self):
        with self.assertRaises(errors.ToolError):
            mocap.parse_interval(10, 5)
        with self.assertRaises(errors.ToolError):
            mocap.parse_interval(-1, 5)
        with self.assertRaises(errors.ToolError):
            mocap.parse_interval("a", 5)

    def test_resolve_selection(self):
        self.assertEqual(mocap.resolve_selection(3, 9, 4), (3, 9))
        with self.assertRaises(errors.ToolError) as caught:
            mocap.resolve_selection(3, 9, 0)
        self.assertIn("No timeline interval", str(caught.exception))
        with self.assertRaises(errors.ToolError):
            mocap.resolve_selection(9, 3, 1)
        with self.assertRaises(errors.ToolError):
            mocap.resolve_selection(None, None, 1)

    def test_video_range(self):
        self.assertEqual(mocap.parse_video_range(None, None), (None, None))
        self.assertEqual(mocap.parse_video_range(5, 30), (5, 30))
        with self.assertRaises(errors.ToolError):
            mocap.parse_video_range(30, 5)

    def test_clamp_video_range(self):
        self.assertEqual(mocap.clamp_video_range(150, None, None), (0, 149, False))
        self.assertEqual(mocap.clamp_video_range(150, -5, 500), (0, 149, True))
        self.assertEqual(mocap.clamp_video_range(150, 10, 20), (10, 20, False))
        self.assertEqual(mocap.clamp_video_range(0, 10, 20), (10, 20, False))
        self.assertEqual(mocap.clamp_video_range(10, 20, 30), (9, 9, True))


class DialogTests(unittest.TestCase):
    def test_text_includes_buttons(self):
        self.assertEqual(mocap.dialog_text(result_dialog("Hello", ("Yes", "No"))), "Hello Yes No")

    def test_roles(self):
        cases = {
            "Video-mocap for the selected interval of 30 frames will take approximately 1m. Proceed?": "confirm",
            "All frames have been processed": "success",
            "Frames containing unrecognized poses: 1, 3-5": "failed_frames",
            "Mocap Failed": "failed",
            "Mocap model is not installed": "model_missing",
            "To use this feature an additional 825Mb package will be downloaded. Proceed?": "download",
            "The downloaded data are corrupted. Retry?": "corrupt",
            "Please select an object with an animation reference": "select_object",
            "Video-mocap for the selected interval of 2 frames will take approximately 8s. Proceed?": "confirm",
            "Selected frames do not contain the animation reference. Please select frames with the animation reference [0, 120]": "select_frames",
            "Selected textures are in different sizes": "textures",
            "Video decoding error": "import_error",
            "Couldn't create frames directory": "import_error",
            "Something else entirely": None,
            "": None,
        }
        for text, role in cases.items():
            self.assertEqual(mocap.dialog_role(text), role, text)

    def test_failed_frames_formats(self):
        self.assertEqual(mocap.parse_failed_frames("Frames containing unrecognized poses: 1, 3-5"), [1, 3, 4, 5])
        self.assertEqual(mocap.parse_failed_frames("Frames containing unrecognized poses: [7 9]"), [7, 9])
        self.assertEqual(mocap.parse_failed_frames("Frames containing unrecognized poses: 2-4, 10"), [2, 3, 4, 10])
        self.assertEqual(mocap.parse_failed_frames("no frames here"), [])
        self.assertEqual(mocap.parse_failed_frames(None), [])

    def test_load_video_dialog_texts(self):
        texts = ["C:/Users/me/clip.mp4", "frames: 31", "fps: 30"]
        self.assertTrue(mocap.path_is_shown(texts, r"C:\Users\me\clip.mp4"))
        self.assertFalse(mocap.path_is_shown(texts, r"C:\other\clip.mp4"))
        self.assertEqual(mocap.video_frame_count(texts), 31)
        self.assertEqual(mocap.video_frame_count(["frames: 0"]), 0)
        self.assertIsNone(mocap.video_frame_count([]))

    def test_mocap_state_texts(self):
        self.assertEqual(mocap.mocap_state(["Model checking..."]), ("check", 0.10))
        self.assertEqual(mocap.mocap_state(["Mocap Cascy.casc", "Model loading..."]), ("loading", 0.50))
        self.assertEqual(mocap.mocap_state(["Mocaping..."]), ("mocap", 0.60))
        self.assertIsNone(mocap.mocap_state(["something else"]))

    def test_import_progress(self):
        self.assertEqual(mocap.import_progress({"buttons": [{"text": "Progress 37%"}]}), 0.37)
        self.assertEqual(mocap.import_progress({"buttons": [{"text": "Progress 120%"}]}), 1.0)
        self.assertIsNone(mocap.import_progress({"buttons": [{"text": "Import"}]}))
        self.assertIsNone(mocap.import_progress(None))


class CharacterTests(unittest.TestCase):
    def setUp(self):
        reset_world()

    def test_single_character_without_query(self):
        chosen = mocap.choose_character(None, CHARACTERS)
        self.assertEqual(chosen["name"], "Cascy")

    def test_several_characters_need_a_name(self):
        entries = CHARACTERS + [dict(CHARACTERS[0], name="Other", info="Other")]
        with self.assertRaises(errors.ToolError) as caught:
            mocap.choose_character(None, entries)
        self.assertIn("pass character", str(caught.exception))
        self.assertEqual(caught.exception.data["candidates"], ["Cascy", "Other"])

    def test_names_root_joint_info_and_mesh_match(self):
        for query in ("Cascy", "cascy", "root", "Cascy_mesh"):
            self.assertEqual(mocap.choose_character(query, CHARACTERS)["name"], "Cascy")

    def test_partial_and_unknown(self):
        self.assertEqual(mocap.choose_character("Cas", CHARACTERS)["name"], "Cascy")
        with self.assertRaises(errors.ToolError) as caught:
            mocap.choose_character("Nomad", CHARACTERS)
        self.assertIn("not found", str(caught.exception))
        self.assertEqual(caught.exception.data["candidates"], ["Cascy"])

    def test_ambiguous_exact_match(self):
        entries = [dict(CHARACTERS[0]), dict(CHARACTERS[0], name="Cascy #2")]
        with self.assertRaises(errors.ToolError) as caught:
            mocap.choose_character("Cascy", entries)
        self.assertIn("matches", str(caught.exception))

    def test_no_character(self):
        with self.assertRaises(errors.ToolError) as caught:
            mocap.choose_character(None, [])
        self.assertIn("no character", str(caught.exception).lower())

    def test_new_object_ids(self):
        self.assertEqual(mocap.new_object_ids(["a", "b"], ["a", "b", "c"]), ["c"])
        self.assertEqual(mocap.new_object_ids([], ["x"]), ["x"])


# ---------------------------------------------------------------------------
# begin / poll / cancel against the fake scene
# ---------------------------------------------------------------------------


class JobTests(unittest.TestCase):
    def setUp(self):
        reset_world()
        self.addCleanup(mocap._clear_active)

    def start(self, **kwargs):
        arguments = {"start": 0, "end": 10, "dry_run": True}
        arguments.update(kwargs)
        result = mocap.video_mocap(VIDEO_PATH, **arguments)
        self.assertTrue(result.get("ok"), result)
        return result

    def reach_waiting(self, dry_run=True):
        self.start(dry_run=dry_run)
        poll_until_phase("file_dialog")
        self.assertIn("load:0", CLICKS, CLICKS)  # the source Choose... button
        NATIVE.append({"kind": "file", "hwnd": 1, "edit": 2})
        poll_until_phase("file_set")
        poll_until_phase("folder_dialog")
        self.assertIn("load:1", CLICKS, CLICKS)  # the frames folder Choose... button
        NATIVE.append({"kind": "folder", "hwnd": 3, "edit": 4})
        poll_until_phase("folder_set")
        poll_until_phase("importing")
        self.assertIn("load:2", CLICKS, CLICKS)  # the Import button
        SCENE.model_viewer_value.add("plane-1", "MeshObject(0)", "Mesh Object")
        poll_until_phase("waiting")
        return mocap._active()

    def test_invalid_video_is_refused_before_any_ui(self):
        result = mocap.video_mocap(os.path.join(tempfile.gettempdir(), "T26-missing.mp4"))
        self.assertFalse(result["ok"], result)
        self.assertIn("does not exist", result["error"])
        self.assertEqual(ACTION_MANAGER.calls, [])

    def test_missing_model_is_refused_with_the_download_url(self):
        def refuse(path=None):
            raise mocap._Error("Cascadeur's mocap model is not installed.", hint="download it", url="x")

        original = mocap.check_model
        mocap.check_model = refuse
        try:
            result = mocap.video_mocap(VIDEO_PATH)
        finally:
            mocap.check_model = original
        self.assertFalse(result["ok"], result)
        self.assertIn("not installed", result["error"])

    def test_no_character_is_refused(self):
        CHARACTERS.clear()
        result = mocap.video_mocap(VIDEO_PATH)
        self.assertFalse(result["ok"], result)
        self.assertIn("no character", result["error"].lower())

    def test_unknown_character_lists_candidates(self):
        result = mocap.video_mocap(VIDEO_PATH, character="Nomad")
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["candidates"], ["Cascy"])

    def test_second_begin_is_refused(self):
        self.start()
        second = mocap.video_mocap(VIDEO_PATH)
        self.assertFalse(second["ok"], second)
        self.assertIn("still running", second["error"])

    def test_selection_is_used_when_start_and_end_are_omitted(self):
        SCENE.layers_selector.ids = ["layer1"]
        SCENE.layers_selector.first, SCENE.layers_selector.last = 4, 8
        result = mocap.video_mocap(VIDEO_PATH, dry_run=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frames"], [4, 8])
        self.assertEqual(mocap._active()["first"], 4)

    def test_no_timeline_selection_is_an_error(self):
        SCENE.layers_selector.ids = []
        result = mocap.video_mocap(VIDEO_PATH, dry_run=True)
        self.assertFalse(result["ok"], result)
        self.assertIn("No timeline interval", result["error"])

    def test_import_uses_the_native_file_and_folder_pickers(self):
        self.reach_waiting(dry_run=True)
        self.assertEqual(NATIVE_TEXTS[0][0], "file")
        self.assertEqual(os.path.normpath(NATIVE_TEXTS[0][1]), os.path.normpath(VIDEO_PATH))
        self.assertEqual(NATIVE_TEXTS[1][0], "folder")
        frames_dir = NATIVE_TEXTS[1][1]
        self.assertTrue(frames_dir.lower().startswith(tempfile.gettempdir().lower()), frames_dir)
        self.assertEqual(NATIVE_OK, ["file", "folder"])
        self.assertEqual(NATIVE_CANCEL, [])
        state = mocap._active()
        self.assertEqual(state["video_frames"], 30)
        self.assertEqual((state["video_first"], state["video_last"]), (0, 29))
        self.assertEqual(state["import_action"], "View.Bind video")

    def test_dry_run_cancels_the_confirmation_and_cleans_up(self):
        state = self.reach_waiting(dry_run=True)
        self.assertEqual(SCENE.selector_value.ids, ["plane-1"])
        DIALOGS.append(confirmation_dialog())
        update = poll_until_done()
        self.assertTrue(update.get("done"), update)
        result = update["result"]
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["dry_run"], result)
        self.assertEqual(result["outcome"], "cancelled", result)
        self.assertEqual(result["frames"], [0, 10], result)
        self.assertEqual(result["character"], "Cascy", result)
        self.assertIsNone(result["reference_plane"], result)
        self.assertEqual(result["plane"]["name"], "MeshObject(0)", result)
        self.assertIn("View.Bind video", ACTION_MANAGER.calls)
        self.assertIn("View.MocapTool", ACTION_MANAGER.calls)
        self.assertIn("confirm:1", CLICKS, CLICKS)  # the confirmation's No button
        self.assertEqual(MODEL_EDITOR.deleted, ["plane-1"], MODEL_EDITOR.deleted)
        self.assertEqual(result["keyframes"], 2, result)  # the fake character layer has keys 0 and 10
        self.assertIsNone(mocap._active())
        self.assertEqual(SCENE.selector_value.ids, ["old"], "the previous selection is restored")
        self.assertTrue(result["cleanup"]["frames"], result)
        self.assertFalse(os.path.isdir(state["frames_dir"]), state["frames_dir"])

    def test_completed_run_collects_keys_and_closes_the_mocap_dialog(self):
        self.reach_waiting(dry_run=False)
        DIALOGS.append(confirmation_dialog())
        poll_until(lambda update: "confirm:0" in CLICKS)
        DIALOGS.append(mocap_dialog("Model checking..."))
        poll_until(lambda update: (mocap._active() or {}).get("mocap_state_name") == "check")
        DIALOGS[:] = [mocap_dialog("Mocaping...")]
        poll_until(lambda update: (mocap._active() or {}).get("mocap_state_name") == "mocap")
        DIALOGS[:] = [result_dialog("All frames have been processed")]
        update = poll_until_done()
        result = update["result"]
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["outcome"], "completed", result)
        self.assertEqual(result["mocap_state"], "mocap", result)
        self.assertEqual(result["failed_frames"], [], result)
        self.assertIn("MCP: Video mocap reference", result["undo"]["steps"], result)
        self.assertIsNone(mocap._active())

    def test_partial_failure_reports_failed_frames(self):
        self.reach_waiting(dry_run=False)
        DIALOGS.append(confirmation_dialog())
        poll_until(lambda update: "confirm:0" in CLICKS)
        DIALOGS.append(mocap_dialog("Mocaping..."))
        poll_until(lambda update: (mocap._active() or {}).get("mocap_state_name") == "mocap")
        DIALOGS[:] = [result_dialog("Frames containing unrecognized poses: 2-3")]
        update = poll_until_done()
        result = update["result"]
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["outcome"], "partial", result)
        self.assertEqual(result["failed_frames"], [2, 3], result)
        self.assertIn("could not recognise", result["warning"], result)

    def test_select_object_error_retries_with_the_character(self):
        self.reach_waiting(dry_run=True)
        DIALOGS.append(result_dialog("Please select an object with an animation reference"))
        poll_until(lambda update: ACTION_MANAGER.calls.count("View.MocapTool") == 2)
        self.assertEqual(mocap._active()["attempt"], 1)
        self.assertEqual(SCENE.selector_value.ids, ["Cascy"])
        DIALOGS[:] = [confirmation_dialog()]
        update = poll_until_done()
        self.assertTrue(update["result"]["ok"], update)

    def test_import_error_dialog_aborts(self):
        self.start()
        poll_until_phase("file_dialog")
        NATIVE.append({"kind": "file", "hwnd": 1, "edit": 2})
        poll_until_phase("file_set")
        poll_until_phase("folder_dialog")
        NATIVE.append({"kind": "folder", "hwnd": 3, "edit": 4})
        poll_until_phase("folder_set")
        poll_until_phase("importing")
        DIALOGS.append(result_dialog("Couldn't create frames directory"))
        update = poll_until_done()
        self.assertFalse(update.get("ok"), update)
        self.assertIn("could not import the video", update["error"])
        self.assertIsNone(mocap._active())

    def test_wrong_picker_is_cancelled_and_the_other_choose_is_used(self):
        self.start()
        poll_until_phase("file_dialog")
        NATIVE.append({"kind": "folder", "hwnd": 9, "edit": 8})  # wrong picker
        update = poll_once()
        self.assertFalse(update.get("done"), update)
        self.assertEqual(NATIVE_CANCEL, ["folder"])
        self.assertEqual(mocap._active()["choose_index"], 1)
        self.assertEqual(mocap._active()["phase"], "open_dialog")

    def test_timeout_cancels_the_job(self):
        self.start(timeout=5)
        CLOCK.advance(6)
        update = mocap._poll()
        self.assertFalse(update.get("ok"), update)
        self.assertTrue(update.get("timed_out"), update)
        self.assertIn("did not finish within 5s", update["error"])
        self.assertIsNone(mocap._active())

    def test_cancel_hook_deletes_the_plane_and_the_frames(self):
        state = self.reach_waiting(dry_run=True)
        mocap._cancel(video_path=VIDEO_PATH)
        self.assertIsNone(mocap._active())
        self.assertEqual(MODEL_EDITOR.deleted, ["plane-1"], MODEL_EDITOR.deleted)
        self.assertFalse(os.path.isdir(state["frames_dir"]), state["frames_dir"])
        self.assertEqual(SCENE.selector_value.ids, ["old"])

    def test_poll_without_a_job_fails(self):
        update = mocap._poll()
        self.assertFalse(update["ok"], update)
        self.assertIn("no video-mocap job", update["error"].lower())

    def test_clamp_is_reported_as_a_warning(self):
        self.start(from_frame=0, to_frame=999)
        poll_until_phase("file_dialog")
        NATIVE.append({"kind": "file", "hwnd": 1, "edit": 2})
        poll_until_phase("file_set")
        poll_until_phase("folder_dialog")
        NATIVE.append({"kind": "folder", "hwnd": 3, "edit": 4})
        poll_until_phase("folder_set")
        poll_until_phase("importing")
        SCENE.model_viewer_value.add("plane-1", "MeshObject(0)", "Mesh Object")
        poll_until_phase("waiting")
        DIALOGS.append(confirmation_dialog())
        update = poll_until_done()
        self.assertIn("outside the video", update["result"]["warning"], update["result"])

    def test_mocap_closing_without_a_result_dialog_completes_with_a_warning(self):
        self.reach_waiting(dry_run=False)
        DIALOGS.append(confirmation_dialog())
        poll_until(lambda update: "confirm:0" in CLICKS)
        DIALOGS.append(mocap_dialog("Mocaping..."))
        poll_until(lambda update: (mocap._active() or {}).get("mocap_state_name") == "mocap")
        DIALOGS[:] = []
        update = poll_until_done(limit=120)
        result = update["result"]
        self.assertEqual(result["outcome"], "completed", result)
        self.assertIn("no completion dialog", result["warning"], result)


class ToolTableTests(unittest.TestCase):
    def test_single_background_tool(self):
        names = [tool["name"] for tool in mocap.TOOLS]
        self.assertEqual(names, ["video_mocap"])
        tool = mocap.TOOLS[0]
        self.assertTrue(tool["background"])
        self.assertEqual(tool["job_timeout"], mocap.JOB_TIMEOUT_S)
        self.assertTrue(callable(tool["fn"]))
        self.assertTrue(callable(tool["poll"]))
        self.assertTrue(callable(tool["cancel"]))
        self.assertEqual(tool["inputSchema"]["required"], ["video_path"])

    def test_description_covers_the_workflow(self):
        description = mocap.TOOLS[0]["description"]
        for needle in ("View.MocapTool", "job id", "dry_run", "keep_reference", "failed_frames"):
            self.assertIn(needle, description)


def tearDownModule():
    """Remove this module's fakes and put back whatever another test module had loaded."""
    for name in (
        PACKAGE + ".tools.mocap",
        PACKAGE + ".timeline",
        PACKAGE + ".rig",
        PACKAGE + ".common",
    ):
        sys.modules.pop(name, None)
    for name, module in _REPLACED.items():
        if module is not None:
            sys.modules[name] = module
    try:
        os.remove(VIDEO_PATH)
    except OSError:
        pass


if __name__ == "__main__":
    unittest.main()
