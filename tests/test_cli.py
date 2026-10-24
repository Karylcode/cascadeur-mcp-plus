"""Tests for the cascadeur-mcp-plus CLI: setup, uninstall, doctor, serve and the wheel layout.

Everything here runs without Cascadeur and without touching a real client configuration:
USERPROFILE/HOME/APPDATA/CODEX_HOME/CLAUDE_CONFIG_DIR and PATH are pointed at a temporary
directory, and Claude Code's own CLI is replaced by a recorder. The only processes started are
the CLI itself and the proxy (against a fake in-process MCP Plus server).

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cascadeur_mcp_plus_cli import clients, doctor  # noqa: E402
from cascadeur_mcp_plus_cli import version as cli_version  # noqa: E402
from cascadeur_mcp_plus_cli.main import main as cli_main  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeServer:
    """A tiny HTTP server standing in for MCP Plus, the script server or a foreign program."""

    def __init__(self, role: str):
        self.role = role
        self.posts: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status: int, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if outer.role == "plus":
                    self._send(
                        200,
                        {"ok": True, "server": "cascadeur-mcp-plus", "version": cli_version(), "queued": 0},
                    )
                elif outer.role == "official":
                    self._send(
                        200,
                        {
                            "ok": True,
                            "version": "0.3.1",
                            "idle_subscription": 0,
                            "message_subscription": 0,
                            "queued": 0,
                        },
                    )
                else:
                    self._send(401, {"error": "unauthorized"})

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}"
                payload = json.loads(body)
                outer.posts.append(payload)
                if outer.role == "plus" and self.path == "/mcp":
                    method = payload.get("method")
                    if method == "tools/list":
                        result = {"tools": [{"name": "live_tool"}]}
                    elif method == "resources/list":
                        result = {"resources": []}
                    else:
                        result = {"content": [{"type": "text", "text": "forwarded"}]}
                    self._send(200, {"jsonrpc": "2.0", "id": payload.get("id"), "result": result})
                else:
                    self._send(404, {"error": "no route"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class CliTestCase(unittest.TestCase):
    """A temporary home directory and PATH, so no real client config is ever read or written."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="cascadeur-mcp-plus-t50-")
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "home"
        self.bin = Path(self._tmp.name) / "bin"
        self.home.mkdir(parents=True)
        self.bin.mkdir()
        env = {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "APPDATA": str(self.home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(self.home / "AppData" / "Local"),
            "CODEX_HOME": str(self.home / ".codex"),
            "CLAUDE_CONFIG_DIR": "",
            "PATH": str(self.bin),
            "CASCADEUR_MCP_PLUS_COMMAND": "",
            "CASCADEUR_PLUS_URL": f"http://127.0.0.1:{free_port()}",
            "CASCADEUR_OFFICIAL_URL": f"http://127.0.0.1:{free_port()}",
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call_main(self, *args: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli_main(list(args))
        return code, buffer.getvalue()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "cascadeur_mcp_plus_cli", *args],
            cwd=str(ROOT),
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=180,
        )

    def launch(self) -> clients.Launch:
        return clients.launch_command()

    def cursor_config(self) -> Path:
        return self.home / ".cursor" / "mcp.json"

    def codex_config(self) -> Path:
        return self.home / ".codex" / "config.toml"

    def vscode_config(self) -> Path:
        return self.home / "AppData" / "Roaming" / "Code" / "User" / "mcp.json"

    def claude_config(self) -> Path:
        return clients.claude_config()

    def add_fake_claude(self) -> Path:
        exe = self.bin / "claude.exe"
        exe.write_bytes(b"")
        return exe

    def write_claude(self, entry: dict | None = None, servers: dict | None = None) -> Path:
        path = self.claude_config()
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {"mcpServers": dict(servers or {})}
        if entry is not None:
            data["mcpServers"][clients.SERVER_NAME] = entry
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return path

    def record_run(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        calls: list[list[str]] = []

        def fake(argv):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

        return calls, fake


class VersionAndPackagingTests(unittest.TestCase):
    def test_cli_version_matches_the_package(self) -> None:
        scope: dict = {}
        exec((ROOT / "cascadeur_mcp_plus" / "version.py").read_text(encoding="utf-8"), scope)
        self.assertEqual(cli_version(), scope["VERSION"])

    def test_pyproject_installs_a_dedicated_runtime_package(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["project"]["scripts"]["cascadeur-mcp-plus"], "cascadeur_mcp_plus_cli.main:main")
        self.assertEqual(data["build-system"]["build-backend"], "setuptools.build_meta")
        self.assertEqual(
            data["tool"]["setuptools"]["dynamic"]["version"]["attr"],
            "cascadeur_mcp_plus_runtime.cascadeur_mcp_plus.version.VERSION",
        )
        self.assertNotIn("py-modules", data["tool"]["setuptools"], "no top-level proxy/start modules in the wheel")
        packages = data["tool"]["setuptools"]["packages"]
        self.assertIn("cascadeur_mcp_plus_cli", packages)
        self.assertIn("cascadeur_mcp_plus_runtime", packages)
        self.assertIn("cascadeur_mcp_plus_runtime.cascadeur_mcp_plus", packages)
        self.assertIn("cascadeur_mcp_plus_runtime.cascadeur_mcp_plus.tools", packages)
        self.assertNotIn("cascadeur_mcp_plus", packages, "the package must only be installed inside the runtime")
        mapping = data["tool"]["setuptools"]["package-dir"]
        self.assertEqual(mapping["cascadeur_mcp_plus_runtime.cascadeur_mcp_plus"], "cascadeur_mcp_plus")
        self.assertEqual(mapping["cascadeur_mcp_plus_runtime.cascadeur_mcp_plus.tools"], "cascadeur_mcp_plus/tools")

    def test_proxy_implementation_is_not_a_top_level_module(self) -> None:
        self.assertTrue((ROOT / "cascadeur_mcp_plus_cli" / "proxy.py").is_file())
        launcher = (ROOT / "proxy.py").read_text(encoding="utf-8")
        self.assertIn("cascadeur_mcp_plus_cli", launcher)
        self.assertNotIn("def handle(", launcher, "the root file is only a launcher")

    def test_runtime_files_exist_for_both_layouts(self) -> None:
        self.assertTrue((ROOT / "start_mcp_plus.py").is_file())
        self.assertTrue((ROOT / "cascadeur_mcp_plus" / "start.py").is_file())
        installed = ROOT / "cascadeur_mcp_plus_runtime"
        self.assertTrue((installed / "__init__.py").is_file())
        self.assertTrue((installed / "start_mcp_plus.py").is_file())

    def test_proxy_finds_the_checkout_runtime_directory(self) -> None:
        from cascadeur_mcp_plus_cli import proxy, serve

        self.assertEqual(os.path.normcase(proxy.ROOT), os.path.normcase(str(ROOT)))
        self.assertTrue(os.path.isfile(proxy.START_SCRIPT))
        self.assertEqual(
            os.path.normcase(serve.proxy_path()),
            os.path.normcase(str(ROOT / "cascadeur_mcp_plus_cli" / "proxy.py")),
        )


def load_start_module():
    path = ROOT / "cascadeur_mcp_plus" / "start.py"
    spec = importlib.util.spec_from_file_location("_t50_start_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StartScriptTests(unittest.TestCase):
    def test_package_root_accepts_checkout_and_installed_runtime(self) -> None:
        module = load_start_module()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            checkout = base / "checkout"
            (checkout / "cascadeur_mcp_plus").mkdir(parents=True)
            (checkout / "cascadeur_mcp_plus" / "__init__.py").write_text("", encoding="utf-8")
            self.assertEqual(module.package_root(str(checkout)), os.path.abspath(str(checkout)))

            installed = base / "site-packages" / "cascadeur_mcp_plus_runtime"
            (installed / "cascadeur_mcp_plus").mkdir(parents=True)
            (installed / "cascadeur_mcp_plus" / "__init__.py").write_text("", encoding="utf-8")
            self.assertEqual(module.package_root(str(installed)), os.path.abspath(str(installed)))
            self.assertNotEqual(
                os.path.dirname(module.package_root(str(installed))),
                os.path.abspath(str(installed)),
                "the installed runtime is the directory itself, never its parent (site-packages)",
            )

            empty = base / "empty"
            empty.mkdir()
            with self.assertRaises(RuntimeError):
                module.package_root(str(empty))

    def test_run_adds_only_the_runtime_directory_to_sys_path(self) -> None:
        module = load_start_module()
        saved_path = list(sys.path)
        # Other unit tests install a stub cascadeur_mcp_plus in sys.modules; run() purges the
        # package by design, so put the exact previous state back afterwards.
        saved_modules = {
            name: value
            for name, value in sys.modules.items()
            if name == "cascadeur_mcp_plus" or name.startswith("cascadeur_mcp_plus.")
        }
        try:
            with tempfile.TemporaryDirectory() as tmp:
                runtime = Path(tmp) / "site-packages" / "cascadeur_mcp_plus_runtime"
                package = runtime / "cascadeur_mcp_plus"
                package.mkdir(parents=True)
                (package / "__init__.py").write_text("def start(port=None):\n    return 'started'\n", encoding="utf-8")
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    module.run(str(runtime))
                self.assertEqual(sys.path[0], os.path.abspath(str(runtime)))
                self.assertNotIn(os.path.abspath(str(runtime.parent)), sys.path)
                self.assertIn("started", buffer.getvalue())
        finally:
            sys.path[:] = saved_path
            for name in [n for n in sys.modules if n == "cascadeur_mcp_plus" or n.startswith("cascadeur_mcp_plus.")]:
                del sys.modules[name]
            sys.modules.update(saved_modules)

    def test_both_wrappers_execute_the_package_from_their_own_directory(self) -> None:
        # This is the path live.py restart and the proxy use: exec(open(script).read(),
        # {"__file__": script, "MCP_PLUS_PORT": port}). A fake package keeps Cascadeur out of it.
        for source in (ROOT / "start_mcp_plus.py", ROOT / "cascadeur_mcp_plus_runtime" / "start_mcp_plus.py"):
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                package = base / "cascadeur_mcp_plus"
                package.mkdir()
                shutil.copy2(ROOT / "cascadeur_mcp_plus" / "start.py", package / "start.py")
                (package / "__init__.py").write_text(
                    "def start(port=None):\n    return 'started from ' + __name__ + ' port=' + repr(port)\n",
                    encoding="utf-8",
                )
                wrapper = base / "start_mcp_plus.py"
                shutil.copy2(source, wrapper)
                saved_path = list(sys.path)
                saved_modules = {
                    name: value
                    for name, value in sys.modules.items()
                    if name == "cascadeur_mcp_plus" or name.startswith("cascadeur_mcp_plus.")
                }
                try:
                    buffer = io.StringIO()
                    with redirect_stdout(buffer):
                        exec(
                            compile(wrapper.read_text(encoding="utf-8"), str(wrapper), "exec"),
                            {"__file__": str(wrapper), "MCP_PLUS_PORT": 12345},
                        )
                    output = buffer.getvalue()
                    self.assertIn("started from cascadeur_mcp_plus", output)
                    self.assertIn("port=12345", output)
                    self.assertEqual(sys.path[0], os.path.abspath(str(base)))
                    self.assertNotIn(os.path.abspath(str(base.parent)), sys.path)
                finally:
                    sys.path[:] = saved_path
                    for name in [
                        n for n in sys.modules if n == "cascadeur_mcp_plus" or n.startswith("cascadeur_mcp_plus.")
                    ]:
                        del sys.modules[name]
                    sys.modules.update(saved_modules)

    def test_both_wrappers_load_the_shared_start_logic(self) -> None:
        for script in (ROOT / "start_mcp_plus.py", ROOT / "cascadeur_mcp_plus_runtime" / "start_mcp_plus.py"):
            text = script.read_text(encoding="utf-8")
            self.assertIn('"cascadeur_mcp_plus", "start.py"', text)
            self.assertIn('_start.run(_here, globals().get("MCP_PLUS_PORT"))', text)
            self.assertIn("_here = os.path.dirname(os.path.abspath(__file__))", text)


class LaunchCommandTests(CliTestCase):
    def test_explicit_command_override(self) -> None:
        self.assertEqual(
            clients.launch_command("C:/tools/cascadeur-mcp-plus.exe").argv(),
            ["C:/tools/cascadeur-mcp-plus.exe", "serve"],
        )

    def test_installed_console_script_wins(self) -> None:
        script = self.bin / "cascadeur-mcp-plus.exe"
        script.write_bytes(b"")
        argv = clients.launch_command().argv()
        self.assertEqual(os.path.normcase(argv[0]), os.path.normcase(str(script)))
        self.assertEqual(argv[1:], ["serve"])

    def test_source_checkout_uses_this_python_and_proxy(self) -> None:
        launch = clients.launch_command()
        self.assertEqual(launch.argv(), [sys.executable, "-I", str(ROOT / "proxy.py")])

    def test_ephemeral_uvx_environment_registers_uvx_not_the_cache_path(self) -> None:
        scripts = self.home / "uv" / "cache" / "archive-v0" / "abc" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "cascadeur-mcp-plus.exe").write_bytes(b"")
        uvx = self.bin / "uvx.exe"
        uvx.write_bytes(b"")
        fake_python = self.home / "uv" / "cache" / "environments-v0" / "abc" / "Scripts" / "python.exe"
        with (
            mock.patch.object(sys, "executable", str(fake_python)),
            mock.patch.dict(os.environ, {"PATH": str(scripts) + os.pathsep + str(self.bin)}),
        ):
            launch = clients.launch_command()
        self.assertEqual(os.path.normcase(launch.command), os.path.normcase(str(uvx)))
        self.assertEqual(launch.args, ("cascadeur-mcp-plus", "serve"))

    def test_installed_wheel_without_a_console_script_uses_the_module(self) -> None:
        site_packages = self.home / "venv" / "Lib" / "site-packages"
        python = self.home / "venv" / "Scripts" / "python.exe"
        with (
            mock.patch.object(clients, "PACKAGE_ROOT", str(site_packages)),
            mock.patch.object(sys, "executable", str(python)),
        ):
            launch = clients.launch_command()
        self.assertEqual(launch.argv(), [str(python), "-I", "-m", "cascadeur_mcp_plus_cli", "serve"])


class HomeConfigTests(CliTestCase):
    @unittest.skipUnless(os.name == "nt", "USERPROFILE precedence is Windows-specific")
    def test_userprofile_wins_over_home(self) -> None:
        profile = self.home / "profile"
        msys = self.home / "msys64" / "home" / "User"
        with mock.patch.dict(os.environ, {"USERPROFILE": str(profile), "HOME": str(msys), "CODEX_HOME": ""}):
            self.assertEqual(clients.home(), profile)
            self.assertEqual(clients.cursor_config(), profile / ".cursor" / "mcp.json")
            self.assertEqual(clients.codex_config(), profile / ".codex" / "config.toml")
            self.assertEqual(
                clients.vscode_config(),
                Path(os.environ["APPDATA"]) / "Code" / "User" / "mcp.json",
            )

    def test_claude_config_dir_is_honoured(self) -> None:
        base = self.home / "claude-home"
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(base)}):
            self.assertEqual(clients.claude_config(), base / ".claude.json")

    def test_claude_plan_reads_the_config_file(self) -> None:
        launch = self.launch()
        self.write_claude({"command": launch.command, "args": list(launch.args)})
        plan = clients.plan_setup("claude-code", launch)
        self.assertEqual(plan.status, "unchanged")
        self.assertEqual(plan.config_state, "exists")


