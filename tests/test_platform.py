"""Platform-specific behaviour (T55): path resolution and the Win32/Qt split.

This machine only runs Windows, so macOS and Linux are exercised by patching sys.platform and
the environment, exactly as the ticket asks. ui.py is loaded behind stand-ins for PySide6,
shiboken6 and csc, the way the other unit tests load Cascadeur-side modules; the same file also
proves that the Win32 capture code lives in platform_win.py and is not imported elsewhere.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cascadeur_mcp_plus_cli import clients  # noqa: E402

PACKAGE = "cascadeur_mcp_plus"
UI_NAME = f"{PACKAGE}.ui"
WIN_NAME = f"{PACKAGE}.platform_win"


def _ensure_package() -> None:
    """Register cascadeur_mcp_plus without running its __init__ (it imports Cascadeur's Qt)."""
    package = sys.modules.get(PACKAGE)
    if package is None:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(ROOT / PACKAGE)]
        sys.modules[PACKAGE] = package


def _load_by_path(name: str, filename: Path):
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _platform(name: str):
    return mock.patch.object(sys, "platform", name)


def _env(**values):
    """A controlled environment: clear=True stops the real LOCALAPPDATA etc. leaking in."""
    return mock.patch.dict(os.environ, values, clear=True)


def _home_base(name: str = "me") -> str:
    return f"/home/{name}"


class FakeTimer:
    """Stands in for PySide6.QtCore.QTimer: records singleShot and runs it immediately."""

    def __init__(self):
        self.scheduled: list[tuple[int, object]] = []

    def singleShot(self, msec, callback):  # noqa: N802 - Qt spelling
        self.scheduled.append((msec, callback))
        callback()


class FakeScreen:
    def __init__(self, pixmap):
        self.pixmap = pixmap
        self.grabbed: list[int] = []

    def grabWindow(self, handle):  # noqa: N802 - Qt spelling
        self.grabbed.append(handle)
        return self.pixmap


class FakePixmap:
    def __init__(self, null=False, image=None):
        self.null = null
        self.image = image

    def isNull(self):  # noqa: N802 - Qt spelling
        return self.null

    def toImage(self):  # noqa: N802 - Qt spelling
        return self.image


class FakeImage:
    def __init__(self, null=False):
        self.null = null

    def isNull(self):  # noqa: N802 - Qt spelling
        return self.null


class FakeWindow:
    """Enough of a QQuickWindow for the capture/minimize/restore paths."""

    def __init__(self, states=0, screen=None, handle=4242):
        self.states = states
        self._screen = screen
        self.handle = handle
        self.show_normal = 0

    def title(self):
        return "Cascadeur"

    def winId(self):  # noqa: N802 - Qt spelling
        return self.handle

    def windowStates(self):  # noqa: N802 - Qt spelling
        return self.states

    def showNormal(self):  # noqa: N802 - Qt spelling
        self.show_normal += 1

    def screen(self):
        return self._screen


class FakeQt:
    """Install stand-ins for the modules ui.py imports, import ui.py, then restore sys.modules."""

    NAMES = (
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtQuick",
        "PySide6.QtTest",
        "shiboken6",
        "csc",
    )

    def __init__(self):
        self.timer = FakeTimer()
        self.qt = types.SimpleNamespace(
            LeftButton=1,
            RightButton=2,
            MiddleButton=4,
            NoButton=0,
            NoModifier=0,
            ControlModifier=0x04000000,
            ShiftModifier=0x02000000,
            AltModifier=0x08000000,
            MetaModifier=0x10000000,
            WindowMinimized=1,
            SmoothTransformation=0,
            Key_Return=0x01000004,
        )
        qtcore = types.ModuleType("PySide6.QtCore")
        for name in ("QBuffer", "QEvent", "QIODevice", "QPoint", "QPointF", "QRect"):
            setattr(qtcore, name, type(name, (), {}))
        qtcore.Qt = self.qt
        qtcore.QTimer = self.timer
        qtgui = types.ModuleType("PySide6.QtGui")
        for name in ("QGuiApplication", "QImage", "QKeyEvent"):
            setattr(qtgui, name, type(name, (), {}))
        qtgui.QGuiApplication.primaryScreen = staticmethod(lambda: None)
        qtquick = types.ModuleType("PySide6.QtQuick")
        for name in ("QQuickItem", "QQuickWindow"):
            setattr(qtquick, name, type(name, (), {}))
        qttest = types.ModuleType("PySide6.QtTest")
        qttest.QTest = type("QTest", (), {})
        pyside = types.ModuleType("PySide6")
        pyside.QtCore = qtcore
        pyside.QtGui = qtgui
        pyside.QtQuick = qtquick
        pyside.QtTest = qttest
        self.modules = {
            "PySide6": pyside,
            "PySide6.QtCore": qtcore,
            "PySide6.QtGui": qtgui,
            "PySide6.QtQuick": qtquick,
            "PySide6.QtTest": qttest,
            "shiboken6": types.ModuleType("shiboken6"),
            "csc": types.ModuleType("csc"),
        }
        self.saved: dict = {}
        self.ui = None

    def __enter__(self):
        _ensure_package()
        self.saved = {name: sys.modules.get(name) for name in self.NAMES}
        self.saved_ui = sys.modules.pop(UI_NAME, None)
        sys.modules.update(self.modules)
        importlib.import_module(f"{PACKAGE}.errors")
        self.ui = _load_by_path(UI_NAME, ROOT / PACKAGE / "ui.py")
        return self

    def __exit__(self, *exc_info):
        # The stand-ins stay installed while the test body runs (platform_win imports PySide6
        # too), and go away afterwards so no other test sees them.
        for name in self.NAMES:
            module = self.saved[name]
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if self.saved_ui is None:
            sys.modules.pop(UI_NAME, None)
        else:
            sys.modules[UI_NAME] = self.saved_ui
        return False


