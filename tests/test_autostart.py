"""Tests for the autostart command: handler, enable, disable, manifest and status.

Everything runs against a fake Cascadeur installation in a temporary directory. Nothing here
touches Program Files, asks for UAC or starts Cascadeur: applying a plan runs the same helper
directly (the path an elevated process takes), and the health probes are replaced by fakes.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cascadeur_mcp_plus_cli import autostart, doctor  # noqa: E402


def checkout_version() -> str:
    """VERSION from cascadeur_mcp_plus/version.py, read the way the release reads it (no import)."""
    scope: dict = {}
    exec((ROOT / "cascadeur_mcp_plus" / "version.py").read_text(encoding="utf-8"), scope)
    return scope["VERSION"]


PACKAGE_VERSION = checkout_version()

# The handler is dropped into Cascadeur's installation directory (Program Files on Windows) and
# installed through one UAC prompt, so the whole command is Windows-only (T52, T55). Skipping the
# module keeps non-Windows runs from reporting platform failures.
if os.name != "nt":
    raise unittest.SkipTest("autostart installs through UAC into Cascadeur's install directory (Windows only)")


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class AutostartTestCase(unittest.TestCase):
    """A fake Cascadeur installation, a fake runtime and a temporary LOCALAPPDATA."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="cascadeur-mcp-plus-autostart-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

        self.root = self.base / "Cascadeur"
        self.app = self.root / "resources" / "scripts" / "python" / "events" / "application_started"
        self.app.mkdir(parents=True)
        (self.root / "cascadeur.exe").write_bytes(b"")
        (self.root / "resources" / "scripts" / "python" / "events_rule.py").write_text(
            "def get_event_handlers(package):\n    return []\n", encoding="utf-8"
        )
        (self.app / "__init__.py").write_text("", encoding="utf-8")
        (self.app / "application_started_manager.py").write_text("def run():\n    pass\n", encoding="utf-8")

        # The runtime that enable copies: small on purpose, the real package is tested elsewhere.
        self.runtime_source = self.base / "runtime-src"
        (self.runtime_source / "cascadeur_mcp_plus").mkdir(parents=True)
        (self.runtime_source / "start_mcp_plus.py").write_text("print('loader')\n", encoding="utf-8")
        (self.runtime_source / "cascadeur_mcp_plus" / "__init__.py").write_text("", encoding="utf-8")
        (self.runtime_source / "cascadeur_mcp_plus" / "version.py").write_text(
            f'VERSION = "{PACKAGE_VERSION}"\nDEFAULT_PORT = 48766\n', encoding="utf-8"
        )

        self.local = self.base / "local"
        self.local.mkdir()
        env = {
            "CASCADEUR_HOME": str(self.root),
            "LOCALAPPDATA": str(self.local),
            "ProgramFiles": str(self.base / "no-program-files"),
            "ProgramFiles(x86)": str(self.base / "no-program-files-x86"),
            "CASCADEUR_PLUS_URL": f"http://127.0.0.1:{free_port()}",
            "CASCADEUR_OFFICIAL_URL": f"http://127.0.0.1:{free_port()}",
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

        # The CLI's runtime is the checkout; point enable at the small fake instead.
        runtime_patch = mock.patch.object(autostart, "runtime_dir", return_value=str(self.runtime_source))
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)
        # Applying the plan as if the process were already elevated runs the helper without UAC.
        admin_patch = mock.patch.object(autostart, "_is_admin", return_value=True)
        admin_patch.start()
        self.addCleanup(admin_patch.stop)
        # No test waits on a real socket; the two tests that need "up" replace this themselves.
        probe_patch = mock.patch.object(autostart.doctor, "probe", side_effect=self.fake_probe())
        probe_patch.start()
        self.addCleanup(probe_patch.stop)

    # -- helpers ---------------------------------------------------------

    def call(self, *args, **kwargs) -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = autostart.run(*args, **kwargs)
        return code, buffer.getvalue()

    def enable(self, **kwargs) -> tuple[int, str]:
        kwargs.setdefault("assume_yes", True)
        return self.call("enable", **kwargs)

    def disable(self, **kwargs) -> tuple[int, str]:
        kwargs.setdefault("assume_yes", True)
        return self.call("disable", **kwargs)

    def status(self, json_output: bool = False) -> tuple[int, str]:
        return self.call("status", json_output=json_output)

    @property
    def target(self) -> Path:
        return self.app / autostart.HANDLER_NAME

    @property
    def manifest(self) -> Path:
        return self.local / "cascadeur-mcp-plus" / "autostart.json"

    @property
    def runtime_copy(self) -> Path:
        return self.local / "cascadeur-mcp-plus" / "runtime" / PACKAGE_VERSION

    @property
    def backups(self) -> Path:
        return self.local / "cascadeur-mcp-plus" / "backup" / "application_started"

    def manifest_data(self) -> dict:
        return json.loads(self.manifest.read_text(encoding="utf-8"))

    def record(self) -> dict:
        return self.manifest_data()["installs"]["admin"]

    def write_legacy(self) -> Path:
        path = self.app / autostart.LEGACY_NAME
        path.write_text("# MCP-SCRIPT-SERVER-AUTOSTART\n\ndef run():\n    pass\n", encoding="utf-8")
        return path

    def write_foreign_target(self, text: str = "someone else's handler\n") -> Path:
        self.target.write_text(text, encoding="utf-8")
        return self.target

    def fake_probe(self, plus_up: bool = False, official_up: bool = False):
        def probe(url: str, timeout: float = 3.0):
            if url.startswith(doctor.plus_url()):
                if plus_up:
                    return doctor.Probe("plus", health={"server": "cascadeur-mcp-plus", "version": PACKAGE_VERSION})
                return doctor.Probe("closed", "connection refused")
            if official_up:
                return doctor.Probe(
                    "official", health={"idle_subscription": 0, "message_subscription": 0, "version": "0.3.1"}
                )
            return doctor.Probe("closed", "connection refused")

        return probe