class SetupDryRunTests(CliTestCase):
    def test_lists_every_client_and_writes_nothing(self) -> None:
        result = self.run_cli("setup", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        for label in ("Claude Code", "Codex CLI", "Cursor", "VS Code"):
            self.assertIn(label, out)
        self.assertIn("claude mcp add --scope user cascadeur-plus --", out)
        self.assertIn("[mcp_servers.cascadeur-plus]", out)
        self.assertIn('"mcpServers"', out)
        self.assertIn('"servers"', out)
        self.assertIn("Dry run: no files were written", out)
        self.assertEqual(list(self.home.rglob("*")), [], "dry-run must not create anything")

    def test_explicit_claude_code_dry_run_works_without_the_cli(self) -> None:
        code, out = self.call_main("setup", "--client", "claude-code", "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("claude mcp add --scope user cascadeur-plus --", out)
        self.assertIn("Dry run", out)

    def test_does_not_run_the_claude_cli_or_any_command(self) -> None:
        self.add_fake_claude()
        self.write_claude()
        with mock.patch.object(clients, "run_command", side_effect=AssertionError("must not run")):
            code, out = self.call_main("setup", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("detected: yes", out)

    def test_keeps_existing_servers_in_the_proposed_json(self) -> None:
        self.cursor_config().parent.mkdir(parents=True)
        original = json.dumps({"mcpServers": {"other": {"command": "node"}}}, indent=2) + "\n"
        self.cursor_config().write_text(original, encoding="utf-8")
        code, out = self.call_main("setup", "--client", "cursor", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn('"other"', out)
        self.assertIn('+    "cascadeur-plus": {', out)
        self.assertEqual(self.cursor_config().read_text(encoding="utf-8"), original)


class SetupApplyTests(CliTestCase):
    def test_cursor_adds_entry_and_backs_up_the_original(self) -> None:
        self.cursor_config().parent.mkdir(parents=True)
        original = '{"mcpServers": {"other": {"command": "node"}}}\n'
        self.cursor_config().write_text(original, encoding="utf-8")
        code, out = self.call_main("setup", "--client", "cursor", "--yes")
        self.assertEqual(code, 0, out)
        data = json.loads(self.cursor_config().read_text(encoding="utf-8"))
        self.assertEqual(data["mcpServers"]["other"], {"command": "node"})
        entry = data["mcpServers"]["cascadeur-plus"]
        self.assertEqual(entry["command"], self.launch().command)
        self.assertEqual(entry["args"], list(self.launch().args))
        backups = list(self.cursor_config().parent.glob("mcp.json.cascadeur-mcp-plus.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), original)
        self.assertIn("wrote", out)

    def test_vscode_uses_the_servers_key_with_a_stdio_type(self) -> None:
        code, out = self.call_main("setup", "--client", "vscode", "--yes")
        self.assertEqual(code, 0, out)
        data = json.loads(self.vscode_config().read_text(encoding="utf-8"))
        entry = data["servers"]["cascadeur-plus"]
        self.assertEqual(entry["type"], "stdio")
        self.assertEqual(entry["command"], self.launch().command)
        self.assertEqual(entry["args"], list(self.launch().args))

    def test_codex_inserts_a_table_and_is_idempotent(self) -> None:
        self.codex_config().parent.mkdir(parents=True)
        self.codex_config().write_text('[windows]\nsandbox = "elevated"\n\n[mcp_servers.other]\ncommand = "node"\n')
        code, out = self.call_main("setup", "--client", "codex", "--yes")
        self.assertEqual(code, 0, out)
        text = self.codex_config().read_text(encoding="utf-8")
        data = tomllib.loads(text)
        self.assertEqual(data["windows"]["sandbox"], "elevated")
        self.assertEqual(data["mcp_servers"]["other"]["command"], "node")
        self.assertEqual(data["mcp_servers"]["cascadeur-plus"]["command"], self.launch().command)
        self.assertEqual(data["mcp_servers"]["cascadeur-plus"]["args"], list(self.launch().args))
        self.assertIn("[mcp_servers.cascadeur-plus]", text)

        code, out = self.call_main("setup", "--client", "codex", "--yes")
        self.assertEqual(code, 0, out)
        self.assertIn("Nothing to do", out)
        self.assertEqual(self.codex_config().read_text(encoding="utf-8"), text)
        self.assertEqual(len(list(self.codex_config().parent.glob("*.bak"))), 1)

    def test_replaces_an_outdated_entry(self) -> None:
        self.cursor_config().parent.mkdir(parents=True)
        self.cursor_config().write_text(
            '{"mcpServers": {"cascadeur-plus": {"command": "old", "args": []}}}\n',
            encoding="utf-8",
        )
        code, out = self.call_main("setup", "--client", "cursor", "--yes")
        self.assertEqual(code, 0, out)
        entry = json.loads(self.cursor_config().read_text(encoding="utf-8"))["mcpServers"]["cascadeur-plus"]
        self.assertEqual(entry["command"], self.launch().command)
        self.assertEqual(entry["args"], list(self.launch().args))

    def test_keeps_the_env_of_an_existing_json_entry(self) -> None:
        self.cursor_config().parent.mkdir(parents=True)
        self.cursor_config().write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "cascadeur-plus": {
                            "command": "old",
                            "args": [],
                            "env": {"FOO": "1"},
                        }
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        code, out = self.call_main("setup", "--client", "cursor", "--yes")
        self.assertEqual(code, 0, out)
        entry = json.loads(self.cursor_config().read_text(encoding="utf-8"))["mcpServers"]["cascadeur-plus"]
        self.assertEqual(entry["env"], {"FOO": "1"})
        self.assertEqual(entry["command"], self.launch().command)

    def test_refuses_without_confirmation_when_not_interactive(self) -> None:
        with mock.patch.object(sys, "stdin", io.StringIO("y\n")):
            code, out = self.call_main("setup", "--client", "cursor")
        self.assertEqual(code, 1)
        self.assertIn("--yes", out)
        self.assertFalse(self.cursor_config().exists())

    def test_accepts_an_interactive_yes(self) -> None:
        class Tty(io.StringIO):
            def isatty(self) -> bool:
                return True

        with mock.patch.object(sys, "stdin", Tty("")), mock.patch("builtins.input", return_value="y"):
            code, out = self.call_main("setup", "--client", "cursor")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.cursor_config().is_file())

    def test_all_applies_only_to_detected_clients(self) -> None:
        self.add_fake_claude()
        self.write_claude()
        self.cursor_config().parent.mkdir(parents=True)
        self.cursor_config().write_text("{}\n", encoding="utf-8")
        calls, fake_run = self.record_run(stdout="Added\n")

        with mock.patch.object(clients, "run_command", fake_run):
            code, out = self.call_main("setup", "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:7], ["mcp", "add", "--scope", "user", "cascadeur-plus", "--"])
        self.assertTrue(self.cursor_config().is_file())
        self.assertIn("Codex CLI: not detected, skipped", out)
        self.assertIn("VS Code: not detected, skipped", out)

    def test_invalid_json_is_reported_without_writing(self) -> None:
        self.cursor_config().parent.mkdir(parents=True)
        broken = '{"mcpServers": '
        self.cursor_config().write_text(broken, encoding="utf-8")
        code, out = self.call_main("setup", "--client", "cursor", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", out)
        self.assertEqual(self.cursor_config().read_text(encoding="utf-8"), broken)
        self.assertEqual(list(self.cursor_config().parent.glob("*.bak")), [])


class ClaudeCodeTests(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.claude = self.add_fake_claude()

    def test_plan_uses_the_claude_cli_with_user_scope(self) -> None:
        plan = clients.plan_setup("claude-code", clients.Launch("C:/python/python.exe", ("-I", "C:/app/proxy.py")))
        self.assertTrue(plan.installed)
        self.assertEqual(os.path.normcase(plan.argv[0]), os.path.normcase(str(self.claude)))
        self.assertEqual(plan.argv[1:7], ["mcp", "add", "--scope", "user", "cascadeur-plus", "--"])
        self.assertEqual(plan.argv[7:], ["C:/python/python.exe", "-I", "C:/app/proxy.py"])

    def test_dry_run_shows_the_entry_that_would_be_added(self) -> None:
        self.write_claude()
        calls, fake = self.record_run()
        with mock.patch.object(clients, "run_command", fake):
            code, out = self.call_main("setup", "--client", "claude-code", "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("current entry: none", out)
        self.assertIn(self.launch().command, out)
        self.assertIn("change: add", out)
        self.assertIn("mcp add --scope user cascadeur-plus --", out)
        self.assertEqual(calls, [])

    def test_dry_run_shows_a_replacement_with_the_existing_entry(self) -> None:
        original = self.write_claude({"command": "old", "args": []}).read_text(encoding="utf-8")
        calls, fake = self.record_run()
        with mock.patch.object(clients, "run_command", fake):
            code, out = self.call_main("setup", "--client", "claude-code", "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn('"command": "old"', out)
        self.assertIn("update (replace the existing registration)", out)
        self.assertIn("mcp remove --scope user cascadeur-plus", out)
        self.assertIn("mcp add --scope user cascadeur-plus --", out)
        self.assertIn("will become:", out)
        self.assertEqual(calls, [])
        self.assertEqual(self.claude_config().read_text(encoding="utf-8"), original)

    def test_apply_replaces_an_existing_entry_with_remove_then_add(self) -> None:
        original = self.write_claude({"command": "old", "args": []}).read_text(encoding="utf-8")
        calls, fake = self.record_run(stdout="done\n")
        with mock.patch.object(clients, "run_command", fake):
            code, out = self.call_main("setup", "--client", "claude-code", "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1:], ["mcp", "remove", "--scope", "user", "cascadeur-plus"])
        self.assertEqual(calls[1][1:7], ["mcp", "add", "--scope", "user", "cascadeur-plus", "--"])
        self.assertEqual(self.claude_config().read_text(encoding="utf-8"), original, "the claude CLI owns the file")
        backups = list(self.home.glob(".claude.json.cascadeur-mcp-plus.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), original)

    def test_apply_refuses_to_run_the_cli_without_a_backup(self) -> None:
        calls, fake = self.record_run()
        with mock.patch.object(clients, "run_command", fake):
            code, out = self.call_main("setup", "--client", "claude-code", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("could not be backed up", out)
        self.assertIn("CLAUDE_CONFIG_DIR", out)
        self.assertEqual(calls, [])
        self.assertFalse(self.claude_config().exists())

    def test_apply_runs_the_cli_and_never_writes_the_config_itself(self) -> None:
        original = self.write_claude().read_text(encoding="utf-8")
        calls, fake = self.record_run(stdout="Added MCP server cascadeur-plus\n")
        with mock.patch.object(clients, "run_command", fake):
            code, out = self.call_main("setup", "--client", "claude-code", "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.claude_config().read_text(encoding="utf-8"), original)
        backups = list(self.home.glob(".claude.json.cascadeur-mcp-plus.*.bak"))
        self.assertEqual(len(backups), 1)

    def test_uninstall_shows_the_existing_entry_and_removes_it(self) -> None:
        entry = {"command": "x", "args": ["serve"], "env": {"A": "1"}}
        self.write_claude(entry)
        calls, fake = self.record_run(stdout="Removed\n")
        with mock.patch.object(clients, "run_command", fake):
            code, out = self.call_main("uninstall", "--client", "claude-code", "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn('"command": "x"', out)
        self.assertIn('"env": {"A": "1"}', out)
        self.assertIn("change: remove", out)
        self.assertIn("will become:   none", out)
        self.assertEqual(calls, [])

        with mock.patch.object(clients, "run_command", fake):
            code, out = self.call_main("uninstall", "--client", "claude-code", "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:], ["mcp", "remove", "--scope", "user", "cascadeur-plus"])
        self.assertEqual(len(list(self.home.glob(".claude.json.cascadeur-mcp-plus.*.bak"))), 1)

    def test_uninstall_without_an_entry_is_a_no_op(self) -> None:
        self.write_claude()
        calls, fake = self.record_run()
        with mock.patch.object(clients, "run_command", fake):
            code, out = self.call_main("uninstall", "--client", "claude-code", "--yes")
        self.assertEqual(code, 0, out)
        self.assertIn("Nothing to remove", out)
        self.assertEqual(calls, [])

    def test_explicit_claude_code_apply_requires_the_cli(self) -> None:
        self.claude.unlink()
        code, out = self.call_main("setup", "--client", "claude-code", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("not installed", out)


class ConfigFileSafetyTests(CliTestCase):
    def test_json_bom_and_crlf_are_preserved(self) -> None:
        self.cursor_config().parent.mkdir(parents=True)
        body = '{\r\n  "mcpServers": {\r\n    "other": {"command": "node"}\r\n  }\r\n}\r\n'
        self.cursor_config().write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
        code, out = self.call_main("setup", "--client", "cursor", "--yes")
        self.assertEqual(code, 0, out)
        raw = self.cursor_config().read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "the UTF-8 BOM must survive")
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), "no LF-only line may be written into a CRLF file")
        data = json.loads(raw.decode("utf-8-sig"))
        self.assertIn("cascadeur-plus", data["mcpServers"])

    def test_non_utf8_file_is_reported_without_a_traceback(self) -> None:
        self.cursor_config().parent.mkdir(parents=True)
        broken = b"\xff\xfe\x00\x00"
        self.cursor_config().write_bytes(broken)
        code, out = self.call_main("setup", "--client", "cursor", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("not UTF-8", out)
        self.assertEqual(self.cursor_config().read_bytes(), broken)

    def test_unreadable_path_is_reported_clearly(self) -> None:
        self.cursor_config().mkdir(parents=True)
        code, out = self.call_main("setup", "--client", "cursor", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("could not read", out)
        self.assertTrue(self.cursor_config().is_dir())


class CodexTomlTests(CliTestCase):
    LAUNCH = clients.Launch("C:/python/python.exe", ("-I", "C:/app/proxy.py"))

    def plan(self, text: str):
        return clients.toml_add(text, self.codex_config(), self.LAUNCH)

    def test_inline_mcp_servers_is_refused_before_any_write(self) -> None:
        self.codex_config().parent.mkdir(parents=True)
        original = 'mcp_servers = { other = { command = "node" } }\n'
        self.codex_config().write_text(original, encoding="utf-8")
        code, out = self.call_main("setup", "--client", "codex", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("inline table", out)
        self.assertEqual(self.codex_config().read_text(encoding="utf-8"), original)
        self.assertEqual(list(self.codex_config().parent.glob("*.bak")), [])

    def test_inline_entry_under_the_header_is_refused(self) -> None:
        self.codex_config().parent.mkdir(parents=True)
        original = '[mcp_servers]\ncascadeur-plus = { command = "old", args = [] }\n'
        self.codex_config().write_text(original, encoding="utf-8")
        code, out = self.call_main("setup", "--client", "codex", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("without a [cascadeur-plus] table", out)
        self.assertEqual(self.codex_config().read_text(encoding="utf-8"), original)

    def test_header_with_an_inline_entry_accepts_a_new_subtable(self) -> None:
        original = '[mcp_servers]\nother = { command = "node" }\n'
        after = self.plan(original)
        data = tomllib.loads(after)
        self.assertEqual(data["mcp_servers"]["other"]["command"], "node")
        self.assertEqual(data["mcp_servers"]["cascadeur-plus"]["command"], self.LAUNCH.command)

    def test_existing_table_keeps_env_comments_and_multiline_args_are_replaced(self) -> None:
        original = (
            '[mcp_servers.cascadeur-plus]\n# keep me\ncommand = "old"\nargs = [\n  "old",\n]\nenv = { FOO = "1" }\n'
        )
        after = self.plan(original)
        data = tomllib.loads(after)
        entry = data["mcp_servers"]["cascadeur-plus"]
        self.assertEqual(entry["command"], self.LAUNCH.command)
        self.assertEqual(entry["args"], list(self.LAUNCH.args))
        self.assertEqual(entry["env"], {"FOO": "1"})
        self.assertIn("# keep me", after)
        self.assertNotIn('"old",', after)
        self.assertEqual(after.count("args ="), 1)

    def test_add_validates_the_new_document(self) -> None:
        with mock.patch.object(clients, "_validate_toml", side_effect=clients.CliError("invalid result")):
            with self.assertRaises(clients.CliError):
                self.plan('[windows]\nsandbox = "elevated"\n')

    def test_uninstall_removes_the_table_and_its_subtables(self) -> None:
        self.codex_config().parent.mkdir(parents=True)
        self.codex_config().write_text(
            '[windows]\nsandbox = "elevated"\n\n'
            '[mcp_servers.cascadeur-plus]\ncommand = "x"\nargs = ["serve"]\n\n'
            '[mcp_servers.cascadeur-plus.env]\nFOO = "bar"\n\n'
            '[mcp_servers.other]\ncommand = "node"\n',
            encoding="utf-8",
        )
        code, out = self.call_main("uninstall", "--client", "codex", "--yes")
        self.assertEqual(code, 0, out)
        text = self.codex_config().read_text(encoding="utf-8")
        data = tomllib.loads(text)
        self.assertNotIn("cascadeur-plus", data["mcp_servers"])
        self.assertEqual(data["mcp_servers"]["other"]["command"], "node")
        self.assertEqual(data["windows"]["sandbox"], "elevated")
        self.assertNotIn("cascadeur-plus", text)
        self.assertNotIn("\n\n\n", text)

    def test_uninstall_removes_a_subtable_placed_after_another_table(self) -> None:
        self.codex_config().parent.mkdir(parents=True)
        self.codex_config().write_text(
            '[mcp_servers.cascadeur-plus]\ncommand = "x"\nargs = ["serve"]\n\n'
            '[mcp_servers.other]\ncommand = "node"\n\n'
            '[mcp_servers.cascadeur-plus.env]\nFOO = "bar"\n',
            encoding="utf-8",
        )
        code, out = self.call_main("uninstall", "--client", "codex", "--yes")
        self.assertEqual(code, 0, out)
        text = self.codex_config().read_text(encoding="utf-8")
        data = tomllib.loads(text)
        self.assertEqual(list(data["mcp_servers"]), ["other"])
        self.assertNotIn("FOO", text)
        self.assertNotIn("\n\n\n", text)

    def test_uninstall_validates_the_new_document(self) -> None:
        original = '[mcp_servers.cascadeur-plus]\ncommand = "x"\nargs = ["serve"]\n'
        with mock.patch.object(clients, "_validate_toml", side_effect=clients.CliError("invalid result")):
            with self.assertRaises(clients.CliError):
                clients.toml_remove(original, self.codex_config())

    def test_uninstall_reports_an_inline_entry_instead_of_corrupting_it(self) -> None:
        self.codex_config().parent.mkdir(parents=True)
        original = '[mcp_servers]\ncascadeur-plus = { command = "x", args = ["serve"] }\n'
        self.codex_config().write_text(original, encoding="utf-8")
        code, out = self.call_main("uninstall", "--client", "codex", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("inline table", out)
        self.assertEqual(self.codex_config().read_text(encoding="utf-8"), original)


class UninstallTests(CliTestCase):
    def test_json_uninstall_removes_only_our_entry(self) -> None:
        self.cursor_config().parent.mkdir(parents=True)
        self.cursor_config().write_text(
            json.dumps(
                {"mcpServers": {"other": {"command": "node"}, "cascadeur-plus": {"command": "x", "args": ["serve"]}}}
            )
            + "\n",
            encoding="utf-8",
        )
        code, out = self.call_main("uninstall", "--client", "cursor", "--yes")
        self.assertEqual(code, 0, out)
        data = json.loads(self.cursor_config().read_text(encoding="utf-8"))
        self.assertNotIn("cascadeur-plus", data["mcpServers"])
        self.assertEqual(data["mcpServers"]["other"], {"command": "node"})
        self.assertEqual(len(list(self.cursor_config().parent.glob("*.bak"))), 1)

    def test_dry_run_shows_the_removal_without_writing(self) -> None:
        self.cursor_config().parent.mkdir(parents=True)
        original = (
            json.dumps(
                {
                    "mcpServers": {
                        "other": {"command": "node"},
                        "cascadeur-plus": {"command": "x", "args": ["serve"]},
                    }
                },
                indent=2,
            )
            + "\n"
        )
        self.cursor_config().write_text(original, encoding="utf-8")
        result = self.run_cli("uninstall", "--client", "cursor", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('-    "cascadeur-plus": {', result.stdout)
        self.assertIn("Dry run", result.stdout)
        self.assertEqual(self.cursor_config().read_text(encoding="utf-8"), original)

    def test_uninstall_without_a_registration_is_a_no_op(self) -> None:
        code, out = self.call_main("uninstall", "--client", "cursor", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("Nothing to remove", out)
        self.assertFalse(self.cursor_config().exists())


class DoctorTests(CliTestCase):
    def test_reports_both_servers_up(self) -> None:
        plus = FakeServer("plus")
        official = FakeServer("official")
        self.addCleanup(plus.close)
        self.addCleanup(official.close)
        cascadeur = self.home / "cascadeur.exe"
        cascadeur.write_bytes(b"")
        with mock.patch.dict(
            os.environ,
            {
                "CASCADEUR_PLUS_URL": plus.url,
                "CASCADEUR_OFFICIAL_URL": official.url,
                "CASCADEUR_EXE": str(cascadeur),
            },
        ):
            code, out = self.call_main("doctor", "--json")
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        by_name = {check["name"]: check for check in payload["checks"]}
        self.assertEqual(by_name["MCP Plus"]["level"], "ok")
        self.assertEqual(by_name["official script server"]["level"], "ok")
        self.assertEqual(by_name["Cascadeur"]["level"], "ok")
        self.assertIn("up", by_name["MCP Plus"]["detail"])

    def test_reports_the_runtime_directory(self) -> None:
        cascadeur = self.home / "cascadeur.exe"
        cascadeur.write_bytes(b"")
        with mock.patch.dict(os.environ, {"CASCADEUR_EXE": str(cascadeur)}):
            code, out = self.call_main("doctor", "--json")
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertEqual(os.path.normcase(payload["runtime"]), os.path.normcase(str(ROOT)))
        by_name = {check["name"]: check for check in payload["checks"]}
        self.assertEqual(by_name["runtime"]["level"], "ok")
        self.assertIn("sys.path", by_name["runtime"]["detail"])

    def test_down_servers_are_warnings_not_failures(self) -> None:
        cascadeur = self.home / "cascadeur.exe"
        cascadeur.write_bytes(b"")
        with mock.patch.dict(os.environ, {"CASCADEUR_EXE": str(cascadeur)}):
            code, out = self.call_main("doctor", "--json")
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        by_name = {check["name"]: check for check in payload["checks"]}
        self.assertEqual(by_name["MCP Plus"]["level"], "warn")
        self.assertEqual(by_name["official script server"]["level"], "warn")
        self.assertIn("not running", by_name["MCP Plus"]["detail"])

    def test_foreign_program_on_the_plus_port_is_a_failure(self) -> None:
        foreign = FakeServer("foreign")
        official = FakeServer("official")
        self.addCleanup(foreign.close)
        self.addCleanup(official.close)
        cascadeur = self.home / "cascadeur.exe"
        cascadeur.write_bytes(b"")
        with mock.patch.dict(
            os.environ,
            {
                "CASCADEUR_PLUS_URL": foreign.url,
                "CASCADEUR_OFFICIAL_URL": official.url,
                "CASCADEUR_EXE": str(cascadeur),
            },
        ):
            code, out = self.call_main("doctor", "--json")
        self.assertEqual(code, 1, out)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        by_name = {check["name"]: check for check in payload["checks"]}
        self.assertEqual(by_name["MCP Plus"]["level"], "fail")
        self.assertIn("another program", by_name["MCP Plus"]["detail"])

    def test_missing_cascadeur_is_a_failure(self) -> None:
        with mock.patch.object(doctor, "cascadeur_install", return_value=None):
            code, out = self.call_main("doctor")
        self.assertEqual(code, 1, out)
        self.assertIn("[FAIL] Cascadeur", out)
        self.assertIn("problems", out)

    def test_human_output_lists_checks_and_hints(self) -> None:
        cascadeur = self.home / "cascadeur.exe"
        cascadeur.write_bytes(b"")
        with mock.patch.dict(os.environ, {"CASCADEUR_EXE": str(cascadeur)}):
            code, out = self.call_main("doctor")
        self.assertEqual(code, 0, out)
        self.assertIn("Cascadeur MCP Plus doctor", out)
        self.assertIn("[WARN] MCP Plus", out)
        self.assertIn("Start script server", out)
        self.assertIn("checks:", out)


class ServeTests(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        # A live tools/list makes the proxy cache the list in the checkout; put it back.
        self._manifests = {}
        for name in ("tools_manifest.json", "resources_manifest.json"):
            path = ROOT / name
            self._manifests[path] = path.read_bytes() if path.is_file() else None
        self.addCleanup(self._restore_manifests)

    def _restore_manifests(self) -> None:
        for path, data in self._manifests.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)

    def _start(self, argv: list[str], cwd: str | None = None) -> subprocess.Popen:
        env = os.environ.copy()
        proc = subprocess.Popen(
            argv,
            cwd=cwd or str(ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.addCleanup(proc.kill)
        return proc

    def _rpc(self, proc: subprocess.Popen, message: dict, timeout: float = 30.0) -> dict:
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
        result: dict = {}

        def read() -> None:
            result["line"] = proc.stdout.readline()

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        thread.join(timeout)
        self.assertIn("line", result, f"no response to {message['method']}")
        self.assertTrue(result["line"], f"proxy closed the connection for {message['method']}")
        return json.loads(result["line"])

    def _exercise(self, argv: list[str], cwd: str | None = None) -> None:
        plus = FakeServer("plus")
        self.addCleanup(plus.close)
        with mock.patch.dict(
            os.environ,
            {
                "CASCADEUR_PLUS_URL": plus.url,
                "CASCADEUR_OFFICIAL_URL": f"http://127.0.0.1:{free_port()}",
            },
        ):
            proc = self._start(argv, cwd=cwd)
            try:
                hello = self._rpc(
                    proc,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18"},
                    },
                )
                self.assertEqual(hello["result"]["serverInfo"]["name"], "cascadeur-mcp-plus-proxy")
                self.assertIn("tools", hello["result"]["capabilities"])

                listing = self._rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                self.assertEqual(listing["result"]["tools"], [{"name": "live_tool"}])

                call = self._rpc(
                    proc,
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "get_scene_info", "arguments": {}},
                    },
                )
                self.assertEqual(call["result"]["content"][0]["text"], "forwarded")
            finally:
                proc.stdin.close()
                proc.wait(timeout=15)
                proc.stdout.close()

    def test_serve_subcommand_runs_the_proxy(self) -> None:
        self._exercise([sys.executable, "-m", "cascadeur_mcp_plus_cli", "serve"])

    def test_python_proxy_py_still_works(self) -> None:
        self._exercise([sys.executable, "-I", str(ROOT / "proxy.py")])

    def test_python_proxy_py_works_from_another_directory(self) -> None:
        # Isolated mode does not add the script directory to sys.path; the launcher must
        # still find the implementation and the runtime through __file__.
        self._exercise([sys.executable, "-I", str(ROOT / "proxy.py")], cwd=str(self.home))

    def test_dash_dash_version(self) -> None:
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cascadeur-mcp-plus", result.stdout)


if __name__ == "__main__":
    unittest.main()
