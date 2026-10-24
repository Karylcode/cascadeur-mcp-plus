"""Install, remove and inspect a Cascadeur application_started handler for MCP Plus.

Cascadeur imports every module in resources/scripts/python/events/application_started/ at launch
and calls its run() (T51). The handler generated here starts Cascadeur's official MCP script
server and MCP Plus on the first scene idle after launch, so nobody has to click
Scripts > MCP > Start script server. That file lives in the installation directory, so installing
or removing it needs one UAC prompt; the stable runtime copy and the manifest are per user.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from . import doctor, runtime_dir, version

DEFAULT_METHOD = "admin"
METHODS = ("admin", "user")

# The first lines of the generated handler. disable only deletes files that carry MARKER, and
# enable treats files carrying LEGACY_MARKER as the temporary add-on it replaces.
MARKER = "# Managed by cascadeur-mcp-plus autostart"
LEGACY_MARKER = "MCP-SCRIPT-SERVER-AUTOSTART"
HANDLER_NAME = "mcp_plus_autostart.py"
LEGACY_NAME = "mcp_script_server_autostart.py"

MANIFEST_SCHEMA = 1
UAC_TIMEOUT = 180.0  # seconds to wait for the elevated helper; the user answers UAC in that time
WAIT_TIMEOUT = 258
UAC_CANCELLED = 1223


class AutostartError(Exception):
    """A problem the user has to fix or decide on; hint says what to do next."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def local_app_data() -> Path:
    """The per-user data folder this add-on writes to (T55: the same rules as clients)."""
    from . import clients

    return clients.local_app_data()


def state_dir() -> Path:
    return local_app_data() / "cascadeur-mcp-plus"


def manifest_path() -> Path:
    return state_dir() / "autostart.json"


def runtime_root() -> Path:
    return state_dir() / "runtime"


def backup_dir() -> Path:
    return state_dir() / "backup" / "application_started"


def cascadeur_root() -> Path | None:
    """The Cascadeur installation directory, or None when it cannot be found."""
    candidates: list[Path] = []
    for variable in ("CASCADEUR_HOME", "CASCADEUR_DIR"):
        value = os.environ.get(variable)
        if value:
            candidates.append(Path(value))
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        value = os.environ.get(variable)
        if value:
            candidates.append(Path(value) / "Cascadeur")
            candidates.append(Path(value) / "Programs" / "Cascadeur")
    for candidate in candidates:
        if candidate.is_file():
            candidate = candidate.parent
        if (candidate / "cascadeur.exe").is_file() and (
            candidate / "resources" / "scripts" / "python" / "events_rule.py"
        ).is_file():
            return candidate
    return None


def application_started_dir(root: str | Path) -> Path:
    return Path(root) / "resources" / "scripts" / "python" / "events" / "application_started"


def handler_path(root: str | Path) -> Path:
    return application_started_dir(root) / HANDLER_NAME


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path: str | Path) -> str:
    with open(path, encoding="utf-8-sig", errors="replace") as handle:
        return handle.read()


def first_line(path: str | Path, limit: int = 4096) -> str:
    with open(path, encoding="utf-8-sig", errors="replace") as handle:
        return handle.readline(limit).rstrip("\r\n")


def carries_marker(path: str | Path, marker: str) -> bool:
    """Whether the first line carries marker: ownership is never inferred from later lines."""
    try:
        return marker in first_line(path)
    except OSError:
        return False


def snapshot(directory: str | Path) -> list[dict]:
    """Name and sha256 of every file directly in directory (Cascadeur's import list)."""
    folder = Path(directory)
    if not folder.is_dir():
        return []
    rows = []
    for entry in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
        if entry.is_file():
            rows.append({"name": entry.name, "sha256": sha256_file(entry)})
    return rows


def legacy_handlers(directory: str | Path) -> list[dict]:
    """The temporary script-server handler(s) in the folder, found by their own marker."""
    folder = Path(directory)
    rows = []
    if not folder.is_dir():
        return rows
    for entry in sorted(folder.glob("*.py")):
        if entry.name == HANDLER_NAME or not carries_marker(entry, LEGACY_MARKER):
            continue
        rows.append({"name": entry.name, "path": str(entry), "sha256": sha256_file(entry)})
    return rows


def pycache_stem(name: str) -> str:
    return Path(name).stem


# ---------------------------------------------------------------------------
# The generated handler
# ---------------------------------------------------------------------------

_HANDLER_TEMPLATE = """# Managed by cascadeur-mcp-plus autostart. Remove with: cascadeur-mcp-plus autostart disable
#
# Cascadeur imports every module in events/application_started at launch and calls run().
# run() only queues work for the first scene idle: starting the servers inline would run them
# while the event system is still calling handlers, and subscribing to the idle manager from
# inside its own callback loop would modify the dictionary it is iterating. The queued callback
# then starts Cascadeur's official MCP script server (127.0.0.1:8765) the same way
# scripts/mcp/start_server.py does, and Cascadeur MCP Plus (127.0.0.1:__PORT__) by running the
# start script below. Every failure is caught and printed with this tag: a broken autostart must
# never stop Cascadeur from starting, and the official script server keeps working without MCP
# Plus.
#
# Generated by cascadeur-mcp-plus __VERSION__. "autostart enable" replaces this file and
# "autostart disable" deletes it; do not edit it by hand.
#
# Security: the official script server (127.0.0.1:8765) and MCP Plus (127.0.0.1:__PORT__) run any
# Python sent to them and have no authentication, so with this file in place both local
# code-execution ports are open whenever Cascadeur is open. If Cascadeur is started as
# administrator, so are both servers. Disable this handler when it is not needed.
import os
import traceback

_TAG = "[Cascadeur MCP Plus] autostart"
_START_SCRIPT = __START_SCRIPT__
_PORT = __PORT__
_OFFICIAL_SERVER = "scripts.mcp.script_server.server"
_state = {"queued": False, "done": False}


def _log(message):
    try:
        print(f"{_TAG} {message}")
    except Exception:
        pass


def _log_exception(context):
    try:
        _log(f"failed ({context}):")
        for line in traceback.format_exc().splitlines():
            _log(line)
    except Exception:
        pass


def _current_scene():
    # start() ignores the scene in 2026.2.3; pass the real one in case a later version uses it.
    try:
        import csc

        app = csc.app.get_application()
        view = app.current_scene() if app else None
        return view.domain_scene() if view else None
    except Exception:
        return None


def _start_official_server():
    try:
        import importlib

        server = importlib.import_module(_OFFICIAL_SERVER)
        if server.is_running():
            _log("official script server is already running")
            return
        importlib.reload(server)
        server.start(_current_scene())
        if server.is_running():
            _log(f"official script server started at http://{server.HOST}:{server.PORT}")
        else:
            _log("official script server did not start (see the lines above)")
    except Exception:
        _log_exception("starting the official script server")


def _start():
    if _state["done"]:
        return
    _state["done"] = True
    try:
        _start_official_server()
    except Exception:
        _log_exception("starting the official script server")
    try:
        if not os.path.isfile(_START_SCRIPT):
            _log("missing " + _START_SCRIPT + "; reinstall cascadeur-mcp-plus and run autostart enable again")
            return
        with open(_START_SCRIPT, encoding="utf-8") as handle:
            code = handle.read()
        exec(compile(code, _START_SCRIPT, "exec"), {"__file__": _START_SCRIPT, "MCP_PLUS_PORT": _PORT})
    except Exception:
        _log_exception("starting Cascadeur MCP Plus")


def run(*args, **kwargs):
    try:
        if _state["queued"]:
            return
        _state["queued"] = True
        from events import scene_idle_manager

        queue = getattr(scene_idle_manager, "execute_when_idle", None)
        if queue is not None:
            queue(_start)
            _log("will start the official script server and MCP Plus on the first scene idle")
            return
        _log("scene_idle_manager.execute_when_idle is missing (Cascadeur changed?); using a Qt timer")
    except Exception:
        _log_exception("queueing the autostart")
    try:
        from PySide6 import QtCore

        QtCore.QTimer.singleShot(0, _start)
    except Exception:
        _log_exception("deferring the autostart")
        _start()
"""