# ---------------------------------------------------------------------------
# The generated handler
# ---------------------------------------------------------------------------


class HandlerTests(AutostartTestCase):
    def load(self, content: str) -> dict:
        namespace = {"__name__": "mcp_plus_autostart_test"}
        exec(compile(content, "mcp_plus_autostart.py", "exec"), namespace)
        return namespace

    def fake_events(self, with_idle: bool = True) -> tuple[types.ModuleType, list]:
        events = types.ModuleType("events")
        queued: list = []
        manager = types.SimpleNamespace()
        if with_idle:
            manager.execute_when_idle = queued.append
        events.scene_idle_manager = manager
        return events, queued

    def test_handler_compiles_and_embeds_the_escaped_path(self) -> None:
        script = self.base / "Program Files" / "it's here" / "start_mcp_plus.py"
        script.parent.mkdir(parents=True)
        content = autostart.render_handler(script, 4321, "9.9.9")
        compile(content, "handler.py", "exec")
        self.assertTrue(content.startswith(autostart.MARKER))
        self.assertIn("Generated by cascadeur-mcp-plus 9.9.9", content)
        namespace = self.load(content)
        self.assertEqual(namespace["_START_SCRIPT"], str(script))
        self.assertEqual(namespace["_PORT"], 4321)

    def test_handler_does_not_import_the_package_at_module_level(self) -> None:
        content = autostart.render_handler(self.base / "start_mcp_plus.py", 48766, PACKAGE_VERSION)
        self.assertNotIn("import cascadeur_mcp_plus", content)
        self.assertNotIn("from cascadeur_mcp_plus", content)
        before = set(sys.modules)
        namespace = self.load(content)
        self.assertEqual(set(sys.modules) - before, set(), "importing the handler must not import the package")
        self.assertEqual(set(namespace["_state"]), {"queued", "done"})

    def test_run_queues_the_start_on_the_first_idle(self) -> None:
        script = self.base / "start.py"
        result = self.base / "result.txt"
        script.write_text(
            f"with open({str(result)!r}, 'w', encoding='utf-8') as handle:\n"
            "    handle.write(__file__ + '|' + str(MCP_PLUS_PORT))\n",
            encoding="utf-8",
        )
        namespace = self.load(autostart.render_handler(script, 48766, PACKAGE_VERSION))
        namespace["_start_official_server"] = lambda: None
        events, queued = self.fake_events()
        with mock.patch.dict(sys.modules, {"events": events}):
            with redirect_stdout(io.StringIO()) as output:
                namespace["run"]()
                self.assertEqual(len(queued), 1)
                namespace["run"]()
                queued[0]()
        self.assertEqual(len(queued), 1, "run() must only queue once")
        self.assertEqual(result.read_text(encoding="utf-8"), f"{script}|48766")
        self.assertIn("will start the official script server", output.getvalue())

    def test_run_falls_back_to_a_qt_timer_without_execute_when_idle(self) -> None:
        script = self.base / "start.py"
        script.write_text("", encoding="utf-8")
        namespace = self.load(autostart.render_handler(script, 48766, PACKAGE_VERSION))
        namespace["_start_official_server"] = lambda: None
        events, queued = self.fake_events(with_idle=False)
        scheduled: list = []
        pyside = types.ModuleType("PySide6")
        qtcore = types.ModuleType("PySide6.QtCore")
        qtcore.QTimer = types.SimpleNamespace(singleShot=lambda delay, fn: scheduled.append((delay, fn)))
        pyside.QtCore = qtcore
        with mock.patch.dict(sys.modules, {"events": events, "PySide6": pyside, "PySide6.QtCore": qtcore}):
            with redirect_stdout(io.StringIO()):
                namespace["run"]()
        self.assertEqual(queued, [])
        self.assertEqual([delay for delay, _ in scheduled], [0])

    def test_run_never_raises_when_the_events_module_is_missing(self) -> None:
        namespace = self.load(autostart.render_handler(self.base / "missing.py", 48766, PACKAGE_VERSION))
        namespace["_start_official_server"] = lambda: None
        with mock.patch.dict(sys.modules, {"events": None, "PySide6": None, "PySide6.QtCore": None}):
            with redirect_stdout(io.StringIO()) as output:
                namespace["run"]()
        self.assertIn("missing", output.getvalue())

    def test_start_swallows_errors_from_the_start_script(self) -> None:
        script = self.base / "boom.py"
        script.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
        namespace = self.load(autostart.render_handler(script, 48766, PACKAGE_VERSION))
        namespace["_start_official_server"] = lambda: None
        with redirect_stdout(io.StringIO()) as output:
            namespace["_start"]()
        self.assertIn("starting Cascadeur MCP Plus", output.getvalue())
        self.assertIn("RuntimeError", output.getvalue())

    def test_start_reports_a_missing_start_script_and_never_raises(self) -> None:
        namespace = self.load(autostart.render_handler(self.base / "gone.py", 48766, PACKAGE_VERSION))
        namespace["_start_official_server"] = lambda: None
        with redirect_stdout(io.StringIO()) as output:
            namespace["_start"]()
        self.assertIn("missing", output.getvalue())
        self.assertIn("autostart enable again", output.getvalue())

    def test_start_runs_the_official_server_before_mcp_plus(self) -> None:
        order: list[str] = []
        script = self.base / "start.py"
        script.write_text(
            f"with open({str(self.base / 'order.txt')!r}, 'w', encoding='utf-8') as handle:\n"
            "    handle.write('plus')\n",
            encoding="utf-8",
        )
        namespace = self.load(autostart.render_handler(script, 48766, PACKAGE_VERSION))
        namespace["_start_official_server"] = lambda: order.append("official")
        with redirect_stdout(io.StringIO()):
            namespace["_start"]()
        self.assertEqual(order, ["official"])
        self.assertEqual((self.base / "order.txt").read_text(encoding="utf-8"), "plus")


