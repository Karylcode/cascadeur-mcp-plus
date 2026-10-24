"""Tests for the render_contact_sheet tool (no Cascadeur, no Qt runnable code).

cascadeur_mcp_plus/tools/contact_sheet.py imports Cascadeur and Qt lazily where it needs them;
this file builds the same small fakes as tests/test_render_tool.py, replaces the dispatcher, the
renderer and the Qt composition step, and checks the tool layer: frame planning, layout, file
handling, playhead/camera restoration, the camera_preset bridge and the image-content flag.

The real QImage/QPainter composition is exercised by tests/live/test_contact_sheet.py, which is
the only place where Cascadeur (and therefore PySide6) is available.
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
from types import SimpleNamespace

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


class FakeCameraStruct:
    def __init__(self) -> None:
        self.position = [0.0, 100.0, 300.0]
        self.target = [0.0, 100.0, 0.0]
        self.type = "PERSPECTIVE"


class FakeDomainViewport:
    def __init__(self, struct: FakeCameraStruct) -> None:
        self.struct = struct
        self.writes = []

    def is_main(self) -> bool:
        return True

    def camera_struct(self) -> FakeCameraStruct:
        return self.struct

    def set_camera_struct(self, struct: FakeCameraStruct) -> None:
        self.writes.append(struct)
        self.struct = struct


class FakeViewport:
    def __init__(self, domain: FakeDomainViewport) -> None:
        self.domain = domain

    def domain_viewport(self) -> FakeDomainViewport:
        return self.domain


class FakeDomainScene:
    """Cascadeur's timeline: the cursor may sit outside the animation range."""

    def __init__(self) -> None:
        self.frame = 0
        self.first = 0
        self.last = 123
        self.history = []

    def get_current_frame(self, clamp_animation: bool = True) -> int:
        return int(self.frame)

    def set_current_frame(self, frame: int) -> None:
        self.history.append(int(frame))
        self.frame = int(frame)


class FakeViewScene:
    def __init__(self, domain: FakeDomainScene) -> None:
        self.domain = domain
        self.camera = FakeCameraStruct()
        self.camera_domain = FakeDomainViewport(self.camera)

    def domain_scene(self) -> FakeDomainScene:
        return self.domain

    def viewports(self):
        return [FakeViewport(self.camera_domain)]

    def animation_boundary(self):
        return SimpleNamespace(first_frame=self.domain.first, last_frame=self.domain.last)


class FakeApplication:
    def __init__(self) -> None:
        self.domain = FakeDomainScene()
        self.view = FakeViewScene(self.domain)

    def current_scene(self):
        return self.view


class ContactSheetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Other test files load the package with their own fake csc; start clean and put the
        # previous entries back in tearDownClass, like tests/test_render_tool.py.
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
        sys.modules["numpy"] = fake_module(
            "numpy",
            ndarray=type("ndarray", (), {}),
            generic=type("generic", (), {}),
            float32=float,
            asarray=lambda value, dtype=None: list(value),
        )
        package = fake_module("cascadeur_mcp_plus")
        package.__path__ = [os.path.join(ROOT, "cascadeur_mcp_plus")]
        sys.modules["cascadeur_mcp_plus"] = package
        cls.package = package
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
        cls.contact_sheet = importlib.import_module("cascadeur_mcp_plus.tools.contact_sheet")
        cls.tool = cls.contact_sheet
        cls.camera = importlib.import_module("cascadeur_mcp_plus.camera")

        cls.real_call_on_gui = cls.dispatch.call_on_gui
        cls.dispatch.call_on_gui = lambda fn, args, timeout, idle=False: fn(**args)
        cls.real_render_to_file = cls.render.render_to_file
        cls.real_encode_image = cls.render.encode_image
        cls.real_compose_sheet = cls.contact_sheet._compose_sheet
        cls.renders = []
        cls.compositions = []

        def render_to_file(view_scene, path, width=1280, height=720, samples=None, timeout=180.0):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(png_bytes(width, height))
            cls.renders.append(
                {"path": path, "width": width, "height": height, "samples": samples, "frame": view_scene.domain.frame}
            )
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
            size = cls.render.image_size(path)
            return (
                base64.b64encode(data).decode("ascii"),
                "image/png",
                {
                    "image_size": list(size) if size else None,
                    "scaled": False,
                },
            )

        def compose_sheet(cell_files, frames, columns, cell_width, cell_height, output):
            width, height = cls.contact_sheet.sheet_size(len(cell_files), columns, cell_width, cell_height)
            with open(output, "wb") as handle:
                handle.write(png_bytes(width, height))
            cls.compositions.append(
                {
                    "cells": list(cell_files),
                    "frames": list(frames),
                    "columns": columns,
                    "cell_width": cell_width,
                    "cell_height": cell_height,
                    "output": output,
                    "size": [width, height],
                }
            )
            return {"width": width, "height": height}

        cls.render.render_to_file = render_to_file
        cls.render.encode_image = encode_image
        cls.contact_sheet._compose_sheet = compose_sheet

        # Auto framing collects real controller positions from Cascadeur; the tool tests replace
        # that GUI step with a fake that records its arguments and moves the camera, while the
        # pure geometry (union bbox / preset_camera) is tested directly below.
        cls.camera_calls = []
        cls.real_prepare_camera = cls.contact_sheet._prepare_camera

        def prepare_camera(frames, view, cell_width, cell_height):
            actual = "right" if view == "auto" else view
            plan = {
                "view": actual,
                "position": [111.0, 222.0, 333.0],
                "target": [4.0, 5.0, 6.0],
                "distance": 10.0,
                "fov_deg": 30.0,
                "aspect": 16.0 / 9.0,
                "bbox": {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0], "center": [0.5, 0.5, 0.5]},
            }
            cls.camera_calls.append(
                {
                    "frames": list(frames),
                    "view": view,
                    "cell_width": cell_width,
                    "cell_height": cell_height,
                }
            )
            struct = cls.app.view.camera_domain.struct
            struct.position = list(plan["position"])
            struct.target = list(plan["target"])
            return {
                "view": actual,
                "plan": plan,
                "camera": {
                    "position": list(plan["position"]),
                    "target": list(plan["target"]),
                    "type": "PERSPECTIVE",
                    "distance": plan["distance"],
                    "fov_deg": plan["fov_deg"],
                    "aspect": plan["aspect"],
                    "bbox": plan["bbox"],
                },
                "frames": list(frames),
            }

        cls.contact_sheet._prepare_camera = prepare_camera

    @classmethod
    def tearDownClass(cls) -> None:
        cls.dispatch.call_on_gui = cls.real_call_on_gui
        cls.render.render_to_file = cls.real_render_to_file
        cls.render.encode_image = cls.real_encode_image
        cls.contact_sheet._compose_sheet = cls.real_compose_sheet
        cls.contact_sheet._prepare_camera = cls.real_prepare_camera
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
        self.app.domain.last = 123
        self.app.domain.history = []
        self.app.view.camera_domain.struct = FakeCameraStruct()
        self.app.view.camera_domain.writes = []
        self.renders.clear()
        self.compositions.clear()
        self.camera_calls.clear()
        self.tool._STATE.image = False
        self.saved_temp_dir = self.common.temp_dir

        def temp_dir(*parts):
            path = os.path.join(self.temp.name, *parts) if parts else self.temp.name
            os.makedirs(path, exist_ok=True)
            return path

        self.common.temp_dir = temp_dir
        self.addCleanup(setattr, self.common, "temp_dir", self.saved_temp_dir)

    def _default_path(self) -> str:
        return os.path.join(self.temp.name, "contact_sheets")

    # -- frame planning ------------------------------------------------------

    def test_even_frames_include_both_ends_and_drop_rounding_duplicates(self):
        self.assertEqual(self.tool.even_frames(0, 123), [0, 18, 35, 53, 70, 88, 105, 123])
        self.assertEqual(self.tool.even_frames(0, 0), [0])
        self.assertEqual(self.tool.even_frames(0, 3), [0, 1, 2, 3])
        self.assertEqual(self.tool.even_frames(5, 6), [5, 6])
        self.assertEqual(len(self.tool.even_frames(0, 100)), 8)

    def test_sheet_size_and_cell_height(self):
        self.assertEqual(self.tool.cell_height_for(320), 180)
        self.assertEqual(self.tool.cell_height_for(16), 9)
        self.assertEqual(self.tool.sheet_size(8, 4, 320, 180), (1286, 362))
        self.assertEqual(self.tool.sheet_size(3, 3, 160, 90), (484, 90))
        self.assertEqual(self.tool.sheet_size(1, 1, 32, 18), (32, 18))

    def test_plan_frames_defaults_to_eight_even_frames(self):
        chosen, source = self.tool.plan_frames(first=0, last=123)
        self.assertEqual(chosen, [0, 18, 35, 53, 70, 88, 105, 123])
        self.assertEqual(source, "even sample")

    def test_plan_frames_start_end_step_and_frames(self):
        self.assertEqual(
            self.tool.plan_frames(start=0, end=20, step=10, first=0, last=100), ([0, 10, 20], "start/end/step")
        )
        self.assertEqual(
            self.tool.plan_frames(start=0, end=20, first=0, last=100),
            (list(range(0, 21)), "start/end/step"),
            "an explicit interval without step should walk it one frame at a time",
        )
        self.assertEqual(self.tool.plan_frames(end=5, first=0, last=100), (list(range(0, 6)), "start/end/step"))
        self.assertEqual(self.tool.plan_frames(step=10, first=0, last=100), (list(range(0, 101, 10)), "start/end/step"))
        self.assertEqual(self.tool.plan_frames(frames="20, 0, 10", first=0, last=100), ([0, 10, 20], "frames"))

    # -- auto framing geometry ----------------------------------------------

    def test_camera_mode_normalizes_auto_and_names(self):
        self.assertEqual(self.tool.camera_mode(None), "auto")
        self.assertEqual(self.tool.camera_mode(""), "auto")
        self.assertEqual(self.tool.camera_mode(" FRONT "), "front")
        self.assertEqual(self.tool.camera_mode("current"), "current")

    def test_auto_view_uses_a_side_view_for_forward_motion(self):
        axes = {"right": [1.0, 0.0, 0.0], "up": [0.0, 1.0, 0.0], "forward": [0.0, 0.0, -1.0]}
        self.assertEqual(self.tool.auto_view(axes, [0.0, 0.0, -300.0]), "right")
        self.assertEqual(self.tool.auto_view(axes, [0.0, 0.0, 300.0]), "right")
        self.assertEqual(self.tool.auto_view(axes, [300.0, 0.0, 0.0]), "front")
        self.assertEqual(self.tool.auto_view(axes, [0.0, 0.0, 0.0]), "right")

    def test_union_bbox_and_camera_plan_cover_every_pose(self):
        first = [
            [-80.0, 0.0, -100.0],
            [80.0, 0.0, -100.0],
            [-80.0, 180.0, -100.0],
            [80.0, 180.0, -100.0],
            [0.0, 90.0, -100.0],
        ]
        second = [[x, y + 40.0, z - 320.0] for x, y, z in first]
        points = first + second
        axes = {"right": [1.0, 0.0, 0.0], "up": [0.0, 1.0, 0.0], "forward": [0.0, 0.0, -1.0]}

        bounds = self.tool.union_bbox(points)
        self.assertEqual(bounds["min"], [-80.0, 0.0, -420.0])
        self.assertEqual(bounds["max"], [80.0, 220.0, -100.0])
        self.assertEqual(bounds["center"], [0.0, 110.0, -260.0])

        for view in ("right", "front"):
            with self.subTest(view=view):
                plan = self.tool.plan_motion_camera(points, axes, view, 30.0, 16.0 / 9.0)
                self.assertEqual(plan["bbox"], bounds)
                self.assertEqual(plan["target"], bounds["center"])
                self.assertGreater(plan["distance"], 0.0)
                for point in points:
                    projected = self.camera.project(
                        point, plan["position"], plan["target"], plan["fov_deg"], plan["aspect"]
                    )
                    self.assertTrue(projected["on_screen"], f"{point} is outside the {view} plan: {projected}")
                    self.assertLessEqual(abs(projected["x"]), 0.98)
                    self.assertLessEqual(abs(projected["y"]), 0.98)

    # -- the tool -----------------------------------------------------------

    def test_default_sheet_renders_eight_even_frames(self):
        result = self.tool.render_contact_sheet(cell_width=32)
        self.assertIsInstance(result, tuple)
        data, mime, meta = result
        self.assertEqual(mime, "image/png")
        self.assertEqual(base64.b64decode(data)[:8], b"\x89PNG\r\n\x1a\n")
        self.assertTrue(meta["ok"])
        self.assertTrue(meta["image_content"])
        self.assertEqual(meta["frames"], [0, 18, 35, 53, 70, 88, 105, 123])
        self.assertEqual(meta["requested_frames"], meta["frames"])
        self.assertEqual(meta["selection"], "even sample")
        self.assertEqual(meta["count"], 8)
        self.assertEqual([item["frame"] for item in self.renders], meta["frames"])
        self.assertEqual([item["width"] for item in self.renders], [32] * 8)
        self.assertEqual([item["height"] for item in self.renders], [18] * 8)
        self.assertEqual(meta["columns"], 4)
        self.assertEqual(meta["rows"], 2)
        self.assertEqual(meta["file_size"], [134, 38])
        self.assertEqual(meta["camera_preset"], "auto")
        self.assertEqual(meta["camera_view"], "right")
        self.assertEqual(meta["camera_frames"], meta["frames"])
        self.assertEqual(meta["camera"]["position"], [111.0, 222.0, 333.0])
        self.assertEqual(len(self.camera_calls), 1)
        self.assertEqual(self.camera_calls[0]["frames"], meta["frames"])
        self.assertEqual(self.camera_calls[0]["view"], "auto")
        self.assertEqual(self.camera_calls[0]["cell_width"], 32)
        self.assertEqual(self.camera_calls[0]["cell_height"], 18)
        self.assertEqual(self.app.domain.history, [*meta["frames"], 0])
        self.assertTrue(meta["frame_restored"])
        self.assertTrue(meta["camera_restored"])
        self.assertEqual(self.app.view.camera_domain.struct.position, [0.0, 100.0, 300.0])
        self.assertTrue(os.path.isfile(meta["path"]))
        self.assertEqual(
            os.path.normcase(os.path.dirname(meta["path"])),
            os.path.normcase(os.path.join(self.temp.name, "contact_sheets")),
        )
        self.assertEqual(len(self.compositions), 1)
        self.assertEqual(self.compositions[0]["frames"], meta["frames"])
        self.assertTrue(bool(self.tool.TOOLS[0]["image"]))

    def test_frames_argument_and_columns_produce_three_cells(self):
        target = os.path.join(self.temp.name, "three.png")
        result = self.tool.render_contact_sheet(frames=[0, 10, 20], columns=3, cell_width=32, path=target)
        _data, _mime, meta = result
        self.assertTrue(meta["ok"], meta)
        self.assertEqual(meta["frames"], [0, 10, 20])
        self.assertEqual(meta["selection"], "frames")
        self.assertEqual(meta["count"], 3)
        self.assertEqual(meta["rows"], 1)
        self.assertEqual(meta["columns"], 3)
        self.assertEqual(meta["file_size"], [100, 18])
        self.assertEqual(meta["path"], target)
        self.assertEqual(meta["current_frame"], 0)
        self.assertEqual(meta["camera_preset"], "auto")
        self.assertEqual(meta["camera_view"], "right")
        self.assertEqual(self.camera_calls[0]["frames"], [0, 10, 20])
        self.assertEqual(self.camera_calls[0]["view"], "auto")
        self.assertEqual(self.app.domain.history, [0, 10, 20, 0])
        self.assertEqual([item["frame"] for item in self.renders], [0, 10, 20])
        self.assertEqual(len(self.compositions), 1)
        self.assertEqual(self.compositions[0]["columns"], 3)

    def test_start_end_step_selects_the_interval(self):
        result = self.tool.render_contact_sheet(start=0, end=20, step=10, cell_width=16)
        _data, _mime, meta = result
        self.assertTrue(meta["ok"], meta)
        self.assertEqual(meta["frames"], [0, 10, 20])
        self.assertEqual(meta["selection"], "start/end/step")
        self.assertEqual(meta["columns"], 3, "a 3-frame sheet drops the unused fourth column")
        self.assertEqual(meta["cell_height"], 9)
        self.assertEqual(meta["file_size"], [52, 9])

    def test_conflicting_frame_arguments_are_refused(self):
        result = self.tool.render_contact_sheet(frames=[0], start=0)
        self.assertFalse(result["ok"])
        self.assertIn("either frames", result["error"])
        self.assertEqual(self.renders, [])
        self.assertEqual(self.app.domain.history, [])

    def test_bad_arguments_are_reported_before_rendering(self):
        target = os.path.join(self.temp.name, "bad.png")
        cases = [
            {"columns": 0},
            {"cell_width": 8},
            {"path": "relative.png"},
            {"path": target[:-4] + ".jpg"},
            {"frames": []},
            {"start": 20, "end": 0},
            {"step": 0, "end": 20},
            {"frames": list(range(0, 65))},
            {"frames": list(range(8)), "cell_width": 4096},
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.tool.render_contact_sheet(**arguments)
                self.assertFalse(result["ok"], arguments)
        self.assertEqual(self.renders, [], "nothing may be rendered for a bad argument")
        self.assertEqual(self.app.domain.history, [], "the playhead must not move for a refused call")
        self.assertFalse(bool(self.tool.TOOLS[0]["image"]))

    def test_existing_file_is_refused_without_overwrite(self):
        target = os.path.join(self.temp.name, "taken.png")
        with open(target, "wb") as handle:
            handle.write(b"old")
        refused = self.tool.render_contact_sheet(frames=[0], cell_width=32, path=target)
        self.assertFalse(refused["ok"])
        self.assertIn("overwrite", refused["error"])
        self.assertEqual(self.renders, [])
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), b"old")
        allowed = self.tool.render_contact_sheet(frames=[0], cell_width=32, path=target, overwrite=True)
        self.assertTrue(allowed[2]["ok"], allowed[2])
        with open(target, "rb") as handle:
            self.assertEqual(handle.read()[:8], b"\x89PNG\r\n\x1a\n")

    def test_playhead_and_camera_are_restored_when_a_render_fails(self):
        self.app.domain.frame = 60
        calls = {"count": 0}
        working = self.render.render_to_file

        def failing(view_scene, path, width=1280, height=720, samples=None, timeout=180.0):
            calls["count"] += 1
            if calls["count"] == 2:
                raise self.render.ToolError("renderer exploded")
            return working(view_scene, path, width, height, samples, timeout)

        self.render.render_to_file = failing
        self.addCleanup(setattr, self.render, "render_to_file", working)
        result = self.tool.render_contact_sheet(frames=[0, 1, 2], cell_width=32)
        self.assertFalse(result["ok"])
        self.assertIn("renderer exploded", result["error"])
        self.assertEqual(self.app.domain.frame, 60)
        self.assertEqual(self.app.domain.history, [0, 1, 60])
        self.assertEqual(self.app.view.camera_domain.struct.position, [0.0, 100.0, 300.0])
        self.assertFalse(bool(self.tool.TOOLS[0]["image"]))
        leftovers = [name for name in os.listdir(self._default_path()) if name.startswith("cells-")]
        self.assertEqual(leftovers, [], "the temporary cell folder must be removed after a failure")

    def test_explicit_camera_preset_frames_the_union_from_that_view(self):
        target = os.path.join(self.temp.name, "preset.png")
        result = self.tool.render_contact_sheet(
            frames=[0, 10, 20], columns=3, cell_width=32, camera_preset="front", path=target
        )
        _data, _mime, meta = result
        self.assertTrue(meta["ok"], meta)
        self.assertEqual(meta["camera_preset"], "front")
        self.assertEqual(meta["camera_view"], "front")
        self.assertEqual(meta["camera"]["position"], [111.0, 222.0, 333.0])
        self.assertTrue(meta["camera_restored"])
        self.assertEqual(len(self.camera_calls), 1)
        self.assertEqual(self.camera_calls[0]["view"], "front")
        self.assertEqual(self.camera_calls[0]["frames"], [0, 10, 20])
        self.assertEqual(self.app.view.camera_domain.struct.position, [0.0, 100.0, 300.0])

    def test_current_camera_preset_keeps_the_viewport_camera(self):
        before = list(self.app.view.camera_domain.struct.position)
        target = os.path.join(self.temp.name, "current.png")
        result = self.tool.render_contact_sheet(frames=[0], cell_width=32, camera_preset="current", path=target)
        _data, _mime, meta = result
        self.assertTrue(meta["ok"], meta)
        self.assertEqual(meta["camera_preset"], "current")
        self.assertIsNone(meta["camera_view"])
        self.assertIsNone(meta["camera"])
        self.assertEqual(meta["camera_frames"], [])
        self.assertTrue(meta["camera_restored"])
        self.assertEqual(self.camera_calls, [], "current must not fit or move the camera")
        self.assertEqual(self.app.view.camera_domain.struct.position, before)

    def test_unknown_camera_preset_is_refused(self):
        result = self.tool.render_contact_sheet(frames=[0], camera_preset="sideways")
        self.assertFalse(result["ok"])
        self.assertIn("Unknown camera_preset", result["error"])
        self.assertEqual(self.renders, [])
        self.assertEqual(self.app.domain.history, [])

    def test_camera_preset_without_camera_tools_fails_before_rendering(self):
        # T31 is merged, so its camera module is on disk and the import in
        # _camera_preset_available() always succeeds. Exercise the fallback path by making the
        # availability check report a checkout without T31.
        original = self.tool._camera_preset_available
        self.tool._camera_preset_available = lambda: False
        self.addCleanup(setattr, self.tool, "_camera_preset_available", original)
        for arguments in ({"camera_preset": "front"}, {}):
            with self.subTest(arguments=arguments):
                result = self.tool.render_contact_sheet(frames=[0], cell_width=32, **arguments)
                self.assertFalse(result["ok"])
                self.assertIn("T31", result["error"])
                self.assertIn("current", result["hint"])
        self.assertEqual(self.renders, [])
        self.assertEqual(self.app.domain.history, [])
        self.assertEqual(self.camera_calls, [])

    def test_minimized_window_is_refused_without_touching_the_playhead(self):
        self.ui.MINIMIZED = True
        result = self.tool.render_contact_sheet(frames=[0], cell_width=32)
        self.assertFalse(result["ok"])
        self.assertIn("minimized", result["error"])
        self.assertIn("restore", result["hint"])
        self.assertEqual(self.renders, [])
        self.assertEqual(self.app.domain.history, [])

    def test_schema_documents_every_argument(self):
        properties = self.tool.TOOLS[0]["inputSchema"]["properties"]
        self.assertEqual(
            set(properties),
            {"start", "end", "step", "frames", "columns", "cell_width", "camera_preset", "path", "overwrite"},
        )
        self.assertEqual(self.tool.TOOLS[0]["name"], "render_contact_sheet")
        self.assertEqual(properties["camera_preset"]["default"], "auto")
        self.assertIn("handler", self.tool.TOOLS[0])
        self.assertNotIn("fn", self.tool.TOOLS[0])


if __name__ == "__main__":
    unittest.main()