def render_handler(start_script: str | Path, port: int, package_version: str | None = None) -> str:
    """The exact handler source written to application_started/; compiled before every write."""
    text = _HANDLER_TEMPLATE
    text = text.replace("__START_SCRIPT__", repr(str(start_script)))
    text = text.replace("__PORT__", str(int(port)))
    text = text.replace("__VERSION__", package_version or version())
    return text


# ---------------------------------------------------------------------------
# The elevated helper
# ---------------------------------------------------------------------------

_HELPER_SOURCE = r'''"""Apply one Cascadeur MCP Plus autostart plan (run elevated, stdlib only).

The source arrives through "python -I -c" and the plan's SHA-256 is a command-line argument, so
nothing a non-elevated process can write decides what runs with administrator rights. Every path
is checked against the events folder and the CLI's own backup folder before it is touched.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

ALLOWED = ("mcp_plus_autostart.py", "mcp_script_server_autostart.py")
ALLOWED_STEMS = tuple(name[:-3] for name in ALLOWED)
_staging = {"target_dir": "", "backup_root": ""}


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _first_line(path):
    with open(path, encoding="utf-8-sig", errors="replace") as handle:
        return handle.readline(4096).rstrip("\r\n")


def _has_marker(path, marker):
    try:
        return marker in _first_line(path)
    except OSError:
        return False


def _same_folder(path, directory):
    return os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(directory))


def _same_dir(path, directory):
    parent = os.path.dirname(os.path.abspath(path))
    return _same_folder(parent, directory)


def _under(path, root):
    path = os.path.normcase(os.path.abspath(path))
    root = os.path.normcase(os.path.abspath(root))
    return path == root or path.startswith(root + os.sep)


def _target(path):
    if not _same_dir(path, _staging["target_dir"]) or os.path.basename(path) not in ALLOWED:
        raise ValueError(path + " is outside the events folder this tool may write")
    return path


def _backup_file(path):
    if not _under(path, _staging["backup_root"]):
        raise ValueError(path + " is outside the backup folder this tool may use")
    return path


def _write(path, content, digest, expect_sha256, expect_marker):
    _target(path)
    if expect_sha256 is None:
        if os.path.exists(path):
            raise ValueError(path + " appeared after the plan was made; not overwriting it")
    else:
        if not os.path.isfile(path):
            raise ValueError(path + " disappeared after the plan was made")
        if _sha256(path) != expect_sha256:
            raise ValueError(path + " changed after the plan was made; not overwriting it")
        if expect_marker and not _has_marker(path, expect_marker):
            raise ValueError(path + " is no longer ours; not overwriting it")
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != digest:
        raise ValueError("content changed before it was written")
    compile(content, path, "exec")
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        raise ValueError("missing directory " + directory)
    handle, temporary = tempfile.mkstemp(prefix=".mcp_plus_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary and os.path.exists(temporary):
            os.remove(temporary)
    if _sha256(path) != digest:
        raise ValueError("written file does not match the planned hash")


def _backup(path, destination, expect):
    _target(path)
    _backup_file(destination)
    if expect and _sha256(path) != expect:
        raise ValueError(path + " changed since the plan was made")
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    shutil.copy2(path, destination)
    if _sha256(destination) != _sha256(path):
        raise ValueError("backup " + destination + " does not match " + path)


def _restore(backup, path, expect, only_if_missing):
    _backup_file(backup)
    _target(path)
    if _sha256(backup) != expect:
        raise ValueError("backup " + backup + " changed since the plan was made")
    if only_if_missing and os.path.exists(path):
        raise ValueError(path + " appeared after the plan was made; not overwriting it")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shutil.copy2(backup, path)
    if _sha256(path) != expect:
        raise ValueError("restored file " + path + " does not match " + backup)


def _remove(path, marker, expect):
    _target(path)
    if not os.path.isfile(path):
        raise ValueError("missing " + path)
    if marker and not _has_marker(path, marker):
        raise ValueError(path + " does not carry the expected marker")
    if expect and _sha256(path) != expect:
        raise ValueError(path + " changed since the plan was made")
    os.remove(path)


def _remove_pycache(directory, stem):
    if not _same_folder(directory, _staging["target_dir"]) or stem not in ALLOWED_STEMS:
        raise ValueError(directory + " is outside the events folder this tool may clean")
    cache = os.path.join(directory, "__pycache__")
    if not os.path.isdir(cache):
        return
    for name in os.listdir(cache):
        if name.startswith(stem + ".") and name.endswith(".pyc"):
            os.remove(os.path.join(cache, name))


def _apply(operation):
    action = operation.get("action")
    if action == "backup":
        _backup(operation["path"], operation["destination"], operation.get("expect_sha256"))
    elif action == "restore":
        _restore(
            operation["backup"],
            operation["path"],
            operation.get("expect_sha256"),
            operation.get("only_if_missing", False),
        )
    elif action == "remove":
        _remove(operation["path"], operation.get("marker"), operation.get("expect_sha256"))
    elif action == "write":
        _write(
            operation["path"],
            operation["content"],
            operation["sha256"],
            operation.get("expect_sha256"),
            operation.get("expect_marker"),
        )
    elif action == "remove_pycache":
        _remove_pycache(operation["directory"], operation["stem"])
    else:
        raise ValueError("unknown action " + repr(action))
    return action


def _report(path, payload):
    if os.path.islink(path) or (os.path.exists(path) and not os.path.isfile(path)):
        raise ValueError("refusing to write the result through a link")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)


def main(argv):
    if len(argv) != 6:
        print("usage: python -I -c <helper> <plan.json> <plan-sha256> <result.json> <events-dir> <backup-dir>")
        return 2
    plan_path, plan_sha, result_path, target_dir, backup_root = argv[1:6]
    _staging["target_dir"] = target_dir
    _staging["backup_root"] = backup_root
    done = []
    try:
        with open(plan_path, "rb") as handle:
            raw = handle.read()
        if hashlib.sha256(raw).hexdigest() != plan_sha:
            raise ValueError("plan.json does not match the SHA-256 given on the command line")
        plan = json.loads(raw.decode("utf-8"))
        for operation in plan.get("operations", []):
            done.append(_apply(operation))
    except Exception as error:
        _report(
            result_path,
            {"ok": False, "error": "%s: %s" % (type(error).__name__, error), "done": done, "plan_sha256": plan_sha},
        )
        return 3
    _report(result_path, {"ok": True, "done": done, "plan_sha256": plan_sha})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''


def _is_admin() -> bool:
    """Whether this process already has the rights to write into the installation directory."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run_elevated(argv: list[str], timeout: float = UAC_TIMEOUT) -> int:
    """Run argv[0] with argv[1:] through one UAC prompt and return its exit code.

    The helper source and the plan hash are part of that command line, so no file a non-elevated
    process can write decides what runs as administrator. Raises AutostartError when the user
    cancels or the helper times out; the caller must not retry on its own, because every retry is
    another prompt.
    """
    if os.name != "nt":
        raise AutostartError("installing the handler needs Windows and one UAC prompt")
    if not argv or not argv[0]:
        raise AutostartError("cannot find this Python executable to run the elevated helper")

    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    see_mask_noclose = 0x00000040
    see_mask_noasync = 0x00000100
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(ShellExecuteInfo)
    info.fMask = see_mask_noclose | see_mask_noasync
    info.lpVerb = "runas"
    info.lpFile = argv[0]
    info.lpParameters = subprocess.list2cmdline(argv[1:])
    info.nShow = 0  # SW_HIDE: the helper has no window of its own
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == UAC_CANCELLED:
            raise AutostartError("the UAC prompt was cancelled; nothing was changed")
        raise AutostartError(f"could not elevate the helper (Windows error {error})")
    handle = info.hProcess
    if not handle:
        raise AutostartError("elevation returned no process handle")
    try:
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        waited = kernel32.WaitForSingleObject(handle, int(timeout * 1000))
        if waited == WAIT_TIMEOUT:
            try:
                kernel32.TerminateProcess(handle, 1)
            except Exception:
                pass
            raise AutostartError(f"the elevated helper did not finish within {timeout:.0f} s; nothing was confirmed")
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return int(code.value)
    finally:
        kernel32.CloseHandle(handle)


def _helper_argv(
    plan_file: Path, plan_sha: str, result_file: Path, target_dir: str | Path, backup_root: str | Path
) -> list[str]:
    """The complete elevated command line: isolated Python, inline helper, plan hash, fixed paths."""
    return [
        sys.executable,
        "-I",
        "-c",
        _HELPER_SOURCE,
        str(plan_file),
        plan_sha,
        str(result_file),
        str(target_dir),
        str(backup_root),
    ]


def _execute_operations(operations: list[dict], target_dir: str | Path, backup_root: str | Path) -> dict:
    """Apply the plan in one helper run (one UAC prompt at most) and return its report.

    Only the plan file lives in the temporary directory; its SHA-256 travels on the command line
    so a rewrite during the UAC wait is detected. The helper may only touch the two handler files
    in target_dir and files under backup_root.
    """
    staging = Path(tempfile.mkdtemp(prefix="cascadeur-mcp-plus-autostart-"))
    plan_file = staging / "plan.json"
    result_file = staging / "result.json"
    try:
        raw = json.dumps({"operations": operations}, ensure_ascii=False, indent=1).encode("utf-8")
        plan_file.write_bytes(raw)
        plan_sha = hashlib.sha256(raw).hexdigest()
        argv = _helper_argv(plan_file, plan_sha, result_file, target_dir, backup_root)
        if _is_admin():
            try:
                completed = subprocess.run(argv, capture_output=True, text=True, timeout=UAC_TIMEOUT)
            except subprocess.TimeoutExpired:
                raise AutostartError(f"the helper did not finish within {UAC_TIMEOUT:.0f} s") from None
            code = completed.returncode
            stderr = (completed.stderr or "").strip()
        else:
            code = _run_elevated(argv)
            stderr = ""
        payload = None
        if result_file.is_file():
            try:
                payload = json.loads(result_file.read_text(encoding="utf-8"))
            except ValueError:
                payload = None
        if not isinstance(payload, dict):
            detail = f": {stderr}" if stderr else ""
            raise AutostartError(f"the elevated helper did not report a result (exit {code}){detail}")
        if payload.get("plan_sha256") != plan_sha:
            raise AutostartError("the helper reported a result for a different plan; nothing was confirmed")
        if not payload.get("ok"):
            raise AutostartError(f"{payload.get('error') or 'the helper failed'} (helper exit {code})")
        return payload
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def read_manifest() -> tuple[dict, str | None]:
    path = manifest_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError) as error:
        return {}, f"cannot read {path}: {error}"
    if not isinstance(data, dict):
        return {}, f"{path} is not a JSON object"
    return data, None


def write_manifest(data: dict) -> None:
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def manifest_record(data: dict) -> dict | None:
    installs = data.get("installs")
    if isinstance(installs, dict):
        record = installs.get(DEFAULT_METHOD)
        return record if isinstance(record, dict) else None
    return None


def _timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------------------
# Runtime copy
# ---------------------------------------------------------------------------


def _ignore(_directory, names):
    return [name for name in names if name == "__pycache__" or name.endswith(".pyc")]


def copy_runtime(source: str | Path, destination: str | Path) -> Path:
    """Copy start_mcp_plus.py and cascadeur_mcp_plus/ to the stable per-user runtime directory."""
    source = Path(source)
    destination = Path(destination)
    start = source / "start_mcp_plus.py"
    package = source / "cascadeur_mcp_plus" / "__init__.py"
    if not start.is_file() or not package.is_file():
        raise AutostartError(f"{source} does not contain start_mcp_plus.py and cascadeur_mcp_plus/")
    if destination.exists():
        try:
            shutil.rmtree(destination)
        except OSError:
            pass  # a running Cascadeur may hold a file; the copy below overwrites what it can
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source / "cascadeur_mcp_plus", destination / "cascadeur_mcp_plus", ignore=_ignore, dirs_exist_ok=True
    )
    shutil.copy2(start, destination / "start_mcp_plus.py")
    if (
        not (destination / "start_mcp_plus.py").is_file()
        or not (destination / "cascadeur_mcp_plus" / "__init__.py").is_file()
    ):
        raise AutostartError(f"the copied runtime at {destination} is incomplete")
    return destination


def runtime_version(runtime: str | Path | None) -> str | None:
    if not runtime:
        return None
    path = Path(runtime) / "cascadeur_mcp_plus" / "version.py"
    try:
        scope: dict = {}
        exec(path.read_text(encoding="utf-8-sig"), scope)
    except (OSError, SyntaxError):
        return None
    return str(scope["VERSION"]) if "VERSION" in scope else None


def default_port() -> int:
    """The in-app default port, read from the runtime so the handler and the server agree."""
    runtime = runtime_dir()
    if runtime:
        path = Path(runtime) / "cascadeur_mcp_plus" / "version.py"
        try:
            scope: dict = {}
            exec(path.read_text(encoding="utf-8-sig"), scope)
            if isinstance(scope.get("DEFAULT_PORT"), int):
                return int(scope["DEFAULT_PORT"])
        except (OSError, SyntaxError):
            pass
    return 48766


# ---------------------------------------------------------------------------
# Process / server probes
# ---------------------------------------------------------------------------


def cascadeur_process() -> dict:
    """Whether cascadeur.exe is running, and its working set, via tasklist (Windows only)."""
    if os.name != "nt":
        return {"running": None, "memory_mb": None}
    try:
        output = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq cascadeur.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {"running": None, "memory_mb": None}
    for row in csv.reader(output.splitlines()):
        if len(row) >= 5 and row[0].lower() == "cascadeur.exe":
            memory = "".join(character for character in row[4] if character.isdigit())
            return {"running": True, "memory_mb": int(memory) // 1024 if memory else None}
    return {"running": False, "memory_mb": None}


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def inspect_state() -> dict:
    """Everything status prints, gathered without writing anything."""
    root = cascadeur_root()
    data, manifest_error = read_manifest()
    record = manifest_record(data)
    directory = application_started_dir(root) if root else None
    target = handler_path(root) if root else None
    legacy = legacy_handlers(directory) if directory else []

    target_exists = bool(target and target.is_file())
    target_ours = bool(target_exists and carries_marker(target, MARKER))
    target_sha = sha256_file(target) if target_exists else None

    installed = False
    state = "not installed"
    if record and target_exists and target_ours and target_sha == record.get("sha256"):
        installed, state = True, "installed"
    elif record and not target_exists:
        state = "removed (Cascadeur update?)"
    elif record and target_exists and target_ours:
        state = "modified at target"
    elif not record and target_exists and target_ours:
        installed, state = True, "installed (manifest missing)"
    elif target_exists:
        state = "unknown file at target"
    elif not root:
        state = "Cascadeur not found"
    elif not directory.is_dir():
        state = "Cascadeur events folder missing"

    recorded_runtime = record.get("runtime") if record else None
    fallback = runtime_dir()
    runtime = str(recorded_runtime or fallback or "")
    runtime_ok = bool(
        runtime
        and (Path(runtime) / "start_mcp_plus.py").is_file()
        and (Path(runtime) / "cascadeur_mcp_plus" / "__init__.py").is_file()
    )
    if installed and not runtime_ok:
        state = "installed, runtime missing"

    running = doctor.probe(doctor.plus_url())
    official = doctor.probe(doctor.official_url())
    hints: list[str] = []
    if state == "removed (Cascadeur update?)":
        hints.append(
            "The manifest says this computer had the handler, but the file is gone, probably removed by a "
            "Cascadeur update. Run 'cascadeur-mcp-plus autostart enable' again."
        )
    elif state == "modified at target":
        hints.append(
            "The installed file no longer matches the manifest (edited by hand, or written by another version). "
            "Run 'cascadeur-mcp-plus autostart enable' again to rewrite it."
        )
    elif state == "unknown file at target":
        hints.append(
            "A file with the handler's name is there but cascadeur-mcp-plus did not write it. Inspect it and add "
            "--force to enable in order to back it up and replace it."
        )
    elif state == "installed, runtime missing":
        hints.append(
            "The handler is installed but its runtime copy is missing; run "
            "'cascadeur-mcp-plus autostart enable' again to restore it."
        )
    elif state == "installed (manifest missing)":
        hints.append(
            "The handler is installed but the manifest is gone, so this status cannot confirm the runtime. Run "
            "'cascadeur-mcp-plus autostart enable' again to record it."
        )
    elif state == "not installed" and legacy:
        hints.append(
            "The temporary handler from docs/tickets/bin/autostart is installed. Run "
            "'cascadeur-mcp-plus autostart enable' to replace it with the managed one (one UAC prompt)."
        )
    elif state == "Cascadeur not found":
        hints.append("Cascadeur was not found; set CASCADEUR_HOME to the installation directory.")
    if record and runtime_version(runtime) and runtime_version(runtime) != version():
        hints.append(
            f"The runtime copy is {runtime_version(runtime)} and this install is {version()}; run "
            "'cascadeur-mcp-plus autostart enable' again to update it."
        )
    if manifest_error:
        hints.append(manifest_error)

    healthy = state in ("installed", "installed (manifest missing)")
    ok = bool(healthy and runtime_ok) or state == "not installed"
    return {
        "ok": ok,
        "state": state,
        "installed": installed,
        "method": record.get("method") if record else None,
        "target": str(target) if target else None,
        "target_sha256": target_sha,
        "target_ours": target_ours,
        "manifest": str(manifest_path()),
        "manifest_error": manifest_error,
        "created": record.get("created") if record else None,
        "version": record.get("version") if record else None,
        "runtime": {
            "path": runtime or None,
            "version": runtime_version(runtime),
            "start_script": bool(runtime and (Path(runtime) / "start_mcp_plus.py").is_file()),
            "package": bool(runtime and (Path(runtime) / "cascadeur_mcp_plus" / "__init__.py").is_file()),
            "source": str(fallback) if fallback else None,
        },
        "running": {
            "up": running.kind == "plus",
            "url": doctor.plus_url(),
            "version": (running.health or {}).get("version") if running.kind == "plus" else None,
        },
        "official": {
            "up": official.kind == "official",
            "url": doctor.official_url(),
            "version": (official.health or {}).get("version") if official.kind == "official" else None,
        },
        "cascadeur": cascadeur_process(),
        "legacy": legacy,
        "hints": hints,
    }


def print_status(document: dict) -> None:
    print(f"Cascadeur MCP Plus autostart (cascadeur-mcp-plus {version()})")
    print(f"  status:       {document['state']}")
    if document["method"]:
        print(f"  method:       {document['method']} drop-in (one UAC prompt to change it)")
    else:
        print(f"  method:       {DEFAULT_METHOD} drop-in (default; not installed yet)")
    if document["target"]:
        print(f"  handler:      {document['target']}")
    if document["created"]:
        print(f"  installed:    {document['created']} (version {document['version']})")
    runtime = document["runtime"]
    if runtime["path"]:
        state = "ok" if runtime["start_script"] and runtime["package"] else "incomplete"
        print(f"  runtime:      {runtime['path']} ({runtime['version'] or 'unknown version'}, {state})")
    else:
        print("  runtime:      not found")
    running, official = document["running"], document["official"]
    print(
        f"  MCP Plus:     {'up at ' + running['url'] if running['up'] else 'not running'}"
        + (f" ({running['version']})" if running["up"] and running["version"] else "")
    )
    print(
        f"  official:     {'up at ' + official['url'] if official['up'] else 'not running'}"
        + (f" ({official['version']})" if official["up"] and official["version"] else "")
    )
    process = document["cascadeur"]
    if process["running"]:
        memory = f" ({process['memory_mb']} MB)" if process["memory_mb"] else ""
        print(f"  Cascadeur:    running{memory}")
    elif process["running"] is False:
        print("  Cascadeur:    not running")
    else:
        print("  Cascadeur:    unknown (tasklist failed)")
    if document["legacy"]:
        names = ", ".join(row["name"] for row in document["legacy"])
        print(f"  legacy:       {names} (temporary handler from docs/tickets/bin/autostart; starts only the")
        print("                official script server. 'autostart enable' replaces it with the managed handler.)")
    else:
        print("  legacy:       none")
    for hint in document["hints"]:
        print(f"  hint:         {hint}")
    print("  note:         MCP Plus accepts unauthenticated Python on 127.0.0.1; run 'autostart disable' when")
    print("                it is not needed. A Cascadeur update may delete the handler; check status after one.")


def run_status(json_output: bool = False) -> int:
    document = inspect_state()
    if json_output:
        print(json.dumps(document, ensure_ascii=False, indent=1))
    else:
        print_status(document)
    return 0 if document["ok"] else 1


# ---------------------------------------------------------------------------
# enable
# ---------------------------------------------------------------------------


def plan_enable(force: bool, method: str = DEFAULT_METHOD, runtime: str | None = None) -> dict:
    """Validate everything and build the plan enable applies; raises AutostartError."""
    if method != "admin":
        raise AutostartError(
            "--method user is not supported yet; no settings.json was changed.",
            hint="Use --method admin (one UAC prompt). The user-level Python.Path/Python.Events route is still "
            "experimental per T51.",
        )
    if os.name != "nt":
        raise AutostartError("autostart enable is only implemented on Windows")
    root = cascadeur_root()
    if root is None:
        raise AutostartError(
            "Cascadeur was not found (no cascadeur.exe with resources/scripts/python/events_rule.py).",
            hint="Set CASCADEUR_HOME to the Cascadeur installation directory and try again.",
        )
    directory = application_started_dir(root)
    if not directory.is_dir():
        raise AutostartError(
            f"{directory} does not exist; this copy of Cascadeur cannot load application_started handlers."
        )
    package_version = version()
    if runtime:
        # Follow a checkout instead of copying a snapshot; development wants the live code.
        source_path = Path(runtime).expanduser().resolve()
        if not source_path.is_dir():
            raise AutostartError(
                f"{source_path} is not a directory.",
                hint="Pass the folder that holds start_mcp_plus.py and cascadeur_mcp_plus/.",
            )
        if (
            not (source_path / "start_mcp_plus.py").is_file()
            or not (source_path / "cascadeur_mcp_plus" / "__init__.py").is_file()
        ):
            raise AutostartError(
                f"{source_path} does not contain start_mcp_plus.py and cascadeur_mcp_plus/.",
                hint="Point --runtime at a source checkout or at cascadeur_mcp_plus_runtime/ from an install.",
            )
        destination = None
        start_script = source_path / "start_mcp_plus.py"
        runtime_kind = "checkout"
    else:
        source = runtime_dir()
        if source is None:
            raise AutostartError(
                "this install has no start_mcp_plus.py and cascadeur_mcp_plus/ next to the CLI.",
                hint="Reinstall cascadeur-mcp-plus (the wheel must contain cascadeur_mcp_plus_runtime/).",
            )
        source_path = Path(source)
        destination = runtime_root() / package_version
        start_script = destination / "start_mcp_plus.py"
        runtime_kind = "snapshot"
    target = directory / HANDLER_NAME
    content = render_handler(start_script, default_port(), package_version)
    try:
        compile(content, str(target), "exec")
    except SyntaxError as error:  # pragma: no cover - the template is fixed
        raise AutostartError(f"the generated handler does not compile: {error}") from None
    digest = sha256_text(content)

    target_exists = target.is_file()
    target_ours = target_exists and carries_marker(target, MARKER)
    if target_exists and not target_ours and not force:
        raise AutostartError(
            f"{target} exists and was not written by cascadeur-mcp-plus.",
            hint="Inspect it first; add --force to back it up and replace it.",
        )
    backup = None
    if target_exists and not target_ours:
        backup = backup_dir() / f"{_timestamp()}-{target.name}"

    legacy = legacy_handlers(directory)
    unexpected = [row for row in legacy if row["name"] != LEGACY_NAME]
    if unexpected:
        raise AutostartError(
            f"{unexpected[0]['path']} carries the temporary handler's marker but is not {LEGACY_NAME}.",
            hint="This tool only replaces that file name; rename or remove the marked file, then run enable again.",
        )
    for row in legacy:
        # Keep the temporary handler so disable can put it back (the official 8765 server
        # autostart depends on it while this tool is uninstalled again).
        row["backup"] = f"{_timestamp()}-{row['name']}"
    operations: list[dict] = []
    if backup is not None:
        operations.append(
            {
                "action": "backup",
                "path": str(target),
                "destination": str(backup),
                "expect_sha256": sha256_file(target),
            }
        )
    for row in legacy:
        operations.append(
            {
                "action": "backup",
                "path": row["path"],
                "destination": str(backup_dir() / row["backup"]),
                "expect_sha256": row["sha256"],
            }
        )
    prior_sha256 = sha256_file(target) if target_exists else None
    operations.append({"action": "remove_pycache", "directory": str(directory), "stem": pycache_stem(HANDLER_NAME)})
    operations.append(
        {
            "action": "write",
            "path": str(target),
            "content": content,
            "sha256": digest,
            "expect_sha256": prior_sha256,
            "expect_marker": MARKER if target_ours else None,
        }
    )
    for row in legacy:
        operations.append(
            {"action": "remove", "path": row["path"], "marker": LEGACY_MARKER, "expect_sha256": row["sha256"]}
        )
        operations.append({"action": "remove_pycache", "directory": str(directory), "stem": pycache_stem(row["name"])})

    return {
        "method": method,
        "root": root,
        "directory": directory,
        "target": target,
        "target_exists": target_exists,
        "target_ours": target_ours,
        "backup": backup,
        "content": content,
        "sha256": digest,
        "runtime_source": str(source_path),
        "runtime_destination": destination,
        "runtime_kind": runtime_kind,
        "start_script": start_script,
        "version": package_version,
        "legacy": legacy,
        "operations": operations,
        "uac_required": not _is_admin(),
        "before": snapshot(directory),
    }


def _describe_enable_action(plan: dict) -> str:
    if not plan["target_exists"]:
        return "new file"
    if plan["target_ours"]:
        return "replaces the handler this tool installed earlier"
    return f"replaces a file that is not ours; it is backed up first to {plan['backup']}"


def print_enable_plan(plan: dict, dry_run: bool) -> None:
    print(f"Cascadeur MCP Plus autostart (cascadeur-mcp-plus {plan['version']})")
    print(f"  method:       {plan['method']} drop-in (one UAC prompt)")
    print(f"  Cascadeur:    {plan['root']}")
    print(f"  handler:      {plan['target']}")
    print(f"                {_describe_enable_action(plan)}")
    if plan["runtime_destination"] is not None:
        print(f"  runtime:      {plan['runtime_destination']}")
        print(f"                copied from {plan['runtime_source']}")
    else:
        print(f"  runtime:      {plan['runtime_source']}")
        print("                used in place (--runtime): the handler follows that directory")
    print(f"  start script: {plan['start_script']}")
    print(f"  manifest:     {manifest_path()}")
    if plan["uac_required"]:
        print("  UAC:          required (this process is not elevated)")
    else:
        print("  UAC:          not required (already elevated)")
    if plan["legacy"]:
        print("  legacy:       these temporary handlers are removed in the same prompt (backed up")
        print("                first, and restored again by 'autostart disable'):")
        for row in plan["legacy"]:
            print(f"                {row['path']} (sha256 {row['sha256']})")
            print(f"                backup: {backup_dir() / row['backup']}")
    print()
    print("Handler content (exactly what will be written):")
    print("-" * 78)
    print(plan["content"])
    print("-" * 78)
    print()
    print("Risks:")
    print("  - The handler starts Cascadeur's official script server on http://127.0.0.1:8765 and MCP")
    print("    Plus on http://127.0.0.1:48766. Neither asks for a password: both run any Python sent to")
    print("    them, so both ports are open whenever Cascadeur is open.")
    print("  - If Cascadeur is started as administrator, both servers run with those rights too.")
    print("  - A Cascadeur update can replace resources\\scripts and delete the handler; run")
    print("    'cascadeur-mcp-plus autostart status' after an update and enable again if needed.")
    print("  - 'cascadeur-mcp-plus autostart disable' removes it again (one UAC prompt).")
    print()
    if dry_run:
        print("Dry run: no files were written and no UAC prompt was shown.")


def apply_enable(plan: dict) -> int:
    if plan["runtime_destination"] is not None:
        try:
            copy_runtime(plan["runtime_source"], plan["runtime_destination"])
        except (AutostartError, OSError) as error:
            print(f"error: cannot prepare the runtime copy: {error}")
            return 1
    start_script = Path(plan["start_script"])
    if not start_script.is_file() or not (start_script.parent / "cascadeur_mcp_plus" / "__init__.py").is_file():
        print(f"error: {start_script} and its cascadeur_mcp_plus/ package are missing")
        return 1
    before = snapshot(plan["directory"])
    try:
        _execute_operations(plan["operations"], plan["directory"], backup_dir())
    except (AutostartError, OSError) as error:
        print(f"error: {error}")
        print("The handler was not installed as planned; 'autostart status' shows what is there now.")
        return 1
    target = Path(plan["target"])
    if not target.is_file() or sha256_file(target) != plan["sha256"]:
        print(f"error: {target} is missing or does not match the planned content after the elevated write")
        return 1
    try:
        compile(read_text(target), str(target), "exec")
    except SyntaxError as error:
        print(f"error: the installed handler does not compile: {error}")
        return 1
    remaining = [row for row in plan["legacy"] if Path(row["path"]).exists()]
    if remaining:
        names = ", ".join(row["name"] for row in remaining)
        print(f"error: the temporary handler(s) {names} are still installed; they are removed on the next enable")
        return 1

    record = {
        "method": plan["method"],
        "target": str(target),
        "sha256": plan["sha256"],
        "runtime": str(plan["runtime_destination"] or plan["runtime_source"]),
        "runtime_kind": plan["runtime_kind"],
        "start_script": str(plan["start_script"]),
        "cascadeur_root": str(plan["root"]),
        "version": plan["version"],
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "before": before,
        "after": snapshot(plan["directory"]),
        "removed_legacy": plan["legacy"],
        "backup": plan["backup"].name if plan["backup"] else None,
    }
    data, manifest_error = read_manifest()
    if manifest_error:
        print(f"warning: {manifest_error}; the manifest is replaced")
    data["schema"] = MANIFEST_SCHEMA
    installs = data.get("installs") if isinstance(data.get("installs"), dict) else {}
    installs[plan["method"]] = record
    data["installs"] = installs
    write_manifest(data)

    print("Installed the Cascadeur MCP Plus autostart handler.")
    print(f"  handler:   {target}")
    print(f"  sha256:    {plan['sha256']}")
    print(f"  runtime:   {plan['runtime_destination'] or plan['runtime_source']}")
    for row in plan["legacy"]:
        print(f"  removed:   {row['path']} (temporary handler; starts only the official script server)")
        print(f"             backed up to {backup_dir() / row['backup']}; 'autostart disable' restores it")
    print(f"  manifest:  {manifest_path()}")
    process = cascadeur_process()
    if process["running"]:
        print()
        print("Cascadeur is running. The handler starts on the next launch: restart Cascadeur when it is")
        print("convenient (the current session keeps its current servers).")
    else:
        print()
        print("Start Cascadeur. On the first scene idle (about 15 s) the official script server and MCP")
        print("Plus start on their own.")
    print("Security: the handler opens the official script server (8765) and MCP Plus (48766) without")
    print("authentication; 'autostart disable' removes it again.")
    return 0


def enable(dry_run: bool, assume_yes: bool, force: bool, method: str, runtime: str | None = None) -> int:
    try:
        plan = plan_enable(force=force, method=method, runtime=runtime)
    except AutostartError as error:
        print(f"error: {error}")
        if error.hint:
            print(f"hint: {error.hint}")
        return 1
    print_enable_plan(plan, dry_run=dry_run)
    if dry_run:
        return 0
    if not assume_yes and not _confirm("Install this autostart handler? [y/N] "):
        print("Cancelled: nothing was changed.")
        return 1
    return apply_enable(plan)


# ---------------------------------------------------------------------------
# disable
# ---------------------------------------------------------------------------


def _backup_name(value) -> str:
    """A plain file name inside our backup folder; the manifest is not trusted with a path."""
    name = str(value or "").strip()
    if not name or name != os.path.basename(name) or name in (".", ".."):
        raise AutostartError(
            "the manifest does not name a backup inside cascadeur-mcp-plus's own backup folder; refusing to "
            "copy it into Cascadeur.",
            hint="Run 'autostart enable' again to make a fresh backup, or remove the handler by hand.",
        )
    return name


def _print_restore_preview(path: Path, max_lines: int = 120) -> None:
    """Show exactly what an elevated restore would put into the Cascadeur installation."""
    text = read_text(path)
    lines = text.splitlines()
    print(f"                sha256: {sha256_file(path)} ({len(text.encode('utf-8'))} bytes)")
    print("                content:")
    for line in lines[:max_lines]:
        print(f"                  {line}")
    if len(lines) > max_lines:
        print(f"                  ... ({len(lines) - max_lines} more lines)")
    print()


def plan_disable(force: bool) -> dict | None:
    """The plan disable applies, or None when there is nothing of ours to remove."""
    if os.name != "nt":
        raise AutostartError("autostart disable is only implemented on Windows")
    root = cascadeur_root()
    if root is None:
        raise AutostartError(
            "Cascadeur was not found (no cascadeur.exe with resources/scripts/python/events_rule.py).",
            hint="Set CASCADEUR_HOME to the Cascadeur installation directory and try again.",
        )
    directory = application_started_dir(root)
    target = handler_path(root)
    data, manifest_error = read_manifest()
    record = manifest_record(data)
    if manifest_error:
        print(f"warning: {manifest_error}")
    target_exists = target.is_file()
    target_ours = target_exists and carries_marker(target, MARKER)
    if not target_exists and not record:
        return None
    if target_exists and not target_ours and not force:
        raise AutostartError(
            f"{target} exists and was not written by cascadeur-mcp-plus.",
            hint="Inspect it; add --force to back it up and delete it.",
        )

    operations: list[dict] = []
    backup = None
    restore = None
    restored_legacy: list[dict] = []
    if target_exists and not target_ours:
        backup = backup_dir() / f"{_timestamp()}-{target.name}"
        operations.append(
            {"action": "backup", "path": str(target), "destination": str(backup), "expect_sha256": sha256_file(target)}
        )
        operations.append({"action": "remove", "path": str(target)})
    elif record and record.get("backup"):
        # The manifest is user-writable: only a plain file name is accepted, and it must resolve
        # inside the backup folder this tool created.
        restore = backup_dir() / _backup_name(record.get("backup"))
        if not restore.is_file():
            raise AutostartError(
                f"the manifest records a backup of the file this handler replaced, but {restore} is gone.",
                hint="Delete the handler by hand (it carries the marker) or add --force.",
            )
        operations.append(
            {
                "action": "restore",
                "backup": str(restore),
                "path": str(target),
                "expect_sha256": sha256_file(restore),
                "only_if_missing": False,
            }
        )
    elif target_exists:
        operations.append(
            {"action": "remove", "path": str(target), "marker": MARKER, "expect_sha256": sha256_file(target)}
        )

    for row in record.get("removed_legacy", []) if record else []:
        name = str(row.get("name") or "")
        if name not in (HANDLER_NAME, LEGACY_NAME) or name != os.path.basename(name):
            raise AutostartError(f"the manifest names an unexpected file ({name or 'empty'}); refusing to restore it.")
        if not row.get("backup"):
            continue  # written by a version that did not back the temporary handler up
        legacy_backup = backup_dir() / _backup_name(row["backup"])
        legacy_path = Path(directory) / name
        if not legacy_backup.is_file():
            raise AutostartError(
                f"the backup of {name} is missing ({legacy_backup}); cannot restore the temporary handler.",
                hint="Reinstall it with docs/tickets/bin/autostart/install.ps1, or delete the manifest.",
            )
        if legacy_path.exists():
            if sha256_file(legacy_path) == sha256_file(legacy_backup):
                continue  # already restored
            raise AutostartError(
                f"{legacy_path} exists and differs from the backup; refusing to overwrite it.",
                hint="Move that file away, then run 'autostart disable' again.",
            )
        operations.append(
            {
                "action": "restore",
                "backup": str(legacy_backup),
                "path": str(legacy_path),
                "expect_sha256": sha256_file(legacy_backup),
                "only_if_missing": True,
            }
        )
        restored_legacy.append(
            {"name": name, "path": str(legacy_path), "backup": str(legacy_backup), "sha256": sha256_file(legacy_backup)}
        )
    operations.append({"action": "remove_pycache", "directory": str(directory), "stem": pycache_stem(HANDLER_NAME)})

    return {
        "root": root,
        "directory": directory,
        "target": target,
        "target_exists": target_exists,
        "target_ours": target_ours,
        "backup": backup,
        "restore": restore,
        "restored_legacy": restored_legacy,
        "record": record,
        "operations": operations,
        "uac_required": not _is_admin(),
    }


def print_disable_plan(plan: dict, dry_run: bool) -> None:
    print(f"Cascadeur MCP Plus autostart (cascadeur-mcp-plus {version()})")
    print(f"  handler:      {plan['target']}")
    if plan["restore"]:
        print(f"  restore:      {plan['restore']} -> {plan['target']}")
        _print_restore_preview(plan["restore"])
    elif not plan["target_ours"] and plan["target_exists"]:
        print(f"  backup:       {plan['backup']} (not ours; backed up before it is deleted)")
    for item in plan["restored_legacy"]:
        print(f"  restore:      {item['backup']} -> {item['path']}")
        print("                (the temporary handler that enable removed)")
        _print_restore_preview(Path(item["backup"]))
    if plan["uac_required"]:
        print("  UAC:          required (this process is not elevated)")
    else:
        print("  UAC:          not required (already elevated)")
    print(f"  manifest:     the autostart record is removed from {manifest_path()}")
    print("  effect:       the next Cascadeur launch no longer starts MCP Plus automatically; the")
    print("                current session is untouched.")
    print()
    if dry_run:
        print("Dry run: no files were written and no UAC prompt was shown.")


def apply_disable(plan: dict) -> int:
    try:
        _execute_operations(plan["operations"], plan["directory"], backup_dir())
    except (AutostartError, OSError) as error:
        print(f"error: {error}")
        print("Nothing else was changed; 'autostart status' shows what is there now.")
        return 1
    target = Path(plan["target"])
    if plan["restore"] is not None:
        if not target.is_file() or sha256_file(target) != sha256_file(plan["restore"]):
            print(f"error: {target} was not restored from {plan['restore']}")
            return 1
    elif target.exists():
        print(f"error: {target} still exists after the elevated delete")
        return 1
    for item in plan["restored_legacy"]:
        path = Path(item["path"])
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            print(f"error: {path} was not restored from {item['backup']}")
            return 1

    data, _ = read_manifest()
    installs = data.get("installs") if isinstance(data.get("installs"), dict) else {}
    installs.pop(DEFAULT_METHOD, None)
    data["schema"] = MANIFEST_SCHEMA
    data["installs"] = installs
    write_manifest(data)

    print("Removed the Cascadeur MCP Plus autostart handler.")
    if plan["restore"] is not None:
        print(f"  restored:  {plan['restore']} -> {target}")
    elif plan["backup"] is not None:
        print(f"  backup:    {plan['backup']}")
    for item in plan["restored_legacy"]:
        print(f"  restored:  {item['backup']} -> {item['path']} (temporary handler; the official script")
        print("             server starts automatically again)")
    print(f"  manifest:  {manifest_path()} (record removed)")

    record = plan["record"]
    if record:
        current = snapshot(plan["directory"])
        expected = {row["name"] for row in record.get("before", [])}
        if plan["restore"] is None:
            expected.discard(HANDLER_NAME)
        present = {row["name"] for row in current}
        missing = sorted(expected - present)
        extra = sorted(present - expected)
        print("  compared with the state recorded before enable:")
        print(f"    missing: {', '.join(missing) if missing else 'none'}")
        print(f"    extra:   {', '.join(extra) if extra else 'none'}")
    print()
    print("The next Cascadeur launch will not start MCP Plus automatically.")
    if plan["restored_legacy"]:
        print("The temporary handler was restored, so the official script server still starts automatically.")
    else:
        print("Open Scripts > MCP > Start script server if you need the official script server again.")
    print("The current session keeps running; restart Cascadeur to drop the current MCP Plus.")
    return 0


def disable(dry_run: bool, assume_yes: bool, force: bool) -> int:
    try:
        plan = plan_disable(force=force)
    except AutostartError as error:
        print(f"error: {error}")
        if error.hint:
            print(f"hint: {error.hint}")
        return 1
    if plan is None:
        print("Cascadeur MCP Plus autostart is not installed; nothing to remove.")
        return 0
    print_disable_plan(plan, dry_run=dry_run)
    if dry_run:
        return 0
    if not assume_yes and not _confirm("Remove this autostart handler? [y/N] "):
        print("Cancelled: nothing was changed.")
        return 1
    return apply_disable(plan)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _confirm(prompt: str) -> bool:
    if not sys.stdin or not sys.stdin.isatty():
        print("No interactive terminal: re-run with --yes to apply this change.")
        return False
    try:
        answer = input(prompt)
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def run(
    command: str,
    dry_run: bool = False,
    assume_yes: bool = False,
    force: bool = False,
    method: str = DEFAULT_METHOD,
    json_output: bool = False,
    runtime: str | None = None,
) -> int:
    if command == "status":
        return run_status(json_output=json_output)
    if command == "enable":
        return enable(dry_run=dry_run, assume_yes=assume_yes, force=force, method=method, runtime=runtime)
    if command == "disable":
        return disable(dry_run=dry_run, assume_yes=assume_yes, force=force)
    print(f"unknown autostart command: {command}")
    return 2