# ---------------------------------------------------------------------------
# enable
# ---------------------------------------------------------------------------


class EnableTests(AutostartTestCase):
    def test_dry_run_lists_everything_and_writes_nothing(self) -> None:
        legacy = self.write_legacy()
        with (
            mock.patch.object(autostart, "_is_admin", return_value=False),
            mock.patch.object(autostart, "_execute_operations") as apply,
        ):
            code, output = self.call("enable", dry_run=True)
        self.assertEqual(code, 0)
        apply.assert_not_called()
        self.assertIn(str(self.target), output)
        self.assertIn(str(self.runtime_copy), output)
        self.assertIn(str(self.manifest), output)
        self.assertIn("UAC:          required", output)
        self.assertIn(autostart.MARKER, output)
        self.assertIn(str(legacy), output)
        self.assertIn("Dry run", output)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.runtime_copy.exists())
        self.assertTrue(legacy.exists())

    def test_enable_installs_handler_runtime_and_manifest(self) -> None:
        code, output = self.enable()
        self.assertEqual(code, 0, output)
        content = self.target.read_text(encoding="utf-8")
        self.assertTrue(content.startswith(autostart.MARKER))
        self.assertIn(repr(str(self.runtime_copy / "start_mcp_plus.py")), content)
        self.assertTrue((self.runtime_copy / "start_mcp_plus.py").is_file())
        self.assertTrue((self.runtime_copy / "cascadeur_mcp_plus" / "__init__.py").is_file())
        record = self.record()
        self.assertEqual(record["target"], str(self.target))
        self.assertEqual(record["sha256"], autostart.sha256_file(self.target))
        self.assertEqual(record["version"], PACKAGE_VERSION)
        self.assertEqual(record["runtime"], str(self.runtime_copy))
        self.assertEqual(record["method"], "admin")
        self.assertTrue(record["start_script"].endswith("start_mcp_plus.py"))
        self.assertIn("application_started_manager.py", {row["name"] for row in record["before"]})
        self.assertIn(autostart.HANDLER_NAME, {row["name"] for row in record["after"]})
        self.assertEqual(record["removed_legacy"], [])
        self.assertIn("Installed", output)

    def test_enable_removes_the_temporary_handler_in_one_helper_run(self) -> None:
        legacy = self.write_legacy()
        cache = self.app / "__pycache__"
        cache.mkdir()
        stale = cache / "mcp_script_server_autostart.cpython-311.pyc"
        stale.write_bytes(b"x")
        ours = cache / "mcp_plus_autostart.cpython-311.pyc"
        ours.write_bytes(b"x")
        runs: list = []
        real = autostart._execute_operations

        def counted(*args, **kwargs):
            runs.append(args[0])
            return real(*args, **kwargs)

        with mock.patch.object(autostart, "_execute_operations", side_effect=counted):
            code, output = self.enable()
        self.assertEqual(code, 0, output)
        self.assertEqual(len(runs), 1, "the temporary handler must go in the same elevated run")
        self.assertFalse(legacy.exists())
        self.assertFalse(stale.exists())
        self.assertFalse(ours.exists())
        self.assertEqual([row["name"] for row in self.record()["removed_legacy"]], [autostart.LEGACY_NAME])
        self.assertIn("removed:", output)

    def test_foreign_target_is_refused_without_force(self) -> None:
        foreign = self.write_foreign_target()
        code, output = self.enable()
        self.assertEqual(code, 1)
        self.assertIn("--force", output)
        self.assertEqual(foreign.read_text(encoding="utf-8"), "someone else's handler\n")
        self.assertFalse(self.manifest.exists())
        self.assertFalse(self.runtime_copy.exists())

    def test_force_backs_up_and_replaces_a_foreign_target(self) -> None:
        self.write_foreign_target()
        code, output = self.enable(force=True)
        self.assertEqual(code, 0, output)
        name = self.record()["backup"]
        self.assertEqual(name, os.path.basename(name), "the manifest stores a plain file name")
        backup = self.backups / name
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_text(encoding="utf-8"), "someone else's handler\n")
        self.assertTrue(autostart.carries_marker(self.target, autostart.MARKER))
        self.assertIn("backed up", output)

    def test_an_older_handler_is_replaced_without_a_backup(self) -> None:
        self.target.write_text(autostart.MARKER + "\nold version\n", encoding="utf-8")
        code, output = self.enable()
        self.assertEqual(code, 0, output)
        self.assertIn(f"# Generated by cascadeur-mcp-plus {PACKAGE_VERSION}", self.target.read_text(encoding="utf-8"))
        self.assertIsNone(self.record()["backup"])

    def test_a_broken_generated_handler_never_reaches_the_disk(self) -> None:
        with mock.patch.object(autostart, "render_handler", return_value="def broken(:\n"):
            code, output = self.enable()
        self.assertEqual(code, 1)
        self.assertIn("does not compile", output)
        self.assertFalse(self.target.exists())

    def test_uac_cancel_is_reported_and_writes_nothing(self) -> None:
        with (
            mock.patch.object(autostart, "_is_admin", return_value=False),
            mock.patch.object(
                autostart, "_run_elevated", side_effect=autostart.AutostartError("the UAC prompt was cancelled")
            ) as elevated,
        ):
            code, output = self.enable()
        self.assertEqual(code, 1)
        self.assertIn("cancelled", output)
        self.assertEqual(elevated.call_count, 1, "a cancelled prompt must not be retried")
        self.assertFalse(self.target.exists())
        self.assertFalse(self.manifest.exists())

    def test_the_interactive_prompt_is_required_without_yes(self) -> None:
        with mock.patch.object(autostart.sys, "stdin", io.StringIO("")):
            code, output = self.call("enable", assume_yes=False)
        self.assertEqual(code, 1)
        self.assertIn("No interactive terminal", output)
        self.assertFalse(self.target.exists())

    def test_the_interactive_prompt_can_be_answered_no(self) -> None:
        stdin = mock.MagicMock()
        stdin.isatty.return_value = True
        with mock.patch.object(autostart.sys, "stdin", stdin), mock.patch("builtins.input", return_value="n"):
            code, output = self.call("enable", assume_yes=False)
        self.assertEqual(code, 1)
        self.assertIn("Cancelled", output)
        self.assertFalse(self.target.exists())

    def test_user_method_is_refused_and_settings_json_is_untouched(self) -> None:
        settings = self.local / "Nekki Limited" / "Cascadeur"
        settings.mkdir(parents=True)
        path = settings / "settings.json"
        original = '{"ScriptsDir": "", "Python": {"Path": [], "Events": ["events"]}}'
        path.write_text(original, encoding="utf-8")
        code, output = self.enable(method="user")
        self.assertEqual(code, 1)
        self.assertIn("not supported", output)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.manifest.exists())

    def test_cascadeur_not_found_is_reported(self) -> None:
        (self.root / "cascadeur.exe").unlink()
        code, output = self.enable()
        self.assertEqual(code, 1)
        self.assertIn("Cascadeur was not found", output)

    def test_a_file_that_appeared_after_planning_is_not_overwritten(self) -> None:
        plan = autostart.plan_enable(force=False)
        self.write_foreign_target("# appeared after planning\n")
        with redirect_stdout(io.StringIO()) as output:
            code = autostart.apply_enable(plan)
        self.assertEqual(code, 1)
        self.assertIn("appeared", output.getvalue())
        self.assertEqual(self.target.read_text(encoding="utf-8"), "# appeared after planning\n")
        self.assertFalse(self.manifest.exists())

    def test_a_marker_on_a_later_line_is_not_ours(self) -> None:
        self.target.write_text("# something else\n" + autostart.MARKER + "\n", encoding="utf-8")
        self.assertFalse(autostart.carries_marker(self.target, autostart.MARKER))
        code, output = self.enable()
        self.assertEqual(code, 1)
        self.assertIn("--force", output)
        self.assertTrue(self.target.exists())

    def test_dry_run_describes_both_unauthenticated_ports(self) -> None:
        code, output = self.call("enable", dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("127.0.0.1:8765", output)
        self.assertIn("127.0.0.1:48766", output)
        self.assertIn("authentication", output)

    def test_enable_reports_the_backup_of_the_temporary_handler(self) -> None:
        legacy = self.write_legacy()
        code, output = self.enable()
        self.assertEqual(code, 0, output)
        entry = self.record()["removed_legacy"][0]
        self.assertTrue((self.backups / entry["backup"]).is_file())
        self.assertIn("backed up to", output)
        self.assertFalse(legacy.exists())

    def test_a_marker_file_with_an_unexpected_name_is_refused(self) -> None:
        other = self.app / "someone_autostart.py"
        other.write_text("# MCP-SCRIPT-SERVER-AUTOSTART\n", encoding="utf-8")
        code, output = self.enable()
        self.assertEqual(code, 1)
        self.assertIn("is not mcp_script_server_autostart.py", output)
        self.assertFalse(self.target.exists())
        self.assertTrue(other.exists())

    def test_runtime_option_uses_the_checkout_in_place(self) -> None:
        checkout = self.base / "dev-checkout"
        (checkout / "cascadeur_mcp_plus").mkdir(parents=True)
        (checkout / "start_mcp_plus.py").write_text("print('dev')\n", encoding="utf-8")
        (checkout / "cascadeur_mcp_plus" / "__init__.py").write_text("", encoding="utf-8")
        (checkout / "cascadeur_mcp_plus" / "version.py").write_text(
            f'VERSION = "{PACKAGE_VERSION}"\nDEFAULT_PORT = 48766\n', encoding="utf-8"
        )
        with redirect_stdout(io.StringIO()):
            dry_run = autostart.run("enable", dry_run=True, runtime=str(checkout))
        self.assertEqual(dry_run, 0)
        self.assertFalse(self.target.exists())
        code, output = self.enable(runtime=str(checkout))
        self.assertEqual(code, 0, output)
        self.assertFalse(self.runtime_copy.exists(), "no snapshot is copied for --runtime")
        content = self.target.read_text(encoding="utf-8")
        self.assertIn(repr(str((checkout / "start_mcp_plus.py").resolve())), content)
        record = self.record()
        self.assertEqual(record["runtime"], str(checkout.resolve()))
        self.assertEqual(record["runtime_kind"], "checkout")
        self.assertEqual(record["start_script"], str((checkout / "start_mcp_plus.py").resolve()))
        self.assertIn("used in place", output)

    def test_runtime_option_rejects_an_incomplete_checkout(self) -> None:
        code, output = self.enable(runtime=str(self.base / "missing-checkout"))
        self.assertEqual(code, 1)
        self.assertIn("is not a directory", output)
        self.assertFalse(self.target.exists())
        empty = self.base / "empty-checkout"
        empty.mkdir()
        code, output = self.enable(runtime=str(empty))
        self.assertEqual(code, 1)
        self.assertIn("start_mcp_plus.py", output)
        self.assertFalse(self.target.exists())


# ---------------------------------------------------------------------------
# disable
# ---------------------------------------------------------------------------


class DisableTests(AutostartTestCase):
    def test_disable_removes_the_handler_and_the_record(self) -> None:
        self.enable()
        self.assertTrue(self.target.exists())
        code, output = self.disable()
        self.assertEqual(code, 0, output)
        self.assertFalse(self.target.exists())
        self.assertNotIn("admin", json.loads(self.manifest.read_text(encoding="utf-8"))["installs"])
        self.assertIn("Removed", output)
        self.assertIn("missing: none", output)

    def test_disable_leaves_the_directory_exactly_as_before_enable(self) -> None:
        before = {entry.name: entry.read_bytes() for entry in self.app.iterdir() if entry.is_file()}
        self.enable()
        self.disable()
        after = {entry.name: entry.read_bytes() for entry in self.app.iterdir() if entry.is_file()}
        self.assertEqual(before, after)
        cache = self.app / "__pycache__"
        self.assertEqual(list(cache.glob("mcp_plus_autostart*")) if cache.is_dir() else [], [])

    def test_disable_restores_the_file_replaced_with_force(self) -> None:
        self.write_foreign_target()
        self.enable(force=True)
        self.assertTrue(autostart.carries_marker(self.target, autostart.MARKER))
        code, output = self.disable()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "someone else's handler\n")
        self.assertIn("restored:", output)

    def test_disable_refuses_an_unknown_file_without_force(self) -> None:
        self.write_foreign_target()
        code, output = self.disable()
        self.assertEqual(code, 1)
        self.assertIn("--force", output)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "someone else's handler\n")
        self.assertFalse(self.manifest.exists())

    def test_disable_force_backs_up_and_deletes_an_unknown_file(self) -> None:
        self.write_foreign_target()
        code, output = self.disable(force=True)
        self.assertEqual(code, 0, output)
        self.assertFalse(self.target.exists())
        backups = list(self.backups.iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "someone else's handler\n")

    def test_disable_without_an_install_does_nothing(self) -> None:
        code, output = self.disable()
        self.assertEqual(code, 0)
        self.assertIn("not installed", output)
        self.assertFalse(self.manifest.exists())

    def test_disable_does_not_touch_the_temporary_handler(self) -> None:
        legacy = self.write_legacy()
        code, output = self.disable()
        self.assertEqual(code, 0)
        self.assertTrue(legacy.exists())
        self.assertIn("not installed", output)

    def test_disable_restores_the_temporary_handler(self) -> None:
        legacy = self.write_legacy()
        original = legacy.read_bytes()
        self.enable()
        self.assertFalse(legacy.exists())
        entry = self.record()["removed_legacy"][0]
        self.assertEqual(entry["name"], autostart.LEGACY_NAME)
        self.assertEqual(entry["backup"], os.path.basename(entry["backup"]))
        backup = self.backups / entry["backup"]
        self.assertEqual(backup.read_bytes(), original)
        code, output = self.disable()
        self.assertEqual(code, 0, output)
        self.assertEqual(legacy.read_bytes(), original)
        self.assertIn("restored:", output)

    def test_disable_restores_the_directory_exactly_as_before_enable(self) -> None:
        self.write_legacy()
        before = {entry.name: entry.read_bytes() for entry in self.app.iterdir() if entry.is_file()}
        self.enable()
        self.disable()
        after = {entry.name: entry.read_bytes() for entry in self.app.iterdir() if entry.is_file()}
        self.assertEqual(before, after)

    def test_disable_shows_the_restored_content_and_hash(self) -> None:
        legacy = self.write_legacy()
        self.enable()
        expected_hash = autostart.sha256_file(self.backups / self.record()["removed_legacy"][0]["backup"])
        code, output = self.disable(dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn(autostart.LEGACY_MARKER, output)
        self.assertIn(expected_hash, output)
        self.assertFalse(legacy.exists(), "a dry run must not put the temporary handler back")

    def test_disable_refuses_to_overwrite_a_changed_temporary_handler(self) -> None:
        self.write_legacy()
        self.enable()
        self.write_legacy().write_text("# replaced by hand\n", encoding="utf-8")
        code, output = self.disable()
        self.assertEqual(code, 1)
        self.assertIn("differs from the backup", output)
        self.assertEqual((self.app / autostart.LEGACY_NAME).read_text(encoding="utf-8"), "# replaced by hand\n")

    def test_disable_refuses_a_manifest_backup_path_outside_our_folder(self) -> None:
        self.write_foreign_target()
        self.enable(force=True)
        data = self.manifest_data()
        data["installs"]["admin"]["backup"] = str(self.base / "elsewhere.py")
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        code, output = self.disable()
        self.assertEqual(code, 1)
        self.assertIn("backup", output)
        self.assertTrue(autostart.carries_marker(self.target, autostart.MARKER))

    def test_disable_refuses_a_temporary_handler_backup_outside_our_folder(self) -> None:
        self.write_legacy()
        self.enable()
        data = self.manifest_data()
        data["installs"]["admin"]["removed_legacy"][0]["backup"] = "..\\evil.py"
        self.manifest.write_text(json.dumps(data), encoding="utf-8")
        code, output = self.disable()
        self.assertEqual(code, 1)
        self.assertIn("backup", output)
        self.assertTrue(autostart.carries_marker(self.target, autostart.MARKER))

    def test_disable_dry_run_changes_nothing(self) -> None:
        self.enable()
        content = self.target.read_bytes()
        code, output = self.disable(dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("Dry run", output)
        self.assertEqual(self.target.read_bytes(), content)
        self.assertIn("admin", json.loads(self.manifest.read_text(encoding="utf-8"))["installs"])


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


class StatusTests(AutostartTestCase):
    def test_not_installed(self) -> None:
        code, output = self.status(json_output=True)
        document = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(document["state"], "not installed")
        self.assertFalse(document["installed"])
        self.assertTrue(document["ok"])

    def test_installed_and_both_servers_up(self) -> None:
        self.enable()
        with mock.patch.object(autostart.doctor, "probe", side_effect=self.fake_probe(True, True)):
            code, output = self.status(json_output=True)
        document = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(document["state"], "installed")
        self.assertTrue(document["installed"])
        self.assertTrue(document["running"]["up"])
        self.assertTrue(document["official"]["up"])
        self.assertEqual(document["version"], PACKAGE_VERSION)
        self.assertEqual(document["runtime"]["version"], PACKAGE_VERSION)

    def test_installed_but_removed_by_an_update(self) -> None:
        self.enable()
        self.target.unlink()
        code, output = self.status()
        self.assertEqual(code, 1)
        self.assertIn("removed (Cascadeur update?)", output)
        self.assertIn("autostart enable", output)

    def test_installed_but_runtime_missing(self) -> None:
        self.enable()
        shutil.rmtree(self.runtime_copy)
        code, output = self.status(json_output=True)
        document = json.loads(output)
        self.assertEqual(code, 1)
        self.assertEqual(document["state"], "installed, runtime missing")
        self.assertFalse(document["ok"])

    def test_unknown_file_at_target(self) -> None:
        self.write_foreign_target()
        code, output = self.status(json_output=True)
        document = json.loads(output)
        self.assertEqual(code, 1)
        self.assertEqual(document["state"], "unknown file at target")
        self.assertTrue(self.target.exists())

    def test_modified_at_target(self) -> None:
        self.enable()
        with open(self.target, "a", encoding="utf-8") as handle:
            handle.write("# edited by hand\n")
        code, output = self.status(json_output=True)
        document = json.loads(output)
        self.assertEqual(code, 1)
        self.assertEqual(document["state"], "modified at target")

    def test_manifest_missing_but_handler_present(self) -> None:
        self.enable()
        self.manifest.unlink()
        code, output = self.status(json_output=True)
        document = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(document["state"], "installed (manifest missing)")
        self.assertTrue(document["installed"])

    def test_legacy_is_reported(self) -> None:
        legacy = self.write_legacy()
        code, output = self.status(json_output=True)
        document = json.loads(output)
        self.assertEqual(document["state"], "not installed")
        self.assertEqual([row["name"] for row in document["legacy"]], [autostart.LEGACY_NAME])
        self.assertEqual(document["legacy"][0]["path"], str(legacy))
        self.assertTrue(any("temporary handler" in hint for hint in document["hints"]))

    def test_cascadeur_not_found(self) -> None:
        (self.root / "cascadeur.exe").unlink()
        code, output = self.status()
        self.assertEqual(code, 1)
        self.assertIn("Cascadeur not found", output)

    def test_human_output_shows_both_handlers(self) -> None:
        self.write_legacy()
        self.enable()
        code, output = self.status()
        self.assertEqual(code, 0, output)
        self.assertIn("status:       installed", output)
        self.assertIn(f"handler:      {self.target}", output)
        self.assertIn("legacy:       none", output)


# ---------------------------------------------------------------------------
# Manifest and helper
# ---------------------------------------------------------------------------


class ManifestTests(AutostartTestCase):
    def test_a_second_enable_is_idempotent(self) -> None:
        self.enable()
        first = self.record()
        code, output = self.enable()
        self.assertEqual(code, 0, output)
        second = self.record()
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(autostart.sha256_file(self.target), second["sha256"])
        self.assertEqual((self.runtime_copy / "start_mcp_plus.py").read_text(encoding="utf-8"), "print('loader')\n")

    def test_a_corrupt_manifest_is_reported_and_replaced(self) -> None:
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_text("not json\n", encoding="utf-8")
        code, output = self.status()
        self.assertEqual(code, 0)
        self.assertIn("cannot read", output)
        code, output = self.enable()
        self.assertEqual(code, 0, output)
        self.assertIn("manifest is replaced", output)
        self.assertIn("admin", self.manifest_data()["installs"])


class HelperTests(AutostartTestCase):
    """The elevated helper run directly: the plan, the hash and every path are checked."""

    def run_plan(self, operations, plan_sha=None, target_dir=None, backup_root=None):
        plan_file = self.base / "plan.json"
        result_file = self.base / "result.json"
        raw = json.dumps({"operations": operations}, ensure_ascii=False).encode("utf-8")
        plan_file.write_bytes(raw)
        digest = plan_sha if plan_sha is not None else hashlib.sha256(raw).hexdigest()
        argv = [
            sys.executable,
            "-I",
            "-c",
            autostart._HELPER_SOURCE,
            str(plan_file),
            digest,
            str(result_file),
            str(target_dir or self.app),
            str(backup_root or self.backups),
        ]
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        report = json.loads(result_file.read_text(encoding="utf-8")) if result_file.is_file() else None
        return completed.returncode, report

    def test_backup_write_remove_and_pycache(self) -> None:
        foreign = self.target
        foreign.write_text("# someone else's handler\n", encoding="utf-8")
        backup = self.backups / "20261024-000000-mcp_plus_autostart.py"
        victim = self.app / autostart.LEGACY_NAME
        victim.write_text("# MCP-SCRIPT-SERVER-AUTOSTART\n", encoding="utf-8")
        content = autostart.MARKER + "\n# new\n"
        cache = self.app / "__pycache__"
        cache.mkdir()
        stale = cache / "mcp_plus_autostart.cpython-311.pyc"
        stale.write_bytes(b"x")
        operations = [
            {
                "action": "backup",
                "path": str(foreign),
                "destination": str(backup),
                "expect_sha256": autostart.sha256_file(foreign),
            },
            {"action": "remove_pycache", "directory": str(self.app), "stem": "mcp_plus_autostart"},
            {
                "action": "write",
                "path": str(self.target),
                "content": content,
                "sha256": autostart.sha256_text(content),
                "expect_sha256": autostart.sha256_file(foreign),
                "expect_marker": None,
            },
            {
                "action": "remove",
                "path": str(victim),
                "marker": autostart.LEGACY_MARKER,
                "expect_sha256": autostart.sha256_file(victim),
            },
        ]
        code, report = self.run_plan(operations)
        self.assertEqual(code, 0, report)
        self.assertTrue(report["ok"])
        self.assertEqual(report["done"], ["backup", "remove_pycache", "write", "remove"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), content)
        self.assertEqual(backup.read_text(encoding="utf-8"), "# someone else's handler\n")
        self.assertFalse(victim.exists())
        self.assertFalse(stale.exists())

    def test_a_plan_with_a_wrong_hash_is_rejected(self) -> None:
        content = autostart.MARKER + "\n"
        operations = [
            {
                "action": "write",
                "path": str(self.target),
                "content": content,
                "sha256": autostart.sha256_text(content),
                "expect_sha256": None,
                "expect_marker": None,
            }
        ]
        code, report = self.run_plan(operations, plan_sha="0" * 64)
        self.assertEqual(code, 3)
        self.assertIn("SHA-256", report["error"])
        self.assertFalse(self.target.exists())

    def test_a_path_outside_the_events_folder_is_rejected(self) -> None:
        evil = self.base / "evil.py"
        content = "# evil\n"
        operations = [
            {
                "action": "write",
                "path": str(evil),
                "content": content,
                "sha256": autostart.sha256_text(content),
                "expect_sha256": None,
                "expect_marker": None,
            }
        ]
        code, report = self.run_plan(operations)
        self.assertEqual(code, 3)
        self.assertIn("outside the events folder", report["error"])
        self.assertFalse(evil.exists())

    def test_a_backup_outside_our_backup_folder_is_rejected(self) -> None:
        source = self.target
        source.write_text("# ours\n", encoding="utf-8")
        destination = self.base / "backup" / "copy.py"
        operations = [
            {
                "action": "backup",
                "path": str(source),
                "destination": str(destination),
                "expect_sha256": autostart.sha256_file(source),
            }
        ]
        code, report = self.run_plan(operations)
        self.assertEqual(code, 3)
        self.assertIn("outside the backup folder", report["error"])
        self.assertFalse(destination.exists())

    def test_a_file_that_appeared_after_planning_is_not_overwritten(self) -> None:
        content = autostart.MARKER + "\n"
        operations = [
            {
                "action": "write",
                "path": str(self.target),
                "content": content,
                "sha256": autostart.sha256_text(content),
                "expect_sha256": None,
                "expect_marker": None,
            }
        ]
        plan_file = self.base / "plan.json"
        result_file = self.base / "result.json"
        raw = json.dumps({"operations": operations}, ensure_ascii=False).encode("utf-8")
        plan_file.write_bytes(raw)
        self.target.write_text("# appeared meanwhile\n", encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                autostart._HELPER_SOURCE,
                str(plan_file),
                hashlib.sha256(raw).hexdigest(),
                str(result_file),
                str(self.app),
                str(self.backups),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 3)
        self.assertIn("appeared", json.loads(result_file.read_text(encoding="utf-8"))["error"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), "# appeared meanwhile\n")

    def test_a_changed_content_hash_stops_the_write(self) -> None:
        content = "# new\n"
        operations = [
            {
                "action": "write",
                "path": str(self.target),
                "content": content,
                "sha256": "0" * 64,
                "expect_sha256": None,
                "expect_marker": None,
            }
        ]
        code, report = self.run_plan(operations)
        self.assertEqual(code, 3)
        self.assertIn("content changed", report["error"])
        self.assertFalse(self.target.exists())

    def test_a_marker_mismatch_stops_the_plan(self) -> None:
        victim = self.app / autostart.LEGACY_NAME
        victim.write_text("# something\n", encoding="utf-8")
        code, report = self.run_plan([{"action": "remove", "path": str(victim), "marker": autostart.LEGACY_MARKER}])
        self.assertEqual(code, 3)
        self.assertIn("marker", report["error"])
        self.assertTrue(victim.exists())

    def test_a_marker_on_a_later_line_does_not_count(self) -> None:
        victim = self.app / autostart.LEGACY_NAME
        victim.write_text("# something\n" + autostart.LEGACY_MARKER + "\n", encoding="utf-8")
        code, report = self.run_plan([{"action": "remove", "path": str(victim), "marker": autostart.LEGACY_MARKER}])
        self.assertEqual(code, 3)
        self.assertIn("marker", report["error"])
        self.assertTrue(victim.exists())

    def test_restore_puts_the_backup_back(self) -> None:
        self.backups.mkdir(parents=True)
        backup = self.backups / "backup.py"
        backup.write_text("# theirs\n", encoding="utf-8")
        operations = [
            {
                "action": "restore",
                "backup": str(backup),
                "path": str(self.target),
                "expect_sha256": autostart.sha256_file(backup),
                "only_if_missing": False,
            }
        ]
        code, report = self.run_plan(operations)
        self.assertEqual(code, 0, report)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "# theirs\n")

    def test_restore_refuses_to_overwrite_when_only_if_missing(self) -> None:
        self.backups.mkdir(parents=True)
        backup = self.backups / "backup.py"
        backup.write_text("# theirs\n", encoding="utf-8")
        self.target.write_text("# ours\n", encoding="utf-8")
        operations = [
            {
                "action": "restore",
                "backup": str(backup),
                "path": str(self.target),
                "expect_sha256": autostart.sha256_file(backup),
                "only_if_missing": True,
            }
        ]
        code, report = self.run_plan(operations)
        self.assertEqual(code, 3)
        self.assertIn("appeared", report["error"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), "# ours\n")

    def test_a_result_path_that_is_a_directory_is_not_written(self) -> None:
        result_path = self.base / "result.json"
        result_path.mkdir()
        plan_file = self.base / "plan.json"
        raw = json.dumps({"operations": []}).encode("utf-8")
        plan_file.write_bytes(raw)
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                autostart._HELPER_SOURCE,
                str(plan_file),
                hashlib.sha256(raw).hexdigest(),
                str(result_path),
                str(self.app),
                str(self.backups),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertTrue(result_path.is_dir())

    def test_pythonpath_cannot_inject_modules_into_the_helper(self) -> None:
        poison = self.base / "poison"
        poison.mkdir()
        (poison / "json.py").write_text("raise RuntimeError('imported from PYTHONPATH')\n", encoding="utf-8")
        operations = [{"action": "remove_pycache", "directory": str(self.app), "stem": "mcp_plus_autostart"}]
        with mock.patch.dict(os.environ, {"PYTHONPATH": str(poison)}):
            report = autostart._execute_operations(operations, self.app, self.backups)
        self.assertTrue(report["ok"])

    def test_the_helper_command_line_is_isolated_and_inline(self) -> None:
        calls = []
        real = subprocess.run

        def record(argv, **kwargs):
            calls.append(argv)
            return real(argv, **kwargs)

        operations = [{"action": "remove_pycache", "directory": str(self.app), "stem": "mcp_plus_autostart"}]
        with mock.patch.object(autostart.subprocess, "run", side_effect=record):
            report = autostart._execute_operations(operations, self.app, self.backups)
        self.assertTrue(report["ok"])
        self.assertEqual(calls[0][1:4], ["-I", "-c", autostart._HELPER_SOURCE])
        self.assertRegex(calls[0][5], r"^[0-9a-f]{64}$")
        self.assertFalse(any(str(part).endswith("apply_autostart.py") for part in calls[0]))

    def test_the_elevated_command_line_is_the_same(self) -> None:
        seen = []

        def fake_elevated(argv, timeout=autostart.UAC_TIMEOUT):
            seen.append(argv)
            completed = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            return completed.returncode

        operations = [{"action": "remove_pycache", "directory": str(self.app), "stem": "mcp_plus_autostart"}]
        with (
            mock.patch.object(autostart, "_is_admin", return_value=False),
            mock.patch.object(autostart, "_run_elevated", side_effect=fake_elevated),
        ):
            report = autostart._execute_operations(operations, self.app, self.backups)
        self.assertTrue(report["ok"])
        self.assertEqual(seen[0][1:4], ["-I", "-c", autostart._HELPER_SOURCE])


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


class CliTests(AutostartTestCase):
    def test_main_dispatches_status_and_enable(self) -> None:
        from cascadeur_mcp_plus_cli.main import main as cli_main

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli_main(["autostart", "status", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buffer.getvalue())["state"], "not installed")

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli_main(["autostart", "enable", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn(str(self.target), buffer.getvalue())
        self.assertFalse(self.target.exists())

    def test_autostart_help_lists_the_three_actions(self) -> None:
        from cascadeur_mcp_plus_cli.main import build_parser

        buffer = io.StringIO()
        with self.assertRaises(SystemExit) as caught, redirect_stdout(buffer):
            build_parser().parse_args(["autostart", "--help"])
        self.assertEqual(caught.exception.code, 0)
        for action in ("status", "enable", "disable"):
            self.assertIn(action, buffer.getvalue())

    def test_enable_help_documents_method_and_force(self) -> None:
        from cascadeur_mcp_plus_cli.main import build_parser

        buffer = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(buffer):
            build_parser().parse_args(["autostart", "enable", "--help"])
        self.assertIn("--method", buffer.getvalue())
        self.assertIn("--force", buffer.getvalue())
        self.assertIn("--dry-run", buffer.getvalue())
        self.assertIn("--runtime", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
