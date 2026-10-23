"""HTTP MCP server for Cascadeur MCP Plus.

Requests arrive on a background HTTP thread and are executed on Qt's GUI thread by a
QTimer-driven dispatcher, which keeps running during popups and nested event loops.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from PySide6 import QtCore

from . import scene_tools, ui
from .version import DEFAULT_PORT, VERSION

HOST = "127.0.0.1"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
_STATE_KEY = "_cascadeur_mcp_plus_state"  # survives module reloads


# ---------------------------------------------------------------------------
# GUI-thread dispatcher
# ---------------------------------------------------------------------------


class _Job:
    def __init__(self, fn, args):
        self.fn = fn
        self.args = args
        self.cancelled = False
        self.done = threading.Event()
        self.result = None
        self.error = None

    def run(self):
        if self.cancelled:
            return
        try:
            self.result = self.fn(**self.args)
        except ui.UiError as e:
            self.error = str(e)
        except Exception:
            self.error = traceback.format_exc()
        finally:
            self.done.set()


class _Dispatcher(QtCore.QObject):
    def __init__(self, jobs: queue.Queue):
        super().__init__()
        self.jobs = jobs
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self.drain)
        self.timer.start()

    def drain(self):
        # Re-entrant on purpose: a job that opens a nested event loop (e.g. a modal
        # dialog) must not block the next job, which may be the one closing it.
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                return
            job.run()

    def stop(self):
        self.timer.stop()


def _drain_idle_jobs(scene=None):
    """scene_idle subscriber: runs scene-editing jobs the way the official script server does."""
    state = _state()
    if not state:
        return
    while True:
        try:
            job = state["idle_jobs"].get_nowait()
        except queue.Empty:
            return
        job.run()


def _call_on_gui(fn, args: dict, timeout: float, idle: bool = False):
    state = _state()
    if state is None:
        raise RuntimeError("Server is not running")
    job = _Job(fn, args)
    state["idle_jobs" if idle else "jobs"].put(job)
    if not job.done.wait(timeout):
        job.cancelled = True
        raise TimeoutError(f"Cascadeur did not finish within {timeout:.0f}s (the GUI may be busy)")
    if job.error is not None:
        raise ui.UiError(job.error)
    return job.result


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def _schema(properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties or {}, "required": required or []}


_WINDOW = {
    "type": ["integer", "string"],
    "description": "Window index or title substring (default: focused/main window).",
}
_REF = {"type": "string", "description": "Element ref from the latest ui_tree or dialogs result."}
_QUERY = {"type": "string", "description": "Text, tooltip, actionId or objectName to find an on-screen element."}
_SETTLE = {
    "type": "integer",
    "description": "Milliseconds to wait for the UI to update before returning (default 150).",
}

TOOLS = [
    {
        "name": "run_python",
        "description": "Run Python inside Cascadeur on the GUI thread with csc, app, scene (domain scene), view_scene and ui in scope. "
        "Returns stdout and the value of a lone expression or of a variable named `result`. "
        "scene.modify*/edit return False when their callback failed; check it.",
        "inputSchema": _schema(
            {
                "code": {"type": "string"},
                "timeout": {"type": "number", "description": "Seconds to wait (default 60, max 600)."},
            },
            ["code"],
        ),
        "fn": lambda code, timeout=None: scene_tools.run_python(code, {"ui": ui}),
        "timeout_arg": True,
    },
    {
        "name": "get_scene_info",
        "description": "Summarize the current scene: name/path, frames, object counts by type, selection, rig/animation behaviours, license flags.",
        "inputSchema": _schema({"max_objects": {"type": "integer"}}),
        "fn": scene_tools.scene_info,
    },
    {
        "name": "get_object_info",
        "description": "Describe objects by exact or partial name: type, id and behaviours with their property names.",
        "inputSchema": _schema({"name": {"type": "string"}, "max_properties": {"type": "integer"}}, ["name"]),
        "fn": scene_tools.object_info,
    },
    {
        "name": "screenshot",
        "description": "Capture a Cascadeur window (or one element by ref) as an image. window_units_per_pixel converts image pixels to the window coordinates used by ui_click x/y.",
        "inputSchema": _schema(
            {
                "window": _WINDOW,
                "ref": _REF,
                "max_width": {"type": "integer", "description": "Downscale to this width (default 1280)."},
                "format": {"type": "string", "enum": ["jpeg", "png"]},
                "restore": {
                    "type": "boolean",
                    "description": "Un-minimize the window first (a minimized window renders nothing).",
                },
            }
        ),
        "handler": lambda window=None, ref=None, max_width=1280, format="jpeg", restore=False: _screenshot(
            window, ref, max_width, format, restore
        ),
        "image": True,
    },
    {
        "name": "ui_tree",
        "description": "List open windows and on-screen UI elements (text, tooltip, actionId, type, rect, clickable/input flags) with refs for the other ui_* tools.",
        "inputSchema": _schema(
            {
                "window": _WINDOW,
                "query": {
                    "type": "string",
                    "description": "Only elements whose text/tooltip/actionId/objectName/type contains this.",
                },
                "interactive_only": {"type": "boolean", "description": "Only clickable, input or action elements."},
                "include_hidden": {"type": "boolean"},
                "limit": {"type": "integer", "description": "Max elements (default 150)."},
            }
        ),
        "fn": ui.tree,
    },
    {
        "name": "ui_click",
        "description": "Click like a real user (synthesized mouse events) on an element by ref or query, or at window coordinates x/y. "
        "Supports right/middle button, double click and modifiers.",
        "inputSchema": _schema(
            {
                "ref": _REF,
                "query": _QUERY,
                "window": _WINDOW,
                "x": {"type": "number"},
                "y": {"type": "number"},
                "button": {"type": "string", "enum": ["left", "right", "middle"]},
                "double": {"type": "boolean"},
                "modifiers": {"type": "array", "items": {"type": "string"}, "description": 'e.g. ["ctrl"], ["shift"]'},
                "index": {"type": "integer", "description": "Pick the n-th best match for query (default 0)."},
                "force": {
                    "type": "boolean",
                    "description": "Skip safety guards (e.g. the Rig-mode check before rig generation).",
                },
                "settle_ms": _SETTLE,
            }
        ),
        "fn": ui.click,
        "settle": True,
    },
    {
        "name": "ui_drag",
        "description": "Drag with the mouse like a user: press at the start, move in steps (button held), release at the end. "
        "Start: object (scene object name, projected into the 3D viewport), ref, query or window x/y. "
        "End: to_x/to_y or offset dx/dy in window coordinates. Each step runs separately so Cascadeur updates "
        "between moves (e.g. AutoPosing while dragging a control point). Returns the actual start/end points.",
        "inputSchema": _schema(
            {
                "object": {
                    "type": "string",
                    "description": "Scene object to start from, e.g. 'mixamorig:LeftHand_MainPoint'.",
                },
                "ref": _REF,
                "query": _QUERY,
                "window": _WINDOW,
                "x": {"type": "number"},
                "y": {"type": "number"},
                "to_x": {"type": "number"},
                "to_y": {"type": "number"},
                "dx": {"type": "number"},
                "dy": {"type": "number", "description": "Negative moves up on screen."},
                "button": {"type": "string", "enum": ["left", "right", "middle"]},
                "modifiers": {"type": "array", "items": {"type": "string"}},
                "steps": {"type": "integer", "description": "Intermediate move events (default 20)."},
                "duration_ms": {"type": "integer", "description": "Total time spent moving (default 400)."},
                "hold_ms": {"type": "integer", "description": "Pause after pressing before moving (default 80)."},
                "settle_ms": {
                    "type": "integer",
                    "description": "Milliseconds to wait for the UI to update before returning (default 300).",
                },
                "method": {
                    "type": "string",
                    "enum": ["qtest", "event"],
                    "description": "qtest (default) goes through Qt's window-system input path; event sends QMouseEvents directly.",
                },
                "fov_deg": {
                    "type": "number",
                    "description": "Viewport vertical FOV for object projection (default 30).",
                },
            }
        ),
        "handler": lambda **kwargs: _drag(**kwargs),  # _drag is defined below TOOLS
    },
    {
        "name": "project_point",
        "description": "Project scene objects (by name) to main-window coordinates in the 3D viewport, for ui_click/ui_drag x/y. "
        "Uses the viewport camera and a 30 degree vertical FOV (verified for Cascadeur 2026.2.3).",
        "inputSchema": _schema(
            {
                "names": {"type": "array", "items": {"type": "string"}},
                "fov_deg": {"type": "number"},
            },
            ["names"],
        ),
        "fn": ui.project_points,
    },
    {
        "name": "ui_hover",
        "description": "Move the mouse over an element or point (shows tooltips, hover menus).",
        "inputSchema": _schema(
            {
                "ref": _REF,
                "query": _QUERY,
                "window": _WINDOW,
                "x": {"type": "number"},
                "y": {"type": "number"},
                "settle_ms": _SETTLE,
            }
        ),
        "fn": ui.hover,
        "settle": True,
    },
    {
        "name": "ui_key",
        "description": "Press keys or shortcuts, space-separated combos, e.g. 'ctrl+s', 'Escape', 'ctrl+a Delete', 'F'.",
        "inputSchema": _schema({"keys": {"type": "string"}, "window": _WINDOW, "settle_ms": _SETTLE}, ["keys"]),
        "fn": lambda keys, window=None: ui.keys(keys, window),
        "settle": True,
    },
    {
        "name": "ui_type",
        "description": "Type text into the focused element, or focus an element by ref/query first. replace clears it; submit presses Enter.",
        "inputSchema": _schema(
            {
                "text": {"type": "string"},
                "ref": _REF,
                "query": _QUERY,
                "window": _WINDOW,
                "replace": {"type": "boolean"},
                "submit": {"type": "boolean"},
                "release_focus": {
                    "type": "boolean",
                    "description": "Drop keyboard focus afterwards so shortcuts reach Cascadeur again.",
                },
                "settle_ms": _SETTLE,
            },
            ["text"],
        ),
        "fn": ui.type_text,
        "settle": True,
    },
    {
        "name": "ui_set_property",
        "description": "Set a QML property on an element (e.g. checked, value, text, currentIndex). Bypasses input handlers; prefer ui_click when possible.",
        "inputSchema": _schema({"ref": _REF, "name": {"type": "string"}, "value": {}}, ["ref", "name", "value"]),
        "fn": ui.set_property,
    },
    {
        "name": "ui_invoke",
        "description": "Invoke a no-argument QML method or signal on an element, e.g. 'clicked', 'toggle', 'open', 'close'. Useful when an element cannot be clicked on screen.",
        "inputSchema": _schema({"ref": _REF, "method": {"type": "string"}}, ["ref", "method"]),
        "fn": ui.invoke,
    },
    {
        "name": "dialogs",
        "description": "List visible dialogs/popups and secondary windows with their texts and buttons (as refs).",
        "inputSchema": _schema(),
        "fn": ui.dialogs,
    },
    {
        "name": "list_actions",
        "description": "List Cascadeur action keys (actionId) found in the UI, e.g. 'Commands.Rig mode.On', with labels.",
        "inputSchema": _schema({"query": {"type": "string"}, "include_hidden": {"type": "boolean"}}),
        "fn": ui.list_actions,
    },
    {
        "name": "call_action",
        "description": "Trigger a Cascadeur action by key via ActionManager.call_action, exactly like its toolbar button or menu item.",
        "inputSchema": _schema({"key": {"type": "string"}, "settle_ms": _SETTLE}, ["key"]),
        "fn": ui.call_action,
        "settle": True,
    },
    {
        "name": "enter_rig_mode",
        "description": "Enter Cascadeur's Rig mode using its own rig_mode.on entry points (run_after_import for a freshly imported model). "
        "Required before Quick Rigging's Add rig elements / Generate rig: without it Cascadeur crashes. "
        "Runs on the scene idle event like the official script server.",
        "inputSchema": _schema(
            {
                "color": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Rig color RGB 0..1 (default [0, 0.5, 0]).",
                }
            }
        ),
        "fn": scene_tools.enter_rig_mode,
        "idle": True,
    },
    {
        "name": "get_log",
        "description": "Read the tail of Cascadeur's log file (errors and Python tracebacks land here), optionally filtered.",
        "inputSchema": _schema({"lines": {"type": "integer"}, "contains": {"type": "string"}}),
        "fn": scene_tools.get_log,
        "gui": False,
    },
]
_TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def _screenshot(window, ref, max_width, fmt, restore):
    if restore:
        restored = _call_on_gui(ui.restore_window, {"window": window}, 30.0)
        if restored.get("restored"):
            time.sleep(0.8)  # let the window repaint before capturing
    return _call_on_gui(ui.screenshot, {"window": window, "ref": ref, "max_width": max_width, "fmt": fmt}, 30.0)


def _drag(
    ref=None,
    query=None,
    object=None,
    window=None,
    x=None,
    y=None,
    to_x=None,
    to_y=None,
    dx=None,
    dy=None,
    button="left",
    modifiers=None,
    steps=20,
    duration_ms=400,
    hold_ms=80,
    settle_ms=300,
    index=0,
    method="qtest",
    fov_deg=ui.DEFAULT_VIEWPORT_FOV_DEG,
):
    steps = max(1, min(int(steps), 500))
    begin = _call_on_gui(
        ui.drag_begin,
        {
            "ref": ref,
            "query": query,
            "obj": object,
            "window": window,
            "x": x,
            "y": y,
            "to_x": to_x,
            "to_y": to_y,
            "dx": dx,
            "dy": dy,
            "button": button,
            "modifiers": modifiers,
            "index": index,
            "method": method,
            "fov_deg": fov_deg,
        },
        30.0,
    )
    (sx, sy), (ex, ey) = begin["start"], begin["end"]
    interval = max(0.0, float(duration_ms) / 1000.0 / steps)
    released = None
    try:
        time.sleep(max(0.0, float(hold_ms)) / 1000.0)
        for i in range(1, steps + 1):
            t = i / steps
            _call_on_gui(ui.drag_move, {"x": sx + (ex - sx) * t, "y": sy + (ey - sy) * t}, 30.0)
            time.sleep(interval)
    finally:
        # Always release, even if a move failed, so Cascadeur is not left with a held button.
        released = _call_on_gui(ui.drag_end, {}, 30.0)
    time.sleep(max(0.0, min(float(settle_ms), 5000.0)) / 1000.0)
    result = {
        "window": begin["window"],
        "start": begin["start"],
        "end": released.get("end", begin["end"]),
        "steps": steps,
        "duration_ms": duration_ms,
        "method": method,
        "target": begin["target"],
    }
    # A modal prompt (e.g. AutoPosing's "Adjust AutoPosing lock state?") swallows the rest of the drag.
    dialogs = _call_on_gui(ui.dialogs, {}, 30.0)["dialogs"]
    if dialogs:
        result["dialogs"] = dialogs
        result["warning"] = (
            "A dialog is open; the drag may not have applied. Answer it with ui_click(ref=...) and drag again."
        )
    return result


def _call_tool(name: str, arguments: dict) -> dict:
    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        raise KeyError(name)
    args = dict(arguments or {})
    timeout = 60.0
    if tool.get("timeout_arg"):
        timeout = min(float(args.get("timeout") or 60.0), 600.0)
    settle_ms = int(args.pop("settle_ms", 150) or 0) if tool.get("settle") else 0
    try:
        if "handler" in tool:
            value = tool["handler"](**args)
        elif tool.get("gui", True):
            value = _call_on_gui(tool["fn"], args, timeout, idle=tool.get("idle", False))
        else:
            value = tool["fn"](**args)
        if settle_ms > 0:
            # Wait on the HTTP thread so the GUI event loop runs normally (never nested).
            time.sleep(min(settle_ms, 5000) / 1000.0)
    except (ui.UiError, TimeoutError, TypeError) as e:
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
                "capabilities": {"tools": {}},
                "instructions": "Inspect the UI with ui_tree or screenshot before clicking. Prefer call_action for elements that have an actionId. "
                "Check get_log after operations that may fail silently.",
            },
        )
    if method and method.startswith("notifications/"):
        return None
    if method == "ping":
        return _rpc_result(payload, {})
    if method == "tools/list":
        return _rpc_result(
            payload,
            {
                "tools": [
                    {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]} for t in TOOLS
                ]
            },
        )
    if method == "tools/call":
        try:
            return _rpc_result(payload, _call_tool(params.get("name"), params.get("arguments") or {}))
        except KeyError:
            return _rpc_error(payload, -32602, f"Unknown tool: {params.get('name')}")
        except Exception:
            return _rpc_result(
                payload, {"content": [{"type": "text", "text": traceback.format_exc()}], "isError": True}
            )
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
            state = _state()
            self._send(
                200,
                {
                    "ok": True,
                    "server": "cascadeur-mcp-plus",
                    "version": VERSION,
                    "queued": state["jobs"].qsize() if state else None,
                },
            )
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


# ---------------------------------------------------------------------------
# Lifecycle (call start/stop on the GUI thread, e.g. from the official MCP's run_script)
# ---------------------------------------------------------------------------


def _state():
    return getattr(sys, _STATE_KEY, None)


def status() -> dict:
    state = _state()
    if not state:
        return {"running": False}
    return {"running": True, "url": f"http://{HOST}:{state['port']}/mcp", "version": state["version"]}


def _write_tool_manifest() -> None:
    """Cache the tool list next to the package so the stdio proxy can list tools while Cascadeur is closed."""
    import os

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools_manifest.json")
    tools = [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]} for t in TOOLS]
    try:
        with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as handle:
            json.dump(tools, handle, ensure_ascii=False, indent=1)
        os.replace(path + ".tmp", path)
    except OSError as e:
        print(f"[Cascadeur MCP Plus] could not write tool manifest: {e}")


def start(port: int = DEFAULT_PORT) -> dict:
    app = QtCore.QCoreApplication.instance()
    if app is None or QtCore.QThread.currentThread() is not app.thread():
        raise RuntimeError("start() must be called on Cascadeur's GUI thread")
    if _state():
        stop()
    from events import scene_idle_manager

    jobs: queue.Queue = queue.Queue()
    idle_jobs: queue.Queue = queue.Queue()
    dispatcher = _Dispatcher(jobs)
    httpd = ThreadingHTTPServer((HOST, port), _Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, name="Cascadeur MCP Plus", daemon=True)
    thread.start()
    idle_subscription = scene_idle_manager.subscribe(_drain_idle_jobs)
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
            "port": port,
            "version": VERSION,
        },
    )
    _write_tool_manifest()
    print(f"[Cascadeur MCP Plus] {VERSION} listening at http://{HOST}:{port}/mcp")
    return status()


def stop() -> dict:
    state = _state()
    if not state:
        return {"running": False}
    state["dispatcher"].stop()
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
