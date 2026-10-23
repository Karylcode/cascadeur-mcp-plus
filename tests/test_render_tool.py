"""Tests for the render_image tool's argument handling (no Cascadeur, no Qt).

cascadeur_mcp_plus.render is covered by tests/test_render.py; this file checks the tool layer:
the default path, frame switching and restoring, overwrite handling, and that the image content
flag follows return_image so server._call_tool unpacks only what the tool actually returned.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import importlib
import os
import struct
import sys
import tempfile
import types
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE_MODULES = ("csc", "numpy", "PySide6", "PySide6.QtCore")
PACKAGE = "cascadeur_mcp_plus"


def png_bytes(width: int = 4, height: int = 2, rgb=(10, 20, 30)) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanlines = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b"")
    )


def fake_module(name: str, **attributes) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


class FakeDomainScene:
    """Cascadeur's timeline: the cursor (frame) may sit outside the animation range.

    get_current_frame() clamps to [first, last] like the real default; clamp_animation=False
    returns the cursor itself. clamp_set emulates a build that also clamps set_current_frame.
    """

    def __init__(self) -> None:
        self.frame = 0
        self.first = 0
        self.last = None
        self.clamp_set = False
        self.history = []

    def get_current_frame(self, clamp_animation: bool = True) -> int:
        if clamp_animation and self.last is not None:
            return min(max(self.frame, self.first), self.last)
        return self.frame

    def set_current_frame(self, frame: int) -> None:
        frame = int(frame)
        if self.clamp_set and self.last is not None:
            frame = min(max(frame, self.first), self.last)
        self.history.append(frame)
        self.frame = frame


class FakeViewScene:
    def __init__(self, domain: FakeDomainScene) -> None:
        self.domain = domain

    def domain_scene(self) -> FakeDomainScene:
        return self.domain


class FakeApplication:
    def __init__(self) -> None:
        self.domain = FakeDomainScene()
        self.view = FakeViewScene(self.domain)

    def current_scene(self) -> FakeViewScene:
        return self.view


class RenderImageToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Other test files load cascadeur_mcp_plus modules with their own fake csc (test_jobs,
        # test_history, test_scene_files), so start from a clean package namespace and put the
        # previous entries back in tearDownClass.
        cached = [name for name in sys.modules if name == PACKAGE or name.startswith(PACKAGE + ".")]
        cls.saved_modules = {name: sys.modules.get(name) for name in FAKE_MODULES + tuple(cached)}
        for name in cached:
            del sys.modules[name]
        cls.app = FakeApplication()
        qt_core = fake_module("PySide6.QtCore", QObject=type("QObject", (), {}))
        sys.modules["PySide6"] = fake_module("PySide6", QtCore=qt_core)
        sys.modules["PySide6.QtCore"] = qt_core
        sys.modules["csc"] = fake_module(
            "csc",
            app=types.SimpleNamespace(get_application=lambda: cls.app),
            math=types.SimpleNamespace(
                Rotation=type("Rotation", (), {}),
                Quaternion=type("Quaternion", (), {}),
                OrthogonalTransform=type("OrthogonalTransform", (), {}),
                ScaledTransform=type("ScaledTransform", (), {}),
            ),
            Guid=type("Guid", (), {}),
            model=types.SimpleNamespace(ObjectId=type("ObjectId", (), {})),
        )
        sys.modules["numpy"] = fake_module("numpy", ndarray=type("ndarray", (), {}), generic=type("generic", (), {}))
        package = fake_module("cascadeur_mcp_plus")
        package.__path__ = [os.path.join(ROOT, "cascadeur_mcp_plus")]
        sys.modules["cascadeur_mcp_plus"] = package
        tools_package = fake_module("cascadeur_mcp_plus.tools")
        tools_package.__path__ = [os.path.join(ROOT, "cascadeur_mcp_plus", "tools")]
        sys.modules["cascadeur_mcp_plus.tools"] = tools_package

        cls.common = importlib.import_module("cascadeur_mcp_plus.common")
        cls.ui = fake_module("cascadeur_mcp_plus.ui")
        cls.ui.MINIMIZED = False
        cls.ui.resolve_window = lambda window=None, include_hidden=False: (0, "main window")
        cls.ui._is_minimized = lambda window: cls.ui.MINIMIZED
        sys.modules["cascadeur_mcp_plus.ui"] = cls.ui
        cls.dispatch = importlib.import_module("cascadeur_mcp_plus.dispatch")
        cls.render = importlib.import_module("cascadeur_mcp_plus.render")
        cls.camera = importlib.import_module("cascadeur_mcp_plus.camera")
        cls.tool = importlib.import_module("cascadeur_mcp_plus.tools.render")

        # T31's camera_preset only needs the camera module's three calls; the fake scene has no
        # viewport, so replace them (call_on_gui is replaced below and passes keyword arguments).
        cls.camera_calls = []

        def camera_snapshot():
            cls.camera_calls.append("snapshot")
            return {"position": [1.0, 2.0, 3.0], "target": [0.0, 1.0, 0.0], "type": "PERSPECTIVE"}

        def camera_apply_preset(view, target="character", aspect=None):
            cls.camera_calls.append(f"apply:{view}:{aspect}")
            return {
                "preset": view,
                "camera": {"position": [10.0, 20.0, 30.0], "target": [0.0, 1.0, 0.0], "type": "PERSPECTIVE"},
            }

        def camera_restore(saved):
            cls.camera_calls.append(f"restore:{saved['position']}")
            return {"position": saved["position"], "target": saved["target"], "type": saved["type"]}

        cls.real_camera = {
            "snapshot": cls.camera.snapshot,
            "apply_preset": cls.camera.apply_preset,
            "restore": cls.camera.restore,
        }
        cls.camera.snapshot = camera_snapshot
        cls.camera.apply_preset = camera_apply_preset
        cls.camera.restore = camera_restore

        # The dispatcher and the real renderer are the only things this file replaces: the
        # first would need Qt's event loop, the second needs Cascadeur's GUI thread.
        cls.real_call_on_gui = cls.dispatch.call_on_gui
        cls.dispatch.call_on_gui = lambda fn, args, timeout, idle=False: fn(**args)
        cls.real_render_to_file = cls.render.render_to_file
        cls.real_encode_image = cls.render.encode_image
        cls.renders = []

        def render_to_file(view_scene, path, width=1280, height=720, samples=None, timeout=180.0):
            with open(path, "wb") as handle:
                handle.write(png_bytes(width, height))
            cls.renders.append({"path": path, "width": width, "height": height, "samples": samples})
            return {
                "path": path,
                "bytes": os.path.getsize(path),
                "seconds": 0.01,
                "width": width,
                "height": height,
                "samples": samples,
            }

        def encode_image(path, max_width=1280):
            with open(path, "rb") as handle:
                data = handle.read()
            return base64.b64encode(data).decode("ascii"), "image/png", {"scaled": False}

        cls.render.render_to_file = render_to_file
        cls.render.encode_image = encode_image

    @classmethod
    def tearDownClass(cls) -> None:
        cls.dispatch.call_on_gui = cls.real_call_on_gui
        cls.render.render_to_file = cls.real_render_to_file
        cls.render.encode_image = cls.real_encode_image
        for name, value in cls.real_camera.items():
            setattr(cls.camera, name, value)
        for name in [n for n in sys.modules if n == PACKAGE or n.startswith(PACKAGE + ".")]:
            del sys.modules[name]
        for name, module in cls.saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def setUp(self) -> None:
        self.ui.MINIMIZED = False
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app.domain.frame = 0
        self.app.domain.first = 0
        self.app.domain.last = None
        self.app.domain.clamp_set = False
        self.app.domain.history = []
        self.renders.clear()
        self.camera_calls.clear()
        self.tool._STATE.image = False
        self.saved_temp_dir = self.common.temp_dir

        def temp_dir(*parts):
            path = os.path.join(self.temp.name, *parts) if parts else self.temp.name
            os.makedirs(path, exist_ok=True)
            return path

        self.common.temp_dir = temp_dir
        self.addCleanup(setattr, self.common, "temp_dir", self.saved_temp_dir)

    def test_default_path_lands_in_the_renders_folder(self):
        result = self.tool.render_image(width=64, height=32, return_image=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual(os.path.dirname(result["path"]), os.path.join(self.temp.name, "renders"))
        self.assertTrue(result["path"].endswith(".png"))
        self.assertTrue(os.path.isfile(result["path"]))
        self.assertEqual(result["file_size"], [64, 32])
        self.assertFalse(result["image_content"])
        self.assertEqual(self.renders[0]["width"], 64)
        self.assertEqual(self.renders[0]["height"], 32)

    def test_frame_is_switched_for_the_render_and_restored(self):
        result = self.tool.render_image(frame=7, width=64, height=32, return_image=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame"], 7)
        self.assertEqual(result["current_frame"], 0)
        self.assertEqual(self.app.domain.frame, 0)
        self.assertEqual(self.app.domain.history, [7, 0])

    def test_current_frame_is_left_alone_without_a_frame_argument(self):
        result = self.tool.render_image(width=64, height=32, return_image=False)
        self.assertEqual(result["frame"], 0)
        self.assertEqual(self.app.domain.history, [])

    def test_clamped_frame_is_reported(self):
        # Defensive: only a build that clamps set_current_frame can trigger this note.
        self.app.domain.last = 10
        self.app.domain.clamp_set = True
        result = self.tool.render_image(frame=99, width=64, height=32, return_image=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame"], 10)
        self.assertEqual(result["requested_frame"], 99)
        self.assertIn("clamped", result["note"])
        self.assertEqual(self.app.domain.frame, 0)

    def test_a_cursor_outside_the_animation_range_is_not_moved(self):
        # Cascadeur 2026.2.3 keeps the cursor outside the range (verified live: raw 128 with
        # last=123); get_current_frame() alone would report 123 and restoring that moves it.
        self.app.domain.frame = 60
        self.app.domain.last = 40
        result = self.tool.render_image(width=64, height=32, return_image=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame"], 60)
        self.assertEqual(result["current_frame"], 60)
        self.assertEqual(self.app.domain.frame, 60)
        self.assertEqual(self.app.domain.history, [], "no frame switch was needed")

    def test_a_cursor_outside_the_animation_range_survives_a_frame_render(self):
        self.app.domain.frame = 60
        self.app.domain.last = 40
        result = self.tool.render_image(frame=10, width=64, height=32, return_image=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["frame"], 10)
        self.assertEqual(result["current_frame"], 60)
        self.assertEqual(self.app.domain.frame, 60)
        self.assertEqual(self.app.domain.history, [10, 60])

    def test_frame_is_restored_when_the_render_fails(self):
        def failing(*args, **kwargs):
            raise self.render.ToolError("renderer exploded")

        working = self.render.render_to_file
        self.render.render_to_file = failing
        try:
            result = self.tool.render_image(frame=5, width=64, height=32)
        finally:
            self.render.render_to_file = working
        self.assertFalse(result["ok"])
        self.assertIn("renderer exploded", result["error"])
        self.assertEqual(self.app.domain.frame, 0)
        self.assertEqual(self.app.domain.history, [5, 0])
        self.assertFalse(self.tool._STATE.image)

    def test_existing_file_is_refused_without_overwrite(self):
        path = os.path.join(self.temp.name, "taken.png")
        with open(path, "wb") as handle:
            handle.write(b"old")
        refused = self.tool.render_image(width=32, height=16, path=path, return_image=False)
        self.assertFalse(refused["ok"])
        self.assertIn("overwrite", refused["error"])
        self.assertEqual(self.renders, [])
        allowed = self.tool.render_image(width=32, height=16, path=path, overwrite=True, return_image=False)
        self.assertTrue(allowed["ok"], allowed)
        self.assertEqual(len(self.renders), 1)

    def test_image_flag_follows_return_image(self):
        plain = self.tool.render_image(width=32, height=16, return_image=False)
        self.assertIsInstance(plain, dict)
        self.assertFalse(bool(self.tool.TOOLS[0]["image"]))
        picture = self.tool.render_image(width=32, height=16, return_image=True, max_width=16)
        self.assertIsInstance(picture, tuple)
        data, mime, meta = picture
        self.assertEqual(mime, "image/png")
        self.assertEqual(base64.b64decode(data)[:8], b"\x89PNG\r\n\x1a\n")
        self.assertTrue(meta["ok"])
        self.assertTrue(meta["image_content"])
        self.assertTrue(bool(self.tool.TOOLS[0]["image"]))
        self.tool._STATE.image = False
        self.assertFalse(bool(self.tool.TOOLS[0]["image"]))

    def test_bad_arguments_are_reported_before_rendering(self):
        self.assertFalse(self.tool.render_image(frame="seven")["ok"])
        self.assertFalse(self.tool.render_image(frame=1.5)["ok"])
        self.assertFalse(self.tool.render_image(path="relative.png")["ok"])
        self.assertEqual(self.renders, [])
        self.assertFalse(self.tool._STATE.image)

    def test_minimized_window_fails_fast_with_a_restore_hint(self):
        self.ui.MINIMIZED = True
        result = self.tool.render_image(width=64, height=32)
        self.assertFalse(result["ok"])
        self.assertIn("minimized", result["error"])
        self.assertIn("restore", result["hint"])
        self.assertEqual(self.renders, [], "nothing may be rendered while the window is minimized")
        self.assertEqual(self.app.domain.history, [], "the frame must not change for a refused call")
        self.assertFalse(bool(self.tool.TOOLS[0]["image"]))

    def test_uppercase_png_extension_is_written_as_png(self):
        # RenderToFile appends ".png" to an unknown extension ("shot.PNG" -> "shot.PNG.png"),
        # so the tool writes the lowercase name the caller asked for.
        path = os.path.join(self.temp.name, "shot.PNG")
        result = self.tool.render_image(width=32, height=16, path=path, return_image=False)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["path"].endswith(".png"), result["path"])
        self.assertFalse(result["path"].endswith(".PNG"))
        self.assertEqual(self.renders[-1]["path"], result["path"])
        self.assertTrue(os.path.isfile(result["path"]))
        self.assertFalse(os.path.exists(path + ".png"), "the renderer must not append another .png")

    def test_only_png_output_paths_are_accepted(self):
        result = self.tool.render_image(width=32, height=16, path=os.path.join(self.temp.name, "image.jpg"))
        self.assertFalse(result["ok"])
        self.assertIn(".png", result["error"])
        self.assertEqual(self.renders, [])

    def test_schema_documents_every_argument(self):
        properties = self.tool.TOOLS[0]["inputSchema"]["properties"]
        self.assertEqual(
            set(properties),
            {"frame", "width", "height", "samples", "path", "overwrite", "return_image", "max_width", "camera_preset"},
        )
        self.assertEqual(self.tool.TOOLS[0]["name"], "render_image")
        self.assertEqual(
            properties["camera_preset"]["enum"], ["front", "back", "left", "right", "top", "three_quarter"]
        )

    def test_camera_preset_is_applied_for_the_render_and_restored(self):
        result = self.tool.render_image(width=64, height=32, return_image=False, camera_preset="front")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["camera_preset"], "front")
        self.assertEqual(result["camera"]["position"], [10.0, 20.0, 30.0])
        self.assertTrue(result["camera_restored"])
        self.assertEqual(self.camera_calls[0], "snapshot")
        self.assertEqual(self.camera_calls[1], "apply:front:2.0")
        self.assertEqual(self.camera_calls[2], "restore:[1.0, 2.0, 3.0]")

    def test_camera_is_restored_when_the_render_fails(self):
        def failing(*args, **kwargs):
            raise self.render.ToolError("renderer exploded")

        working = self.render.render_to_file
        self.render.render_to_file = failing
        try:
            result = self.tool.render_image(width=64, height=32, camera_preset="top")
        finally:
            self.render.render_to_file = working
        self.assertFalse(result["ok"])
        self.assertEqual(self.camera_calls[0], "snapshot")
        self.assertEqual(self.camera_calls[-1], "restore:[1.0, 2.0, 3.0]")

    def test_unknown_camera_preset_is_refused_before_rendering(self):
        result = self.tool.render_image(width=64, height=32, camera_preset="sideways")
        self.assertFalse(result["ok"])
        self.assertIn("camera_preset", result["error"])
        self.assertEqual(self.renders, [])
        self.assertEqual(self.camera_calls, [])


if __name__ == "__main__":
    unittest.main()
