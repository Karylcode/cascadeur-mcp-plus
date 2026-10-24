"""HTTP MCP server for Cascadeur MCP Plus.

Requests arrive on a background HTTP thread and are executed on Qt's GUI thread by the
dispatcher in dispatch.py, which keeps running during popups and nested event loops. The tools
themselves live in cascadeur_mcp_plus/tools/ and are loaded through the registry.

Each Cascadeur process runs its own server on the first free port from DEFAULT_PORT (T53), so
several Cascadeur windows can be open at once. /health reports the pid, scene and Cascadeur
version so the proxy can tell the instances apart.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from PySide6 import QtCore

from . import compat, dispatch, jobs
from .errors import ToolError
from .resources import load_resources
from .tools import load_tools
from .version import DEFAULT_PORT, VERSION

HOST = "127.0.0.1"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTANCE_PORT_COUNT = 10  # 48766-48775; keep in sync with the proxy's scan range
_STATE_KEY = dispatch.STATE_KEY

TOOLS = load_tools()
_TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}

RESOURCES = load_resources()
_RESOURCES_BY_URI = {resource["uri"]: resource for resource in RESOURCES}

# /health is answered on the HTTP thread, so the scene and Cascadeur version are cached on the
# GUI thread: waiting for a busy GUI there would make the proxy's identify() time out and look
# like a dead server (T53).
_scene_metadata = {"name": None, "path": None}
_cascadeur_version = None
_last_refresh = 0.0


def refresh_metadata(force: bool = False) -> None:
    """Refresh the scene name/path that /health reports (GUI thread; at most once a second)."""
    global _scene_metadata, _last_refresh
    now = time.monotonic()
    if not force and now - _last_refresh < 1.0:
        return
    _last_refresh = now
    try:
        import csc

        view_scene = csc.app.get_application().current_scene()
        _scene_metadata = {"name": str(view_scene.name()), "path": str(view_scene.get_path_name() or "")}
    except Exception:
        _scene_metadata = {"name": None, "path": None}


def _application_version() -> str | None:
    """Cascadeur's version; compat.cascadeur_version keeps the git_version-first order.

    csc.SystemVariables.git_version() is the one that matches the version users see; Qt's
    applicationVersion() and the executable's version resource both say 2026.2.0.16863 on
    Cascadeur 2026.2.3, so they are only fallbacks.
    """
    return compat.cascadeur_version()


def _tool_entry(tool: dict) -> dict:
    """One tools/list entry: the description carries the unavailability prefix (T54)."""
    return {"name": tool["name"], "description": compat.describe(tool), "inputSchema": tool["inputSchema"]}


def health_payload() -> dict:
    """What GET /health reports: the MCP Plus identity plus the Cascadeur room it serves."""
    state = _state()
    scene = _scene_metadata if isinstance(_scene_metadata, dict) else {"name": None, "path": None}
    return {
        "ok": True,
        "server": "cascadeur-mcp-plus",
        "version": VERSION,
        "pid": os.getpid(),
        "port": state["port"] if state else None,
        "scene": dict(scene),
        "cascadeur_version": _cascadeur_version,
        "queued": state["jobs"].qsize() if state else None,
    }


def _on_idle(scene=None):
    """scene_idle subscriber: refresh the cached scene, then run the queued scene-editing jobs.

    Refresh before the jobs on purpose: a job that closed a scene must not be followed by a
    SceneManager read in the same call (the closed tab's QML objects are being destroyed).
    """
    refresh_metadata()
    return dispatch.drain_idle_jobs(scene)


def _call_tool(name: str, arguments: dict) -> dict:
    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        raise KeyError(name)
    missing = compat.unavailable(name)
    if missing:
        # Tools whose APIs this Cascadeur build does not have refuse before touching the scene
        # or the UI (T54), so the caller gets the missing entry instead of an AttributeError.
        return {
            "content": [
                {"type": "text", "text": json.dumps(compat.refusal(name, missing), ensure_ascii=False, indent=1)}
            ],
            "isError": True,
        }
    args = dict(arguments or {})
    timeout = 60.0
    if tool.get("timeout_arg"):
        timeout = min(float(args.get("timeout") or 60.0), 600.0)
    settle_ms = int(args.pop("settle_ms", 150) or 0) if tool.get("settle") and not tool.get("background") else 0
    try:
        if tool.get("background"):
            # Start the job on the GUI thread; it returns as soon as Cascadeur's own async work
            # is running, so the MCP call never waits for the result.
            started = dispatch.call_on_gui(
                jobs.start_tool_job, {"tool": tool, "arguments": args}, timeout, idle=tool.get("idle", False)
            )
            # start_tool_job returns an error dict when begin() rejected the call; forward it
            # synchronously as the tool result instead of pretending a job started.
            value = jobs.started_result(started)
        elif "handler" in tool:
            value = tool["handler"](**args)
        elif tool.get("gui", True):
            value = dispatch.call_on_gui(tool["fn"], args, timeout, idle=tool.get("idle", False))
        else:
            value = tool["fn"](**args)
        if settle_ms > 0:
            # Wait on the HTTP thread so the GUI event loop runs normally (never nested).
            time.sleep(min(settle_ms, 5000) / 1000.0)
    except (ToolError, TimeoutError, TypeError) as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}

    if tool.get("image"):
        data, mime, meta = value
        return {
            "content": [{"type": "image", "data": data, "mimeType": mime}, {"type": "text", "text": json.dumps(meta)}]
        }
    is_error = isinstance(value, dict) and (value.get("ok") is False or "error" in value and len(value) <= 2)
    return {
        "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=1)}],
        "isError": bool(is_error),
    }


def _on_gui_thread() -> bool:
    app = QtCore.QCoreApplication.instance()
    return app is not None and QtCore.QThread.currentThread() is app.thread()


def call_tool(name: str, arguments: dict | None = None) -> dict:
    """Run one registered tool by name and return its MCP content result.

    Only call this from a handler tool (the HTTP thread). From Cascadeur's GUI thread the
    tool's GUI job would wait for the thread that is already waiting for it, so this raises
    immediately instead of blocking for the whole timeout.
    """
    if _on_gui_thread():
        raise RuntimeError(
            "server.call_tool() cannot run on Cascadeur's GUI thread: it would wait for a GUI job "
            "queued behind the thread that is calling it. Call it from a handler tool (HTTP thread); "
            "from a GUI tool, use dispatch.call_on_gui() for the Cascadeur part instead."
        )
    return _call_tool(name, arguments or {})


def has_tool(name: str) -> bool:
    """Return whether a tool with this name is registered (batch checks names before calling)."""
    return name in _TOOLS_BY_NAME


def _resource_entry(resource: dict) -> dict:
    return {
        "uri": resource["uri"],
        "name": resource["name"],
        "description": resource["description"],
        "mimeType": resource.get("mimeType", "text/plain"),
    }


def _read_resource(uri: str) -> dict:
    resource = _RESOURCES_BY_URI[uri]
    if resource.get("gui", True):
        value = dispatch.call_on_gui(resource["fn"], {}, 30.0, idle=resource.get("idle", False))
    else:
        value = resource["fn"]()
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)
    return {"contents": [{"uri": uri, "mimeType": resource.get("mimeType", "text/plain"), "text": text}]}


# ---------------------------------------------------------------------------
# MCP over HTTP
# ---------------------------------------------------------------------------


def _rpc_result(payload, result):
    return {"jsonrpc": "2.0", "id": payload.get("id"), "result": result}


def _rpc_error(payload, code, message):
    return {"jsonrpc": "2.0", "id": payload.get("id"), "error": {"code": code, "message": message}}


def _handle(payload: dict):
    method = payload.get("method")
    params = payload.get("params") or {}
    if method == "initialize":
        requested = params.get("protocolVersion")
        return _rpc_result(
            payload,
            {
                "protocolVersion": requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0],
                "serverInfo": {"name": "cascadeur-mcp-plus", "version": VERSION},
                "capabilities": {"tools": {}, "resources": {}},
                "instructions": compat.instructions(
                    "Pose and key with the dedicated tools, not the ui_* tools: set_pose (numbers in cm and degrees), set_key, "
                    "get_pose and list_keys, then render_contact_sheet or render_video to check and output the motion. "
                    "The ui_* tools and screenshot are a last resort. Prefer call_action for elements that have an actionId. "
                    "Check get_log after operations that may fail silently."
                ),
            },
        )
    if method and method.startswith("notifications/"):
        return None
    if method == "ping":
        return _rpc_result(payload, {})
    if method == "tools/list":
        return _rpc_result(
            payload,
            {"tools": [_tool_entry(tool) for tool in TOOLS]},
        )
    if method == "tools/call":
        name = params.get("name")
        # Check the name explicitly: a KeyError raised inside a tool is a tool failure, not an
        # unknown tool, and its traceback must reach the caller.
        if name not in _TOOLS_BY_NAME:
            return _rpc_error(payload, -32602, f"Unknown tool: {name}")
        try:
            return _rpc_result(payload, _call_tool(name, params.get("arguments") or {}))
        except Exception:
            return _rpc_result(
                payload, {"content": [{"type": "text", "text": traceback.format_exc()}], "isError": True}
            )
    if method == "resources/list":
        return _rpc_result(payload, {"resources": [_resource_entry(resource) for resource in RESOURCES]})
    if method == "resources/templates/list":
        return _rpc_result(payload, {"resourceTemplates": []})
    if method == "resources/read":
        uri = params.get("uri")
        if not isinstance(uri, str):
            return _rpc_error(payload, -32602, f"resources/read requires a string uri (got {type(uri).__name__})")
        if uri not in _RESOURCES_BY_URI:
            return _rpc_error(payload, -32002, f"Resource not found: {uri}")
        try:
            return _rpc_result(payload, _read_resource(uri))
        except Exception as e:
            return _rpc_error(payload, -32603, f"Resource {uri} failed: {e}")
    return _rpc_error(payload, -32601, f"Unknown method: {method}")


class _Handler(BaseHTTPRequestHandler):
    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = urlparse(origin).hostname or ""
        return host in ("127.0.0.1", "localhost", "::1")

    def do_GET(self):
        if self.path == "/health":
            self._send(200, health_payload())
            return
        self._send(405, {"error": "Use POST /mcp"})

    def do_DELETE(self):
        self._send(405, {"error": "Sessions are not used"})

    def do_POST(self):
        if not self._origin_allowed():
            self._send(403, {"error": "Origin not allowed"})
            return
        if urlparse(self.path).path != "/mcp":
            self._send(404, {"error": "Use POST /mcp"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._send(400, _rpc_error({}, -32700, "Parse error"))
            return
        if isinstance(payload, list):
            responses = [r for r in (_handle(p) for p in payload) if r is not None]
            self._send(200, responses) if responses else self._send(202, None)
            return
        response = _handle(payload)
        self._send(202, None) if response is None else self._send(200, response)

    def log_message(self, format, *args):
        return

    def _send(self, status: int, payload):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            if payload is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError):
            pass


def _allow_reuse_address(platform: str | None = None) -> bool:
    """SO_REUSEADDR everywhere except Windows, where it lets a second process bind a listening port.

    On POSIX the option is what lets a restart rebind 48766 immediately while old connections
    are in TIME_WAIT; disabling it there would move every quick restart to the next port.
    """
    return (platform or sys.platform) != "win32"


class _Server(ThreadingHTTPServer):
    """HTTP server that never shares its port on Windows and rebinds promptly elsewhere.

    http.server's ThreadingHTTPServer sets SO_REUSEADDR. On Windows that lets a second process
    bind a port that is already listening: the second Cascadeur would silently share 48766
    instead of moving to the next free port, and requests would reach an arbitrary instance.
    On macOS/Linux the same option is what makes an immediate restart bind 48766 again instead
    of skipping to 48767 because the old connections are in TIME_WAIT.
    """

    allow_reuse_address = _allow_reuse_address()


def _bind(port: int, count: int) -> tuple[int, _Server]:
    """Bind the first free port at or after port; raise when the whole range is taken."""
    errors = []
    for candidate in range(port, port + count):
        try:
            return candidate, _Server((HOST, candidate), _Handler)
        except OSError as e:
            errors.append(f"{candidate}: {e}")
    raise RuntimeError(f"no free port for MCP Plus in {port}-{port + count - 1} ({'; '.join(errors)})")


# ---------------------------------------------------------------------------
# Lifecycle (call start/stop on the GUI thread, e.g. from the official MCP's run_script)
# ---------------------------------------------------------------------------


def _state():
    return dispatch.state()


def status() -> dict:
    state = _state()
    if not state:
        return {"running": False}
    return {"running": True, "url": f"http://{HOST}:{state['port']}/mcp", "version": state["version"]}


def _write_tool_manifest() -> None:
    """Cache the tool list next to the package so the stdio proxy can list tools while Cascadeur is closed."""
    import os

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools_manifest.json")
    tools = [_tool_entry(tool) for tool in TOOLS]
    try:
        with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as handle:
            json.dump(tools, handle, ensure_ascii=False, indent=1)
        os.replace(path + ".tmp", path)
    except OSError as e:
        print(f"[Cascadeur MCP Plus] could not write tool manifest: {e}")


def _write_resource_manifest() -> None:
    """Cache the resource list next to the package so the stdio proxy can list resources while Cascadeur is closed."""
    import os

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources_manifest.json")
    resources = [_resource_entry(resource) for resource in RESOURCES]
    try:
        with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as handle:
            json.dump(resources, handle, ensure_ascii=False, indent=1)
        os.replace(path + ".tmp", path)
    except OSError as e:
        print(f"[Cascadeur MCP Plus] could not write resource manifest: {e}")


def start(port: int = DEFAULT_PORT) -> dict:
    app = QtCore.QCoreApplication.instance()
    if app is None or QtCore.QThread.currentThread() is not app.thread():
        raise RuntimeError("start() must be called on Cascadeur's GUI thread")
    global _cascadeur_version
    if _state():
        stop()
    requested = int(port)
    refresh_metadata(force=True)
    _cascadeur_version = _application_version()
    # Audit every tool's requires once, on the GUI thread the QML tree and the scene need (T54).
    # A broken audit must not keep the server from starting: get_status reports the failure.
    try:
        compat.refresh(TOOLS, version=_cascadeur_version)
    except Exception as exc:
        print(f"[Cascadeur MCP Plus] compatibility audit failed: {type(exc).__name__}: {exc}")
    from events import scene_idle_manager

    # Bind before anything else: a second Cascadeur finds 48766 taken and must move on to the
    # next free port instead of failing (autostart swallows that failure) or sharing the port.
    chosen, httpd = _bind(requested, INSTANCE_PORT_COUNT)
    if chosen != requested:
        print(f"[Cascadeur MCP Plus] port {requested} is in use; listening on {chosen} instead")
    jobs: queue.Queue = queue.Queue()
    idle_jobs: queue.Queue = queue.Queue()
    dispatcher = dispatch.Dispatcher(jobs)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, name="Cascadeur MCP Plus", daemon=True)
    thread.start()
    idle_subscription = scene_idle_manager.subscribe(_on_idle)
    setattr(
        sys,
        _STATE_KEY,
        {
            "httpd": httpd,
            "thread": thread,
            "dispatcher": dispatcher,
            "jobs": jobs,
            "idle_jobs": idle_jobs,
            "idle_subscription": idle_subscription,
            "port": chosen,
            "version": VERSION,
        },
    )
    _write_tool_manifest()
    _write_resource_manifest()
    print(f"[Cascadeur MCP Plus] {VERSION} listening at http://{HOST}:{chosen}/mcp")
    return status()


def stop() -> dict:
    state = _state()
    if not state:
        return {"running": False}
    state["dispatcher"].stop()
    try:
        # Running jobs lose their poller when the modules are replaced; report them as lost.
        jobs.shutdown()
    except Exception:
        pass
    if state.get("idle_subscription", -1) >= 0:
        from events import scene_idle_manager

        try:
            scene_idle_manager.unsubscribe(state["idle_subscription"])
        except KeyError:
            pass
    state["httpd"].shutdown()
    state["httpd"].server_close()
    delattr(sys, _STATE_KEY)
    print("[Cascadeur MCP Plus] stopped")
    return {"running": False}
