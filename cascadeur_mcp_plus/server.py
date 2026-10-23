"""HTTP MCP server for Cascadeur MCP Plus.

Requests arrive on a background HTTP thread and are executed on Qt's GUI thread by the
dispatcher in dispatch.py, which keeps running during popups and nested event loops. The tools
themselves live in cascadeur_mcp_plus/tools/ and are loaded through the registry.
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

from . import dispatch, jobs
from .errors import ToolError
from .tools import load_tools
from .version import DEFAULT_PORT, VERSION

HOST = "127.0.0.1"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
_STATE_KEY = dispatch.STATE_KEY

TOOLS = load_tools()
_TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def _call_tool(name: str, arguments: dict) -> dict:
    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        raise KeyError(name)
    args = dict(arguments or {})
    timeout = 60.0
    if tool.get("timeout_arg"):
        timeout = min(float(args.get("timeout") or 60.0), 600.0)
    settle_ms = int(args.pop("settle_ms", 150) or 0) if tool.get("settle") and not tool.get("background") else 0
    try:
        if tool.get("background"):
            # Start the job on the GUI thread; it returns as soon as Cascadeur's own async work
            # is running, so the MCP call never waits for the result.
            job_id = dispatch.call_on_gui(
                jobs.start_tool_job, {"tool": tool, "args": args}, timeout, idle=tool.get("idle", False)
            )
            value = {"ok": True, "job_id": job_id, "hint": "call get_job"}
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
    dispatcher = dispatch.Dispatcher(jobs)
    httpd = ThreadingHTTPServer((HOST, port), _Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, name="Cascadeur MCP Plus", daemon=True)
    thread.start()
    idle_subscription = scene_idle_manager.subscribe(dispatch.drain_idle_jobs)
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