def load_platform_paths():
    _ensure_package()
    name = f"{PACKAGE}.paths"
    if name in sys.modules:
        return sys.modules[name]
    return _load_by_path(name, ROOT / PACKAGE / "paths.py")


class InAppPathTests(unittest.TestCase):
    """cascadeur_mcp_plus/paths.py resolves per platform without importing Win32 code."""

    @classmethod
    def setUpClass(cls):
        cls.paths = load_platform_paths()

    def test_windows_paths(self):
        local = r"C:\Users\me\AppData\Local"
        with _platform("win32"), _env(LOCALAPPDATA=local, USERPROFILE=_home_base(), HOME=_home_base()):
            self.assertEqual(self.paths.data_dir(), Path(local) / "Nekki Limited" / "Cascadeur")
            self.assertEqual(
                Path(self.paths.log_path()),
                Path(local) / "Nekki Limited" / "Cascadeur" / "logs" / "cascadeur_log.log",
            )
            self.assertEqual(self.paths.install_dirs()[0], Path(local) / "Cascadeur")

    def test_windows_install_folders(self):
        program_files = r"C:\Program Files"
        with _platform("win32"), _env(ProgramFiles=program_files, USERPROFILE=_home_base(), HOME=_home_base()):
            folders = self.paths.install_dirs()
            self.assertEqual(folders[0], Path(program_files) / "Cascadeur")
            self.assertEqual(folders[1], Path(program_files) / "Programs" / "Cascadeur")

    def test_macos_paths(self):
        home = Path(_home_base())
        with _platform("darwin"), _env(HOME=_home_base(), USERPROFILE=_home_base()):
            expected = home / "Library" / "Application Support" / "Nekki Limited" / "Cascadeur"
            self.assertEqual(self.paths.data_dir(), expected)
            self.assertEqual(Path(self.paths.log_path()), expected / "logs" / "cascadeur_log.log")
            self.assertEqual(self.paths.install_dirs()[0], Path("/Applications") / "Cascadeur.app" / "Contents")

    def test_linux_paths(self):
        home = Path(_home_base())
        data_home = home / ".data"
        with _platform("linux"), _env(HOME=_home_base(), USERPROFILE=_home_base(), XDG_DATA_HOME=str(data_home)):
            expected = data_home / "Nekki Limited" / "Cascadeur"
            self.assertEqual(self.paths.data_dir(), expected)
            self.assertEqual(Path(self.paths.log_path()), expected / "logs" / "cascadeur_log.log")
            self.assertEqual(self.paths.install_dirs()[0], Path("/opt") / "cascadeur")

    def test_linux_without_xdg_data_home(self):
        home = Path(_home_base())
        with _platform("linux"), _env(HOME=_home_base(), USERPROFILE=_home_base()):
            self.assertEqual(self.paths.data_dir(), home / ".local" / "share" / "Nekki Limited" / "Cascadeur")

    def test_environment_overrides_win_on_every_platform(self):
        data = Path(_home_base()) / "custom-data"
        log = Path(_home_base()) / "custom.log"
        install = Path(_home_base()) / "Cascadeur-custom"
        for platform_name in ("win32", "darwin", "linux"):
            with (
                _platform(platform_name),
                _env(
                    HOME=_home_base(),
                    USERPROFILE=_home_base(),
                    CASCADEUR_DATA=str(data),
                    CASCADEUR_LOG=str(log),
                    CASCADEUR_HOME=str(install),
                ),
            ):
                self.assertEqual(self.paths.data_dir(), data, platform_name)
                self.assertEqual(Path(self.paths.log_path()), log, platform_name)
                self.assertEqual(self.paths.install_dirs()[0], install, platform_name)

    def test_common_uses_the_platform_log_path(self):
        source = (ROOT / PACKAGE / "common.py").read_text(encoding="utf-8")
        self.assertIn("LOG_PATH = paths.log_path()", source)
        self.assertNotIn("LOCALAPPDATA", source)


