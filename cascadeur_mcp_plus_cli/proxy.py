"""stdio MCP proxy for Cascadeur MCP Plus.

Claude Code launches this with a normal Python (stdlib only), so the connection always
succeeds even when Cascadeur is closed. Tool calls are forwarded to the in-app server on
port 48766; if it is not running, the proxy starts it through the official Cascadeur script
server (port 8765, POST /run) and then forwards the call.

The installed "cascadeur-mcp-plus serve" command runs this module; a source checkout runs the
root-level proxy.py, which loads this file by path. ROOT is the directory that holds
start_mcp_plus.py and cascadeur_mcp_plus/: the repository root in a checkout, or the installed
cascadeur_mcp_plus_runtime/ directory. It is derived from __file__, never hardcoded.
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


def _runtime_dir(package_dir: str) -> str:
    """The directory that holds start_mcp_plus.py and cascadeur_mcp_plus/ (checkout or wheel).

    In a wheel this is <site-packages>/cascadeur_mcp_plus_runtime; Cascadeur only ever adds
    that directory to sys.path, never all of site-packages. In a checkout the same files sit
    at the repository root, one level above this package.
    """
    parent = os.path.dirname(os.path.abspath(package_dir))
    for candidate in (os.path.join(parent, "cascadeur_mcp_plus_runtime"), parent):
        if os.path.isfile(os.path.join(candidate, "start_mcp_plus.py")) and os.path.isfile(
            os.path.join(candidate, "cascadeur_mcp_plus", "__init__.py")
        ):
            return candidate
    return parent


PLUS_URL = os.environ.get("CASCADEUR_PLUS_URL", "http://127.0.0.1:48766")  # keep in sync with version.DEFAULT_PORT
OFFICIAL_URL = os.environ.get("CASCADEUR_OFFICIAL_URL", "http://127.0.0.1:8765")
ROOT = _runtime_dir(os.path.dirname(os.path.abspath(__file__)))
START_SCRIPT = os.path.join(ROOT, "start_mcp_plus.py")
VERSION_FILE = os.path.join(ROOT, "cascadeur_mcp_plus", "version.py")
MANIFEST = os.path.join(ROOT, "tools_manifest.json")
RESOURCE_MANIFEST = os.path.join(ROOT, "resources_manifest.json")
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
NOT_RUNNING = (
    "Cascadeur MCP Plus is not reachable and could not be started: Cascadeur's script server is not running. "
    "Open Cascadeur and click Scripts → MCP → Start script server, then retry."
)

_write_lock = threading.Lock()
_verified_until = 0.0  # monotonic time until which PLUS_URL is known to be MCP Plus
_start_lock = threading.Lock()


def _version() -> str:
    """Read VERSION from the package without importing it (it needs Cascadeur's Python)."""
    scope: dict = {}
    try:
        with open(VERSION_FILE, encoding="utf-8") as handle:
            exec(handle.read(), scope)
        return scope.get("VERSION", "unknown")
    except OSError:
        return "unknown"


def log(message: str) -> None:
    sys.stderr.write(f"[cascadeur-plus proxy] {message}\n")
    sys.stderr.flush()


def send(message: dict) -> None:
    data = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
    with _write_lock:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()


def http_json(url: str, payload=None, timeout: float = 5.0):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="GET" if body is None else "POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8")
        return json.loads(text) if text else None


def identify(base: str, timeout: float = 3.0) -> str | None:
    """Who answers on this port: 'plus', 'official', 'other' (some unrelated program) or None (nothing)."""
    try:
        health = http_json(f"{base}/health", timeout=timeout)
    except urllib.error.HTTPError:
        return "other"  # e.g. another local service answering 401/404
    except Exception as e:
        return None if _refused(e) or isinstance(getattr(e, "reason", e), (TimeoutError, OSError)) else "other"
    if not isinstance(health, dict):
        return "other"
    if health.get("server") == "cascadeur-mcp-plus":
        return "plus"
    if "idle_subscription" in health and "message_subscription" in health:
        return "official"  # signature of Cascadeur's own script server (/health)
    return "other"


def port_taken(base: str, what: str) -> str:
    return (
        f"{base} is used by another program, not {what}, so nothing was sent there. "
        "Free that port (Windows: netstat -ano | findstr <port>) or move the other program, then retry."
    )


