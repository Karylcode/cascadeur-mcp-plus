"""Tests for the render_video / render_image_sequence tool layer (no Cascadeur, no Qt).

The tool module imports Cascadeur and PySide6 lazily; this file builds the same small fakes as
tests/test_render_tool.py and tests/test_contact_sheet.py, replaces the renderer, the Export Video
dialog and the display helpers, and drives a whole background job by calling fn, poll and cancel
directly. The MP4 parser is fed hand-built MP4 boxes; the live tests exercise the real renderer.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

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


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + kind + payload


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


def mp4_bytes(frames: int, fps: int = 30) -> bytes:
    """A tiny but structurally valid MP4: ftyp + moov(mvhd + trak/mdia/minf/stbl/stsz)."""
    timescale = fps
    duration = int(round(frames * timescale / fps))
    mvhd = box(b"mvhd", b"\x00" * 12 + struct.pack(">II", timescale, duration) + b"\x00" * 80)
    stsz = box(b"stsz", b"\x00" * 8 + struct.pack(">I", frames))
    stbl = box(b"stbl", stsz)
    minf = box(b"minf", stbl)
    mdia = box(b"mdia", minf)
    trak = box(b"trak", mdia)
    moov = box(b"moov", mvhd + trak)
    ftyp = box(b"ftyp", b"isom" + b"\x00\x00\x02\x00" + b"isomiso2")
    return ftyp + moov + box(b"mdat", b"\x00" * 16)


def fake_module(name: str, **attributes) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


class FakeBoundary:
    def __init__(self, first=0, last=7) -> None:
        self.first_frame = first
        self.last_frame = last
        self.first_visible_frame = first
        self.last_visible_frame = last


class FakeDomainScene:
    def __init__(self) -> None:
        self.frame = 0

    def get_current_frame(self, clamp_animation: bool = True) -> int:
        return int(self.frame)

    def set_current_frame(self, frame: int) -> None:
        self.frame = int(frame)


class FakeViewScene:
    def __init__(self) -> None:
        self.boundary = FakeBoundary()
        self.domain = FakeDomainScene()
        self.fps = 30.0

    def domain_scene(self) -> FakeDomainScene:
        return self.domain

    def animation_boundary(self) -> FakeBoundary:
        return self.boundary

    def get_setting_handler(self):
        return types.SimpleNamespace(get_float_value=lambda group, key: self.fps)


class FakeRenderParameters:
    def __init__(self) -> None:
        self.width = 0
        self.height = 0
        self.samples = 0


class FakeRenderTool:
    def __init__(self) -> None:
        self.video_calls = []
        self.sequence_calls = []

    def play_to_video_file(self, view_scene, parameters, path):
        boundary = view_scene.animation_boundary()
        frames = boundary.last_visible_frame - boundary.first_visible_frame + 1
        self.video_calls.append(
            {"path": path, "width": parameters.width, "height": parameters.height, "frames": frames}
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(mp4_bytes(frames, int(view_scene.fps)))

    def play_to_images_sequence(self, view_scene, parameters, folder):
        boundary = view_scene.animation_boundary()
        frames = boundary.last_visible_frame - boundary.first_visible_frame + 1
        self.sequence_calls.append(
            {"folder": folder, "width": parameters.width, "height": parameters.height, "frames": frames}
        )
        os.makedirs(folder, exist_ok=True)
        for index in range(frames):
            with open(os.path.join(folder, f"frame_{index:04d}.png"), "wb") as handle:
                handle.write(png_bytes(parameters.width, parameters.height))


class FakeToolsManager:
    def __init__(self, tool) -> None:
        self.tool = tool

    def get_tool(self, name):
        return self.tool if name == "RenderToFile" else None


class FakeApplication:
    def __init__(self) -> None:
        self.view = FakeViewScene()
        self.render_tool = FakeRenderTool()

    def current_scene(self) -> FakeViewScene:
        return self.view

    def get_tools_manager(self) -> FakeToolsManager:
        return FakeToolsManager(self.render_tool)


class FakeDisplay:
    """The display module's state and actions, without any QML."""

    def __init__(self) -> None:
        self.state = {
            "view_mode": "point_controller",
            "ghosts": "previous",
            "ghost_base_mode": "previous",
            "ghost_keyframes": False,
            "trajectory_mode": "all",
            "trajectory_active": True,
            "only_key_frames": False,
            "silhouette": False,
            "surface": True,
        }
        self.calls = []

    def read(self):
        return dict(self.state)

    def set_view_mode_direct(self, mode):
        self.calls.append(("view", mode))
        self.state["view_mode"] = mode

    def call_action(self, action):
        self.calls.append(("action", action))
        if "Disable ghost" in action:
            self.state["ghost_base_mode"] = "none"
            self.state["ghosts"] = "none"
        elif "Previous frames ghosts" in action:
            self.state["ghost_base_mode"] = "previous"
        elif "All trajectories" in action:
            self.state["trajectory_mode"] = "all"
            self.state["trajectory_active"] = True
        elif "Switch trajectory activity" in action:
            self.state["trajectory_active"] = not self.state["trajectory_active"]
        self.state["ghosts"] = (
            "keyframes"
            if self.state["ghost_keyframes"] and self.state["ghost_base_mode"] not in (None, "none")
            else self.state["ghost_base_mode"]
        )


class VideoToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
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
            tools=types.SimpleNamespace(RenderParameters=FakeRenderParameters),
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
        package = fake_module(PACKAGE)
        package.__path__ = [os.path.join(ROOT, PACKAGE)]
        sys.modules[PACKAGE] = package
        cls.package = package
        tools_package = fake_module(PACKAGE + ".tools")
        tools_package.__path__ = [os.path.join(ROOT, PACKAGE, "tools")]
        sys.modules[PACKAGE + ".tools"] = tools_package

        cls.ui = fake_module(PACKAGE + ".ui")
        cls.ui.MINIMIZED = False
        cls.ui.resolve_window = lambda window=None, include_hidden=False: (0, "main window")
        cls.ui._is_minimized = lambda window: cls.ui.MINIMIZED
        cls.ui.quick_windows = lambda include_hidden=False: []
        sys.modules[PACKAGE + ".ui"] = cls.ui

        cls.common = importlib.import_module(PACKAGE + ".common")
        cls.dispatch = importlib.import_module(PACKAGE + ".dispatch")
        cls.render = importlib.import_module(PACKAGE + ".render")
        cls.camera = importlib.import_module(PACKAGE + ".camera")
        cls.timeline = importlib.import_module(PACKAGE + ".timeline")
        cls.display = importlib.import_module(PACKAGE + ".tools.display")
        cls.video = importlib.import_module(PACKAGE + ".tools.video")
        cls.tool = cls.video

        cls.display_impl = FakeDisplay()
        cls.real_display = {
            "read": cls.display._read_state,
            "view": cls.display._set_view_mode_direct,
            "action": cls.display._call_action,
        }
        cls.display._read_state = cls.display_impl.read
        cls.display._set_view_mode_direct = cls.display_impl.set_view_mode_direct
        cls.display._call_action = cls.display_impl.call_action

        cls.real_camera = {
            "snapshot": cls.camera.snapshot,
            "restore": cls.camera.restore,
            "write": cls.camera.write_camera,
        }
        cls.camera_writes = []
        cls.camera.snapshot = lambda: {
            "position": [0.0, 100.0, 300.0],
            "target": [0.0, 100.0, 0.0],
            "type": "PERSPECTIVE",
        }
        cls.camera.write_camera = lambda position=None, target=None, type_name=None: (
            cls.camera_writes.append({"position": list(position), "target": list(target), "type": type_name})
            or {"position": list(position), "target": list(target), "type": type_name}
        )
        cls.camera.restore = lambda saved: cls.camera_writes.append({"restore": saved}) or saved

        cls.motion_calls = []
        cls.real_motion_helpers = cls.video._motion_helpers
        cls.fake_helpers = types.SimpleNamespace(
            _collect_motion=lambda frames: (
                cls.motion_calls.append(list(frames))
                or {
                    "points": [[0.0, 100.0, 0.0], [10.0, 150.0, 20.0]],
                    "axes": {"right": [1.0, 0.0, 0.0], "up": [0.0, 1.0, 0.0], "forward": [0.0, 0.0, 1.0]},
                    "movement": [5.0, 0.0, 0.0],
                    "fov_deg": 30.0,
                }
            ),
            auto_view=lambda axes, movement: "front",
            plan_motion_camera=lambda points, axes, view, fov_deg=30.0, aspect=1.0: {
                "view": view,
                "position": [100.0, 200.0, 300.0],
                "target": [0.0, 100.0, 0.0],
                "distance": 400.0,
                "fov_deg": fov_deg,
                "aspect": aspect,
                "bbox": {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0], "center": [0.5, 0.5, 0.5]},
            },
        )
        cls.video._motion_helpers = lambda: cls.fake_helpers

        cls.real_schedule = cls.video._schedule
        cls.scheduled = []
        cls.run_scheduled = True
        cls.video._schedule = lambda callback, delay_ms=0: (
            cls.scheduled.append(callback) or (callback() if cls.run_scheduled else None)
        )
        cls.real_close = cls.video._close_dialog
        cls.dialog_closed = True
        cls.video._close_dialog = lambda session, stop=False: cls.dialog_closed
        cls.real_dialog_state = cls.video._dialog_state
        cls.dialog_state = None
        cls.video._dialog_state = lambda session=None: cls.dialog_state

    @classmethod
    def tearDownClass(cls) -> None:
        cls.display._read_state = cls.real_display["read"]
        cls.display._set_view_mode_direct = cls.real_display["view"]
        cls.display._call_action = cls.real_display["action"]
        cls.camera.snapshot = cls.real_camera["snapshot"]
        cls.camera.restore = cls.real_camera["restore"]
        cls.camera.write_camera = cls.real_camera["write"]
        cls.video._motion_helpers = cls.real_motion_helpers
        cls.video._close_dialog = cls.real_close
        cls.video._dialog_state = cls.real_dialog_state
        cls.video._schedule = cls.real_schedule
        for name in [n for n in sys.modules if n == PACKAGE or n.startswith(PACKAGE + ".")]:
            del sys.modules[name]
        for name, module in cls.saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.video._STATE[self.video.SESSION_KEY] = None
        self.video.VERIFY_DELAY = 0.0
        self.video.START_TIMEOUT = 30.0
        self.ui.MINIMIZED = False
        self.app.view = FakeViewScene()
        self.app.view.domain.frame = 0
        self.app.render_tool = FakeRenderTool()
        self.app.render_tool.video_calls.clear()
        self.app.render_tool.sequence_calls.clear()
        self.camera_writes.clear()
        self.motion_calls.clear()
        self.display_impl.calls.clear()
        self.display_impl.state = {
            "view_mode": "point_controller",
            "ghosts": "previous",
            "ghost_base_mode": "previous",
            "ghost_keyframes": False,
            "trajectory_mode": "all",
            "trajectory_active": True,
            "only_key_frames": False,
            "silhouette": False,
            "surface": True,
        }
        self.dialog_state = None
        self.dialog_closed = True
        self.tool._STATE[self.tool.SESSION_KEY] = None
        self.video._motion_helpers = lambda: self.fake_helpers
        self.scheduled = []
        self.run_scheduled = True
        self.video._schedule = lambda callback, delay_ms=0: (
            self.scheduled.append(callback) or (callback() if self.run_scheduled else None)
        )

        def close(session, stop=False):
            self.dialog_state = None
            return self.dialog_closed

        self.video._close_dialog = close
        self.video._dialog_state = lambda session=None: self.dialog_state

    # -- pure helpers --------------------------------------------------------

    def test_plan_range_defaults_to_the_animation_and_checks_order(self):
        self.assertEqual(self.video.plan_range(0, 123), (0, 123, "animation"))
        self.assertEqual(self.video.plan_range(0, 123, start=10, end=20), (10, 20, "requested range"))
        self.assertEqual(self.video.plan_range(0, 123, start="10"), (10, 123, "requested range"))
        with self.assertRaises(self.video.ToolError):
            self.video.plan_range(0, 123, start=20, end=10)

    def test_camera_modes(self):
        self.assertEqual(self.video.check_camera_mode(None), "auto")
        self.assertEqual(self.video.check_camera_mode(" Current "), "current")
        self.assertEqual(self.video.check_camera_mode("three_quarter"), "three_quarter")
        with self.assertRaises(self.video.ToolError):
            self.video.check_camera_mode("sideways")

    def test_mp4_info_reads_duration_frames_and_fps(self):
        path = os.path.join(self.temp.name, "clip.mp4")
        with open(path, "wb") as handle:
            handle.write(mp4_bytes(124, 30))
        info = self.video.mp4_info(path)
        self.assertEqual(info["sample_count"], 124)
        self.assertEqual(info["timescale"], 30)
        self.assertAlmostEqual(info["duration_s"], 124 / 30, places=3)
        self.assertAlmostEqual(info["fps"], 30.0, places=2)
        self.assertEqual(info["brand"], "isom")

    def test_mp4_info_rejects_a_non_mp4(self):
        path = os.path.join(self.temp.name, "not.mp4")
        with open(path, "wb") as handle:
            handle.write(b"not a video")
        with self.assertRaises(self.video.ToolError):
            self.video.mp4_info(path)

    def test_video_complete_waits_for_a_stable_mp4(self):
        path = os.path.join(self.temp.name, "clip.mp4")
        with open(path, "wb") as handle:
            handle.write(mp4_bytes(4))
        seen = {"size": os.path.getsize(path), "changed_at": 0.0}
        self.assertTrue(self.video.video_complete(path, stable=0.0, seen=seen))
        partial = os.path.join(self.temp.name, "partial.mp4")
        with open(partial, "wb") as handle:
            handle.write(b"\x00\x00\x00\x08mdat")
        self.assertFalse(self.video.video_complete(partial, stable=0.0))

    def test_ghost_and_trajectory_action_plans(self):
        state = {"ghost_base_mode": "previous", "ghost_keyframes": False}
        self.assertEqual(self.video._ghost_actions(state, "none", False), [self.display.GHOST_ACTIONS["none"]])
        state = {"ghost_base_mode": "none", "ghost_keyframes": False}
        self.assertEqual(self.video._ghost_actions(state, "previous", False), [self.display.GHOST_ACTIONS["previous"]])
        state = {"ghost_base_mode": "none", "ghost_keyframes": True}
        self.assertEqual(
            self.video._ghost_actions(state, "none", False),
            [self.display.GHOST_ACTIONS["keyframes"]],
        )
        trajectory = {"trajectory_active": True, "trajectory_mode": "all", "only_key_frames": False}
        self.assertEqual(self.video._trajectory_actions(trajectory, "off", None), [self.display.TRAJECTORY_SWITCH])

    # -- the background job --------------------------------------------------

    def test_video_job_runs_to_done_and_restores_everything(self):
        target = os.path.join(self.temp.name, "shot.mp4")
        self.app.view.domain.frame = 3
        started = self.video.render_video(path=target, width=320, height=180, samples=4)
        self.assertTrue(started["ok"], started)
        self.assertEqual(started["range"], [0, 7])
        self.assertEqual(self.app.render_tool.video_calls[0]["width"], 320)
        self.assertEqual(self.app.render_tool.video_calls[0]["height"], 180)
        self.assertEqual(self.app.render_tool.video_calls[0]["frames"], 8)
        temporary = self.video._STATE[self.video.SESSION_KEY]["temporary"]
        self.assertTrue(os.path.isfile(temporary))
        self.assertEqual(self.display_impl.state["view_mode"], "mesh")
        self.assertEqual(self.display_impl.state["ghosts"], "none")
        self.assertFalse(self.display_impl.state["trajectory_active"])
        self.assertEqual(self.app.view.boundary.first_visible_frame, 0)
        self.assertEqual(self.app.view.boundary.last_visible_frame, 7)
        self.assertEqual(self.motion_calls, [list(range(0, 8))])
        self.assertEqual(started["camera_preset"], "front")

        self.dialog_state = {"in_progress": True, "done": False, "setup": False, "progress": 0.4}
        running = self.video.poll()
        self.assertFalse(running["done"], running)
        self.assertAlmostEqual(running["progress"], 0.4, places=3)

        self.dialog_state = {"in_progress": False, "done": True, "setup": False, "progress": 1.0}
        finishing = self.video.poll()
        self.assertFalse(finishing["done"])
        self.assertAlmostEqual(finishing["progress"], 1.0)

        result = self.video.poll()
        self.assertTrue(result["done"], result)
        meta = result["result"]
        self.assertTrue(meta["ok"], meta)
        self.assertEqual(meta["frames"], 8)
        self.assertAlmostEqual(meta["duration_s"], 8 / 30, places=5)
        self.assertEqual(meta["file_bytes"], os.path.getsize(target))
        self.assertTrue(meta["frame_restored"])
        self.assertTrue(meta["range_restored"])
        self.assertTrue(meta["camera_restored"])
        self.assertTrue(meta["display_restored"])
        self.assertTrue(meta["dialog_closed"])
        self.assertTrue(meta["frame_count_matches"])
        self.assertEqual(self.app.view.domain.frame, 3)
        self.assertEqual(self.app.view.boundary.first_visible_frame, 0)
        self.assertEqual(self.app.view.boundary.last_visible_frame, 7)
        self.assertEqual(self.display_impl.state["view_mode"], "point_controller")
        self.assertEqual(self.display_impl.state["ghosts"], "previous")
        self.assertTrue(self.display_impl.state["trajectory_active"])
        self.assertTrue(os.path.isfile(target))
        self.assertFalse(os.path.exists(temporary))

    def test_start_end_narrows_the_rendered_range(self):
        target = os.path.join(self.temp.name, "range.mp4")
        started = self.video.render_video(path=target, start=2, end=4, width=64, height=36)
        self.assertTrue(started["ok"], started)
        self.assertEqual(started["range"], [2, 4])
        self.assertEqual(self.app.render_tool.video_calls[0]["frames"], 3)
        self.dialog_state = {"in_progress": True, "done": False, "setup": False, "progress": 0.9}
        self.video.poll()
        self.dialog_state = {"in_progress": False, "done": True, "setup": False, "progress": 1.0}
        self.video.poll()
        result = self.video.poll()
        self.assertTrue(result["result"]["ok"], result)
        self.assertEqual(result["result"]["frames"], 3)
        self.assertEqual(result["result"]["requested_range"], [2, 4])

    def test_cancel_presses_stop_restores_and_deletes_the_partial_file(self):
        target = os.path.join(self.temp.name, "cancelled.mp4")
        self.app.view.domain.frame = 5
        self.assertTrue(self.video.render_video(path=target, width=64, height=36)["ok"])
        temporary = self.video._STATE[self.video.SESSION_KEY]["temporary"]
        calls = []
        self.video._close_dialog = lambda session, stop=False: calls.append(stop) or True
        self.dialog_state = {"in_progress": True, "done": False, "setup": False, "progress": 0.2}
        self.video.poll()
        self.video.cancel()
        self.assertTrue(calls and calls[-1] is True, "cancel must ask for the Stop button")
        self.assertIsNone(self.video._STATE[self.video.SESSION_KEY])
        self.assertFalse(os.path.exists(temporary))
        self.assertFalse(os.path.exists(target))
        self.assertEqual(self.app.view.domain.frame, 5)
        self.assertEqual(self.app.view.boundary.last_visible_frame, 7)
        self.assertEqual(self.display_impl.state["view_mode"], "point_controller")

    def test_existing_file_is_refused_without_overwrite(self):
        target = os.path.join(self.temp.name, "taken.mp4")
        with open(target, "wb") as handle:
            handle.write(b"old")
        result = self.video.render_video(path=target, width=64, height=36)
        self.assertFalse(result["ok"])
        self.assertIn("overwrite", result["error"])
        self.assertIsNone(self.video._STATE[self.video.SESSION_KEY])
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), b"old")

    def test_existing_file_can_be_overwritten(self):
        target = os.path.join(self.temp.name, "replace.mp4")
        with open(target, "wb") as handle:
            handle.write(b"old")
        started = self.video.render_video(path=target, width=64, height=36, overwrite=True)
        self.assertTrue(started["ok"], started)
        self.dialog_state = {"in_progress": True, "done": False, "setup": False, "progress": 0.99}
        self.video.poll()
        self.dialog_state = {"in_progress": False, "done": True, "setup": False, "progress": 1.0}
        self.video.poll()
        result = self.video.poll()
        self.assertTrue(result["result"]["ok"], result)
        with open(target, "rb") as handle:
            self.assertTrue(handle.read().startswith(b"\x00\x00\x00"))

    def test_auto_camera_requires_the_t33_helpers(self):
        self.video._motion_helpers = lambda: (_ for _ in ()).throw(self.video.ToolError("T33-fix missing"))
        result = self.video.render_video(path=os.path.join(self.temp.name, "auto.mp4"), width=64, height=36)
        self.assertFalse(result["ok"])
        self.assertIn("T33-fix", result["error"])
        self.assertEqual(self.app.view.boundary.first_visible_frame, 0)
        self.assertEqual(self.display_impl.calls, [])
        self.assertEqual(self.app.render_tool.video_calls, [])

    def test_current_camera_skips_motion_framing_and_camera_restore(self):
        target = os.path.join(self.temp.name, "current.mp4")
        started = self.video.render_video(path=target, width=64, height=36, camera_preset="current")
        self.assertTrue(started["ok"], started)
        self.assertEqual(self.motion_calls, [])
        self.assertEqual(self.camera_writes, [])
        self.dialog_state = {"in_progress": True, "done": False, "setup": False, "progress": 0.5}
        self.video.poll()
        self.dialog_state = {"in_progress": False, "done": True, "setup": False, "progress": 1.0}
        self.video.poll()
        result = self.video.poll()
        self.assertIsNone(result["result"]["camera_restored"])
        self.assertTrue(result["result"]["ok"], result)

    def test_sequence_job_moves_the_frames_into_an_empty_folder(self):
        folder = os.path.join(self.temp.name, "frames")
        started = self.video.render_image_sequence(folder=folder, start=0, end=2, width=32, height=18)
        self.assertTrue(started["ok"], started)
        self.assertEqual(self.app.render_tool.sequence_calls[0]["frames"], 3)
        self.dialog_state = {"in_progress": True, "done": False, "setup": False, "progress": 0.5}
        self.video.poll()
        self.dialog_state = {"in_progress": False, "done": True, "setup": False, "progress": 1.0}
        self.video.poll()
        result = self.video.poll()
        meta = result["result"]
        self.assertTrue(meta["ok"], meta)
        self.assertEqual(meta["frames"], 3)
        self.assertEqual(len(os.listdir(folder)), 3)
        self.assertTrue(all(name.endswith(".png") for name in os.listdir(folder)))
        self.assertTrue(meta["frame_restored"])
        self.assertTrue(meta["display_restored"])

    def test_sequence_refuses_a_non_empty_folder(self):
        folder = os.path.join(self.temp.name, "full")
        os.makedirs(folder)
        with open(os.path.join(folder, "old.png"), "wb") as handle:
            handle.write(b"old")
        result = self.video.render_image_sequence(folder=folder, width=32, height=18)
        self.assertFalse(result["ok"])
        self.assertIn("not empty", result["error"])
        self.assertEqual(self.app.render_tool.sequence_calls, [])

    def test_minimized_window_is_refused_before_touching_the_scene(self):
        self.ui.MINIMIZED = True
        result = self.video.render_video(path=os.path.join(self.temp.name, "min.mp4"), width=64, height=36)
        self.assertFalse(result["ok"])
        self.assertIn("minimized", result["error"])
        self.assertEqual(self.app.view.boundary.first_visible_frame, 0)
        self.assertEqual(self.display_impl.calls, [])

    def test_a_second_render_is_refused_while_one_is_running(self):
        path = os.path.join(self.temp.name, "one.mp4")
        self.assertTrue(self.video.render_video(path=path, width=64, height=36)["ok"])
        second = self.video.render_video(path=os.path.join(self.temp.name, "two.mp4"), width=64, height=36)
        self.assertFalse(second["ok"])
        self.assertIn("already running", second["error"])
        self.video.cancel()

    def test_the_failed_render_restores_the_scene_and_removes_the_temp_file(self):
        target = os.path.join(self.temp.name, "fail.mp4")
        self.app.view.domain.frame = 2
        self.assertTrue(self.video.render_video(path=target, width=64, height=36)["ok"])
        temporary = self.video._STATE[self.video.SESSION_KEY]["temporary"]
        self.dialog_state = {"in_progress": False, "done": False, "setup": True, "progress": 0.0}
        self.video.START_TIMEOUT = 0.0
        self.addCleanup(setattr, self.video, "START_TIMEOUT", 30.0)
        result = self.video.poll()
        self.assertFalse(result["ok"], result)
        self.assertIn("did not start", result["error"])
        self.assertIsNone(self.video._STATE[self.video.SESSION_KEY])
        self.assertFalse(os.path.exists(temporary))
        self.assertEqual(self.app.view.domain.frame, 2)
        self.assertEqual(self.app.view.boundary.first_visible_frame, 0)
        self.assertEqual(self.display_impl.state["view_mode"], "point_controller")

    def test_export_starts_on_a_later_event_loop_turn(self):
        # RenderToFile's video export runs Cascadeur's own loop; starting it inside the
        # dispatcher's QTimer job stack crashes Cascadeur, so begin must schedule it.
        self.run_scheduled = False
        path = os.path.join(self.temp.name, "scheduled.mp4")
        started = self.video.render_video(path=path, width=64, height=36)
        self.assertTrue(started["ok"], started)
        self.assertEqual(self.app.render_tool.video_calls, [], "the export ran inside begin()")
        self.assertEqual(len(self.scheduled), 1, "begin did not schedule the export")
        self.assertEqual(self.video._STATE[self.video.SESSION_KEY]["phase"], "scheduled")
        self.scheduled.pop()()
        self.assertEqual(len(self.app.render_tool.video_calls), 1)
        self.assertTrue(self.video._STATE[self.video.SESSION_KEY]["render_called"])

    def test_cancel_before_the_scheduled_start_does_not_render(self):
        self.run_scheduled = False
        path = os.path.join(self.temp.name, "cancelled-early.mp4")
        self.assertTrue(self.video.render_video(path=path, width=64, height=36)["ok"])
        self.video.cancel()  # both the export start and the cleanup are queued now
        self.run_scheduled = True
        while self.scheduled:
            self.scheduled.pop(0)()
        self.assertEqual(self.app.render_tool.video_calls, [], "the cancelled export still started")
        self.assertIsNone(self.video._STATE[self.video.SESSION_KEY])

    def test_window_state_reads_percentage_buttons_and_progress_bar(self):
        class FakeItem:
            def __init__(self, info, value=None):
                self.info = info
                self._value = value

            def property(self, name):
                return self._value if name == "value" else None

        def item(text=None, type_name="Text", clickable=False, value=None):
            info = {"type": type_name}
            if text:
                info["text"] = text
            if clickable:
                info["clickable"] = True
            return FakeItem(info, value)

        original_walk = getattr(self.ui, "_walk", None)
        original_describe = getattr(self.ui, "_describe", None)
        self.addCleanup(setattr, self.ui, "_walk", original_walk)
        self.addCleanup(setattr, self.ui, "_describe", original_describe)
        self.ui._describe = lambda entry: entry.info

        setup_items = [
            item("Export video"),
            item("Start rendering", "Button", clickable=True),
            item("VIDEO FILE", "LinkButton", clickable=True),
        ]
        self.ui._walk = lambda window, visible: iter(setup_items)
        state = self.video._window_state(object())
        self.assertTrue(state["setup"])
        self.assertFalse(state["in_progress"])
        self.assertFalse(state["done"])

        progress_items = [
            item("Rendering timeline"),
            item("42 %"),
            item("Stop", "Button", clickable=True),
            item("", "ProgressBar", value=0.42),
        ]
        self.ui._walk = lambda window, visible: iter(progress_items)
        state = self.video._window_state(object())
        self.assertTrue(state["in_progress"])
        self.assertAlmostEqual(state["progress"], 0.42, places=3)

        done_items = [
            item("The Render Ended Successfully"),
            item("Ok", "Button", clickable=True),
            item("Show in folder...", "Button", clickable=True),
        ]
        self.ui._walk = lambda window, visible: iter(done_items)
        state = self.video._window_state(object())
        self.assertTrue(state["done"])
        self.assertFalse(state["in_progress"])

    def test_tool_schema_and_background_contract(self):
        tools = {tool["name"]: tool for tool in self.video.TOOLS}
        self.assertEqual(set(tools), {"render_video", "render_image_sequence"})
        video = tools["render_video"]
        self.assertTrue(video["background"])
        self.assertNotIn("handler", video)
        for key in ("fn", "poll", "cancel"):
            self.assertTrue(callable(video[key]), key)
        self.assertEqual(video["job_timeout"], self.video.JOB_TIMEOUT)
        self.assertEqual(
            set(video["inputSchema"]["properties"]),
            {"path", "start", "end", "width", "height", "samples", "camera_preset", "overwrite"},
        )
        sequence = tools["render_image_sequence"]
        self.assertEqual(
            set(sequence["inputSchema"]["properties"]),
            {"folder", "start", "end", "width", "height", "samples", "camera_preset"},
        )


if __name__ == "__main__":
    unittest.main()