class CliPathTests(unittest.TestCase):
    """The CLI resolves its own paths from sys.platform, never from a Windows-only variable."""

    def test_appdata_per_platform(self):
        roaming = r"C:\Users\me\AppData\Roaming"
        home = Path(_home_base())
        with _platform("win32"), _env(APPDATA=roaming, USERPROFILE=_home_base(), HOME=_home_base()):
            self.assertEqual(clients.appdata(), Path(roaming))
        with _platform("win32"), _env(USERPROFILE=_home_base(), HOME=_home_base()):
            self.assertEqual(clients.appdata(), home / "AppData" / "Roaming")
        with _platform("darwin"), _env(HOME=_home_base(), USERPROFILE=_home_base()):
            self.assertEqual(clients.appdata(), home / "Library" / "Application Support")
        with _platform("linux"), _env(HOME=_home_base(), USERPROFILE=_home_base(), XDG_CONFIG_HOME=str(home / ".cfg")):
            self.assertEqual(clients.appdata(), home / ".cfg")
        with _platform("linux"), _env(HOME=_home_base(), USERPROFILE=_home_base()):
            self.assertEqual(clients.appdata(), home / ".config")

    def test_local_app_data_per_platform(self):
        local = r"C:\Users\me\AppData\Local"
        home = Path(_home_base())
        with _platform("win32"), _env(LOCALAPPDATA=local, USERPROFILE=_home_base(), HOME=_home_base()):
            self.assertEqual(clients.local_app_data(), Path(local))
        with _platform("win32"), _env(USERPROFILE=_home_base(), HOME=_home_base()):
            self.assertEqual(clients.local_app_data(), home / "AppData" / "Local")
        with _platform("darwin"), _env(HOME=_home_base(), USERPROFILE=_home_base()):
            self.assertEqual(clients.local_app_data(), home / "Library" / "Application Support")
        with _platform("linux"), _env(HOME=_home_base(), USERPROFILE=_home_base(), XDG_DATA_HOME=str(home / ".data")):
            self.assertEqual(clients.local_app_data(), home / ".data")
        with _platform("linux"), _env(HOME=_home_base(), USERPROFILE=_home_base()):
            self.assertEqual(clients.local_app_data(), home / ".local" / "share")

    def test_vscode_user_directory_per_platform(self):
        home = Path(_home_base())
        with _platform("win32"), _env(APPDATA=r"C:\roaming", USERPROFILE=_home_base(), HOME=_home_base()):
            self.assertEqual(clients.vscode_config(), Path(r"C:\roaming") / "Code" / "User" / "mcp.json")
        with _platform("darwin"), _env(HOME=_home_base(), USERPROFILE=_home_base()):
            self.assertEqual(
                clients.vscode_config(), home / "Library" / "Application Support" / "Code" / "User" / "mcp.json"
            )
        with _platform("linux"), _env(HOME=_home_base(), USERPROFILE=_home_base()):
            self.assertEqual(clients.vscode_config(), home / ".config" / "Code" / "User" / "mcp.json")

    def test_autostart_state_folder_uses_the_platform_data_folder(self):
        from cascadeur_mcp_plus_cli import autostart

        home = Path(_home_base())
        with _platform("linux"), _env(HOME=_home_base(), USERPROFILE=_home_base(), XDG_DATA_HOME=str(home / ".data")):
            self.assertEqual(autostart.state_dir(), home / ".data" / "cascadeur-mcp-plus")
            self.assertEqual(autostart.manifest_path(), home / ".data" / "cascadeur-mcp-plus" / "autostart.json")
        local = r"C:\Users\me\AppData\Local"
        with _platform("win32"), _env(LOCALAPPDATA=local, USERPROFILE=_home_base(), HOME=_home_base()):
            self.assertEqual(autostart.state_dir(), Path(local) / "cascadeur-mcp-plus")

    def test_uvx_cache_paths_are_recognized_with_the_host_separator(self):
        # os.path.join builds the host's separator, so the same assertions hold on every OS.
        def joined(*parts):
            return os.path.join(*parts)

        self.assertTrue(clients._ephemeral_path(joined("home", "me", ".cache", "uv", "archive-v0", "abc", "bin", "c")))
        self.assertTrue(
            clients._ephemeral_path(joined("home", "me", "uv", "cache", "environments-v0", "abc", "python"))
        )
        self.assertFalse(clients._ephemeral_path(joined("home", "me", ".local", "bin", "cascadeur-mcp-plus")))
        self.assertFalse(clients._ephemeral_path(joined("home", "me", "projects", "uv", "of-courses", "bin", "x")))