def _refused(error: Exception) -> bool:
    reason = getattr(error, "reason", error)
    return isinstance(reason, ConnectionRefusedError) or getattr(reason, "winerror", None) == 10061


def ensure_plus_running() -> tuple[bool, str]:
    """Return (running, note). Starts MCP Plus through the official server when needed.

    Never sends anything to a port unless /health proves it is the expected Cascadeur server.
    """
    who = identify(PLUS_URL)
    if who == "plus":
        return True, ""
    if who is not None:
        return False, port_taken(PLUS_URL, "Cascadeur MCP Plus")
    with _start_lock:
        if identify(PLUS_URL) == "plus":
            return True, ""
        official = identify(OFFICIAL_URL, timeout=5.0)
        if official is None:
            time.sleep(0.5)
            official = identify(OFFICIAL_URL, timeout=5.0)
        if official is None:
            return False, NOT_RUNNING
        if official != "official":
            return False, port_taken(OFFICIAL_URL, "Cascadeur's script server") + (
                " Cascadeur cannot start its script server while that port is taken."
            )
        log("MCP Plus is down; starting it through the official script server")
        port = urllib.parse.urlparse(PLUS_URL).port
        code = (
            f'exec(open(r"{START_SCRIPT}", encoding="utf-8").read(), '
            f'{{"__file__": r"{START_SCRIPT}", "MCP_PLUS_PORT": {port}}})'
        )
        try:
            result = http_json(f"{OFFICIAL_URL}/run", {"code": code}, timeout=40.0)
        except Exception as e:
            return False, f"Starting MCP Plus through Cascadeur's script server failed: {e}"
        if not result or not result.get("ok"):
            return False, f"Cascadeur rejected the MCP Plus start script: {json.dumps(result)[:1500]}"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if identify(PLUS_URL) == "plus":
                return True, "started MCP Plus automatically"
            time.sleep(0.3)
        return False, "MCP Plus start script ran but the server did not come up; check Cascadeur's log."


def load_manifest() -> list:
    try:
        with open(MANIFEST, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return []


def save_manifest(tools: list) -> None:
    try:
        with open(MANIFEST + ".tmp", "w", encoding="utf-8", newline="\n") as handle:
            json.dump(tools, handle, ensure_ascii=False, indent=1)
        os.replace(MANIFEST + ".tmp", MANIFEST)
    except Exception as e:
        log(f"could not save tool manifest: {e}")


def load_resource_manifest() -> list:
    try:
        with open(RESOURCE_MANIFEST, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return []


def save_resource_manifest(resources: list) -> None:
    try:
        with open(RESOURCE_MANIFEST + ".tmp", "w", encoding="utf-8", newline="\n") as handle:
            json.dump(resources, handle, ensure_ascii=False, indent=1)
        os.replace(RESOURCE_MANIFEST + ".tmp", RESOURCE_MANIFEST)
    except Exception as e:
        log(f"could not save resource manifest: {e}")


def forward(message: dict, timeout: float):
    return http_json(f"{PLUS_URL}/mcp", message, timeout=timeout)


def tool_error(message: dict, text: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": message.get("id"),
        "result": {"content": [{"type": "text", "text": text}], "isError": True},
    }


def rpc_error(message: dict, text: str, code: int = -32000) -> dict:
    return {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": code, "message": text}}


MAX_TOOL_TIMEOUT = 600.0
DEFAULT_TOOL_TIMEOUT = 210.0  # covers render_image, which can wait 180 s inside the server
TOOL_TIMEOUT_GRACE = 30.0


def tool_timeout(arguments: dict) -> float:
    """How long the proxy waits for a tools/call response.

    A tool that takes `timeout` does its own waiting (capped at 600 s), so wait that long
    plus a grace period for the response to travel back. Tools without a numeric timeout get
    210 s, which covers render_image's internal 180 s wait. Timing out before the server
    finishes would make an agent retry a slow import or export and run it twice.
    """
    value = arguments.get("timeout")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return DEFAULT_TOOL_TIMEOUT
    if isinstance(value, float) and not math.isfinite(value):
        return DEFAULT_TOOL_TIMEOUT
    # Compare before converting: a huge JSON integer would overflow float().
    capped = min(value, MAX_TOOL_TIMEOUT)
    return float(max(0.0, capped)) + TOOL_TIMEOUT_GRACE


def handle(message: dict):
    method = message.get("method")
    params = message.get("params") or {}
    msg_id = message.get("id")

    if method == "initialize":
        requested = params.get("protocolVersion")
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": requested if requested in PROTOCOLS else PROTOCOLS[0],
                "serverInfo": {"name": "cascadeur-mcp-plus-proxy", "version": _version()},
                "capabilities": {"tools": {}, "resources": {}},
                "instructions": "Operate Cascadeur: inspect the UI with ui_tree or screenshot before clicking, prefer call_action for "
                "elements with an actionId, and check get_log after operations that may fail silently. "
                "The in-app server starts automatically when Cascadeur's script server is running.",
            },
        }
    if method and method.startswith("notifications/"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        tools = None
        if identify(PLUS_URL) == "plus":
            try:
                tools = forward(message, 10.0)["result"]["tools"]
                save_manifest(tools)
            except Exception as e:
                log(f"live tools/list failed: {e}")
        if tools is None:
            tools = load_manifest()
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}}

    if method == "resources/list":
        resources = None
        if identify(PLUS_URL) == "plus":
            try:
                resources = forward(message, 10.0)["result"]["resources"]
                save_resource_manifest(resources)
            except Exception as e:
                log(f"live resources/list failed: {e}")
        if resources is None:
            resources = load_resource_manifest()
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": resources}}

    if method == "resources/templates/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resourceTemplates": []}}

    if method == "resources/read":
        # Reading scene state goes to the app server, so verify who owns the port first and start
        # MCP Plus through the official script server when Cascadeur is open but the server is not.
        running, note = ensure_plus_running()
        if not running:
            return rpc_error(message, note)
        try:
            # The server's own GUI wait is 30 s; 40 s lets its error message arrive first.
            return forward(message, 40.0)
        except (TimeoutError, urllib.error.URLError) as e:
            if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
                return rpc_error(message, "No response from Cascadeur within 40s; it may be busy or frozen.")
            return rpc_error(
                message, f"Lost connection to Cascadeur MCP Plus ({e}). Cascadeur may have closed or crashed."
            )

    if method == "tools/call":
        arguments = params.get("arguments") or {}
        timeout = tool_timeout(arguments)
        # Verify who is listening first (cached for a minute): sending tool calls, which can carry
        # Python code, to whatever program happens to own the port must never happen.
        global _verified_until
        note = ""
        if time.monotonic() >= _verified_until:
            running, note = ensure_plus_running()
            if not running:
                return tool_error(message, note)
            _verified_until = time.monotonic() + 60
        try:
            try:
                response = forward(message, timeout)
            except urllib.error.URLError as e:
                if not _refused(e):
                    raise
                # MCP Plus went away (e.g. Cascadeur restarted): verify/start it, then retry once.
                _verified_until = 0.0
                running, note = ensure_plus_running()
                if not running:
                    return tool_error(message, note)
                _verified_until = time.monotonic() + 60
                response = forward(message, timeout)
        except (TimeoutError, urllib.error.URLError) as e:
            if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
                return tool_error(
                    message, f"No response from Cascadeur within {timeout:.0f}s; it may be busy or frozen."
                )
            return tool_error(
                message, f"Lost connection to Cascadeur MCP Plus ({e}). Cascadeur may have closed or crashed."
            )
        if note and isinstance(response, dict) and isinstance(response.get("result"), dict):
            response["result"].setdefault("content", []).append({"type": "text", "text": f"(proxy: {note})"})
        return response

    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}


def worker(message: dict) -> None:
    try:
        response = handle(message)
    except Exception as e:
        log(f"error handling {message.get('method')}: {e!r}")
        response = (
            None
            if message.get("id") is None
            else {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32603, "message": str(e)}}
        )
    if response is not None:
        send(response)


def main() -> None:
    log(f"started (plus={PLUS_URL}, official={OFFICIAL_URL})")
    for raw in sys.stdin.buffer:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line.decode("utf-8"))
        except Exception:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        # One thread per request so a slow tool call never blocks pings or other calls.
        threading.Thread(target=worker, args=(message,), daemon=True).start()


if __name__ == "__main__":
    main()