class UIPlatformTests(unittest.TestCase):
    """ui.py keeps the Win32 code in platform_win.py and has Qt fallbacks for the rest."""

    def test_win32_code_is_moved_out_of_ui(self):
        ui_source = (ROOT / PACKAGE / "ui.py").read_text(encoding="utf-8")
        for win32_call in ("import ctypes", "user32", "PostMessageW", "wintypes"):
            self.assertFalse(win32_call in ui_source, f"ui.py still contains {win32_call!r}")
        win_source = (ROOT / PACKAGE / "platform_win.py").read_text(encoding="utf-8")
        for win32_call in ("PrintWindow", "SC_RESTORE", "IsIconic"):
            self.assertIn(win32_call, win_source)

    def test_off_windows_does_not_import_the_win32_module(self):
        with FakeQt() as fixture, _platform("linux"):
            ui = fixture.ui
            sys.modules.pop(WIN_NAME, None)
            self.assertIsNone(ui._win32())
            self.assertFalse(ui._is_minimized(FakeWindow(states=0)))
            self.assertNotIn(WIN_NAME, sys.modules)

    def test_off_windows_minimized_uses_the_qt_window_state(self):
        with FakeQt() as fixture, _platform("darwin"):
            ui = fixture.ui
            self.assertTrue(ui._is_minimized(FakeWindow(states=fixture.qt.WindowMinimized)))
            self.assertFalse(ui._is_minimized(FakeWindow(states=0)))

    def test_off_windows_capture_uses_the_platform_screen_grab(self):
        with FakeQt() as fixture, _platform("linux"):
            ui = fixture.ui
            image = FakeImage()
            screen = FakeScreen(FakePixmap(null=False, image=image))
            self.assertIs(ui._capture_client(FakeWindow(screen=screen)), image)
            self.assertEqual(screen.grabbed, [4242])

    def test_off_windows_capture_failure_points_at_render_image(self):
        with FakeQt() as fixture, _platform("linux"):
            ui = fixture.ui
            with self.assertRaises(ui.UiError) as caught:
                ui._capture_client(FakeWindow(screen=FakeScreen(FakePixmap(null=True))))
            text = str(caught.exception)
            self.assertIn("render_image", text)
            self.assertIn("linux", text)

    def test_off_windows_restore_schedules_show_normal(self):
        with FakeQt() as fixture, _platform("linux"):
            ui = fixture.ui
            window = FakeWindow(states=fixture.qt.WindowMinimized)
            with mock.patch.object(ui, "resolve_window", return_value=(0, window)):
                result = ui.restore_window()
            self.assertEqual(result, {"window": "Cascadeur", "restored": True})
            self.assertEqual(window.show_normal, 1)
            self.assertEqual(fixture.timer.scheduled[-1][0], 0)

    def test_off_windows_restore_does_nothing_when_not_minimized(self):
        with FakeQt() as fixture, _platform("linux"):
            ui = fixture.ui
            window = FakeWindow(states=0)
            with mock.patch.object(ui, "resolve_window", return_value=(0, window)):
                result = ui.restore_window()
            self.assertEqual(result, {"window": "Cascadeur", "restored": False})
            self.assertEqual(window.show_normal, 0)
            self.assertEqual(fixture.timer.scheduled, [])

    @unittest.skipUnless(sys.platform == "win32", "the Win32 path only exists on Windows")
    def test_windows_uses_platform_win(self):
        with FakeQt() as fixture:
            ui = fixture.ui
            win32 = ui._win32()
            self.assertIsNotNone(win32)
            self.assertIs(win32, sys.modules[WIN_NAME])
            window = FakeWindow(handle=0)
            self.assertFalse(ui._is_minimized(window))
            self.assertFalse(win32.restore(window))
            self.assertTrue(callable(win32.capture_client))


if __name__ == "__main__":
    unittest.main()
