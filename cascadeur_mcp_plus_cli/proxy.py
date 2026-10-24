"""stdio MCP proxy for Cascadeur MCP Plus.

Claude Code launches this with a normal Python (stdlib only), so the connection always
succeeds even when Cascadeur is closed. Tool calls are forwarded to the in-app server on
port 48766; if it is not running, the proxy starts it through the official Cascadeur script
server (port 8765, POST /run) and then forwards the call.

Several Cascadeur windows can be open at once (T53). Each in-app server takes the first free
port from 48766 to 48775, and this proxy lists them with list_instances. set_active_instance
changes where later calls go; an optional instance argument on any tool call (a port, a pid or
a scene name) routes only that call. With one Cascadeur everything behaves as before.

The installed "cascadeur-mcp-plus serve" command runs this module; a source checkout runs the
root-level proxy.py, which loads this file by path. ROOT is the directory that holds
start_mcp_plus.py and cascadeur_mcp_plus/: the repository root in a checkout, or the installed
cascadeur_mcp_plus_runtime/ directory. It is derived from __file__, never hardcoded.
"""

from __future__ import annotations

import ast
import concurrent.futures
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_PORT = 48766  # keep in sync with version.DEFAULT_PORT in the in-app package
INSTANCE_PORT_COUNT = 10  # ports 48766-48775; keep in sync with cascadeur_mcp_plus/server.py
PROBE_TIMEOUT = 0.5  # seconds for one /health probe in a scan; a local MCP Plus answers at once
DISCOVERY_TTL = 5.0  # seconds a discovery scan is reused before probing again
OWNER_CHECK_TIMEOUT = 3.0  # seconds to confirm another live MCP Plus before a start is refused (identify's budget)


def _port_of(url: str | None) -> int:
    return urllib.parse.urlparse(url or "").port or DEFAULT_PORT


def _base_key(url: str | None) -> tuple[str, str, int]:
    """Scheme, host and port of a URL, so a configured host (e.g. a WSL address) compares faithfully."""
    parts = urllib.parse.urlsplit(url or "")
    return (parts.scheme or "http", (parts.hostname or "127.0.0.1").lower(), parts.port or DEFAULT_PORT)


def _base_url(port: int) -> str:
    """URL for one MCP Plus port on the host configured in CASCADEUR_PLUS_URL."""
    scheme, host, _ = _base_key(PLUS_URL)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"


def _monotonic() -> float:
    """Clock for the discovery cache; tests replace it to advance time without sleeping."""
    return time.monotonic()


PLUS_URL = os.environ.get("CASCADEUR_PLUS_URL", f"http://127.0.0.1:{DEFAULT_PORT}")
OFFICIAL_URL = os.environ.get("CASCADEUR_OFFICIAL_URL", "http://127.0.0.1:8765")


def _runtime_dir(package_dir) -> Path:
    """The directory that holds start_mcp_plus.py and cascadeur_mcp_plus/ (checkout or wheel).

    In a wheel this is <site-packages>/cascadeur_mcp_plus_runtime; Cascadeur only ever adds
    that directory to sys.path, never all of site-packages. In a checkout the same files sit
    at the repository root, one level above this package. Path objects are used throughout so
    the same code runs on Windows, macOS and Linux (T55).
    """
    parent = Path(package_dir).resolve().parent
    for candidate in (parent / "cascadeur_mcp_plus_runtime", parent):
        if (candidate / "start_mcp_plus.py").is_file() and (candidate / "cascadeur_mcp_plus" / "__init__.py").is_file():
            return candidate
    return parent


ROOT = _runtime_dir(Path(__file__).resolve().parent)
START_SCRIPT = ROOT / "start_mcp_plus.py"
VERSION_FILE = ROOT / "cascadeur_mcp_plus" / "version.py"
MANIFEST = ROOT / "tools_manifest.json"
RESOURCE_MANIFEST = ROOT / "resources_manifest.json"
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
NOT_RUNNING = (
    "Cascadeur MCP Plus is not reachable and could not be started: Cascadeur's script server is not running. "
    "Open Cascadeur and click Scripts → MCP → Start script server, then retry."
)

_write_lock = threading.Lock()
# Monotonic time until which the default instance (PLUS_URL) is known to be MCP Plus. Other
# instances are verified on every call: their port can belong to a different program.
_verified_until = 0.0
_start_lock = threading.Lock()
_active_lock = threading.Lock()
# "pinned": the active instance was named by pid, scene or a non-default port, so its pid is its identity and a
# different pid on that port is refused. The default instance is unpinned: it means whatever answers on the
# default port, and a restarted Cascadeur there keeps working without re-selecting it.
_active = {"url": PLUS_URL, "port": _port_of(PLUS_URL), "pid": None, "scene": None, "pinned": False}


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
        "Free that port (Windows: netstat -ano | findstr <port>; macOS/Linux: lsof -i :<port>) or move the "
        "other program, then retry."
    )


def _refused(error: Exception) -> bool:
    reason = getattr(error, "reason", error)
    return isinstance(reason, ConnectionRefusedError) or getattr(reason, "winerror", None) == 10061


# ---------------------------------------------------------------------------
# Instances (T53)
# ---------------------------------------------------------------------------


def active_instance() -> dict:
    """Where calls without an instance argument go (the proxy's default until set_active_instance)."""
    with _active_lock:
        return dict(_active)


def active_url() -> str:
    with _active_lock:
        return _active["url"]


def _activate(entry: dict, pinned: bool) -> None:
    with _active_lock:
        _active.update(
            {
                "url": entry["url"],
                "port": entry["port"],
                "pid": entry.get("pid"),
                "scene": entry.get("scene"),
                "pinned": pinned,
            }
        )


def _is_default(base: str | None) -> bool:
    """Whether base is the proxy's own default instance, the only one the official server may start."""
    return _base_key(base) == _base_key(PLUS_URL)


def _active_summary(entry: dict) -> dict:
    return {
        "port": entry["port"],
        "url": entry["url"],
        "pid": entry.get("pid"),
        "scene": entry.get("scene"),
        "pinned": bool(entry.get("pinned")),
    }


def plus_health(base: str, timeout: float = 1.5) -> dict | None:
    """MCP Plus's /health payload from base, or None when nothing (or another program) answers."""
    try:
        health = http_json(f"{base}/health", timeout=timeout)
    except Exception:
        return None
    if isinstance(health, dict) and health.get("server") == "cascadeur-mcp-plus":
        return health
    return None


def candidate_ports() -> list[int]:
    """Ports list_instances scans: the proxy's own default first, then the in-app range 48766-48775."""
    ports = [_port_of(PLUS_URL)]
    for port in range(DEFAULT_PORT, DEFAULT_PORT + INSTANCE_PORT_COUNT):
        if port not in ports:
            ports.append(port)
    return ports


def _instance_entry(port: int, health: dict) -> dict:
    scene = health.get("scene")
    url = _base_url(port)
    return {
        "port": port,
        "url": url,
        "pid": health.get("pid"),
        "scene": scene if isinstance(scene, dict) else None,
        "cascadeur_version": health.get("cascadeur_version"),
        "version": health.get("version"),
        "queued": health.get("queued"),
        "default": _base_key(url) == _base_key(PLUS_URL),
        "active": _base_key(url) == _base_key(active_url()),
    }


def _default_entry() -> dict:
    """The default instance even while it is down: the proxy can start it through the official server."""
    port = _port_of(PLUS_URL)
    return {
        "port": port,
        "url": PLUS_URL,
        "pid": None,
        "scene": None,
        "cascadeur_version": None,
        "version": None,
        "queued": None,
        "default": True,
        "active": _base_key(PLUS_URL) == _base_key(active_url()),
    }


_discovery_lock = threading.Lock()
_discovery_cache: dict = {"instances": None, "expires": 0.0}


def invalidate_instances() -> None:
    """Forget the discovery scan now: an instance went away, restarted, or changed owner."""
    with _discovery_lock:
        _discovery_cache["instances"] = None
        _discovery_cache["expires"] = 0.0


def _refresh_flags(entry: dict) -> dict:
    """Recompute default/active at read time: the active instance can change while the scan is cached."""
    entry = dict(entry)
    entry["default"] = _base_key(entry["url"]) == _base_key(PLUS_URL)
    entry["active"] = _base_key(entry["url"]) == _base_key(active_url())
    return entry


def _cached_instances() -> list[dict] | None:
    with _discovery_lock:
        cached = _discovery_cache["instances"]
        if cached is None or _monotonic() >= _discovery_cache["expires"]:
            return None
        snapshot = [dict(entry) for entry in cached]
    return [_refresh_flags(entry) for entry in snapshot]


def _store_instances(instances: list[dict]) -> None:
    with _discovery_lock:
        _discovery_cache["instances"] = [dict(entry) for entry in instances]
        _discovery_cache["expires"] = _monotonic() + DISCOVERY_TTL


def _probe_port(port: int, timeout: float) -> tuple[int, dict | None]:
    return port, plus_health(_base_url(port), timeout=timeout)


def probe_instances(timeout: float = PROBE_TIMEOUT) -> list[dict]:
    """Probe every scan port at the same time; ten closed ports must not cost ten timeouts."""
    ports = candidate_ports()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(ports))) as pool:
        results = list(pool.map(lambda port: _probe_port(port, timeout), ports))
    instances = [_instance_entry(port, health) for port, health in results if health is not None]
    return sorted(instances, key=lambda entry: entry["port"])


def discover_instances(timeout: float = PROBE_TIMEOUT, force: bool = False) -> list[dict]:
    """Every MCP Plus answering on the scan ports, in port order, reused for DISCOVERY_TTL seconds."""
    if not force:
        cached = _cached_instances()
        if cached is not None:
            return cached
    instances = probe_instances(timeout)
    _store_instances(instances)
    return [_refresh_flags(entry) for entry in instances]


def _describe_instances(instances: list[dict]) -> str:
    if not instances:
        return f"none on ports {DEFAULT_PORT}-{DEFAULT_PORT + INSTANCE_PORT_COUNT - 1}"
    parts = []
    for entry in instances:
        scene = entry.get("scene") or {}
        pid = entry.get("pid")
        parts.append(
            f"port {entry['port']} (pid {pid if pid is not None else '?'}, scene {scene.get('name') or 'untitled'!r})"
        )
    return "; ".join(parts)


def _manual_start_hint() -> str:
    snippet = f'exec(open(r"{START_SCRIPT}", encoding="utf-8").read(), {{"__file__": r"{START_SCRIPT}"}})'
    return (
        f"Start MCP Plus in that Cascadeur's Python Console with: {snippet} "
        f"(it listens on the first free port from {DEFAULT_PORT}), or enable autostart "
        "(cascadeur-mcp-plus autostart enable) so every Cascadeur starts it by itself."
    )


def _other_instance_down(base: str) -> str:
    return (
        f"MCP Plus is not running at {base}, so this call was not sent. A second Cascadeur cannot use the "
        "official script server (port 8765 belongs to the first one), so the proxy does not start MCP Plus for it. "
        + _manual_start_hint()
    )


class InstanceError(Exception):
    """The instance argument names no reachable MCP Plus instance."""


class NoMatchError(InstanceError):
    """Nothing in the scan matches; a fresh scan may still find it (the cache can predate a Cascadeur that started)."""


def _no_match(instances: list[dict], spec) -> InstanceError:
    return NoMatchError(
        f"No MCP Plus instance matches {spec!r}. Running: {_describe_instances(instances)}. {_manual_start_hint()}"
    )


def _as_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _scene_strings(entry: dict) -> list[str]:
    scene = entry.get("scene") if isinstance(entry.get("scene"), dict) else {}
    values = [str(value).strip() for value in (scene.get("name"), scene.get("path")) if value]
    for value in list(values):
        normalized = value.replace("\\", "/")
        if "/" in normalized:
            values.append(normalized.rsplit("/", 1)[-1])
    return [value for value in values if value]


def _unique(entries: list[dict]) -> list[dict]:
    result = []
    for entry in entries:
        if entry not in result:
            result.append(entry)
    return result


def _match_number(instances: list[dict], value: int, spec) -> dict:
    for entry in instances:
        if entry["port"] == value:
            return entry
    for entry in instances:
        if entry["pid"] == value:
            return entry
    raise _no_match(instances, spec)


def match_instance(instances: list[dict], spec) -> dict:
    """Pick one instance by port, pid or scene name; an ambiguous name is an error, never a guess."""
    if isinstance(spec, bool) or not isinstance(spec, (int, str)):
        raise InstanceError(
            f"instance must be a port number, a pid, or a scene name (got {type(spec).__name__}). "
            f"Running: {_describe_instances(instances)}."
        )
    if isinstance(spec, int):
        return _match_number(instances, spec, spec)
    text = str(spec).strip()
    lowered = text.lower()
    if not text:
        raise InstanceError(f"instance must not be empty. Running: {_describe_instances(instances)}.")
    for prefix, key in (("port:", "port"), ("pid:", "pid")):
        if lowered.startswith(prefix):
            number = _as_int(text[len(prefix) :])
            matches = [entry for entry in instances if number is not None and entry[key] == number]
            if len(matches) == 1:
                return matches[0]
            raise _no_match(instances, spec)
    if text.isdigit():
        return _match_number(instances, int(text), spec)
    exact = []
    partial = []
    for entry in instances:
        for value in _scene_strings(entry):
            if value.lower() == lowered:
                exact.append(entry)
            elif lowered in value.lower():
                partial.append(entry)
    matches = _unique(exact) or _unique(partial)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise _no_match(instances, spec)
    raise InstanceError(
        f"{spec!r} matches {len(matches)} instances: {_describe_instances(matches)}. Use a port or a pid instead."
    )


def _names_default(spec) -> bool:
    """Whether spec asks for the proxy's default instance (48766), even while that one is down."""
    if isinstance(spec, bool):
        return False
    if isinstance(spec, int):
        return spec == _port_of(PLUS_URL)
    if isinstance(spec, str):
        text = spec.strip().lower()
        if text in ("default", "first"):
            return True
        if text.startswith("port:"):
            text = text[5:].strip()
        return text.isdigit() and int(text) == _port_of(PLUS_URL)
    return False


def _match_fresh(spec) -> dict:
    """match_instance on the cached scan; a miss scans once more before it is reported as no match."""
    try:
        return match_instance(discover_instances(), spec)
    except NoMatchError:
        return match_instance(discover_instances(force=True), spec)


def _pinned(spec) -> bool:
    """Whether a per-call instance argument names one Cascadeur window (a pid, a scene, or a non-default port)."""
    return spec is not None and not _names_default(spec)


def resolve_instance(spec) -> dict:
    """The instance a per-call instance argument names; it never falls back to another instance."""
    try:
        return _match_fresh(spec)
    except InstanceError:
        if _names_default(spec):
            return _default_entry()
        raise


INSTANCE_PARAM = {
    "type": ["integer", "string"],
    "description": "Route only this call to one Cascadeur instance: its port (48766-48775), its pid, or part of its "
    "scene name/path. Use list_instances to see them; omit it to use the active instance.",
}

INSTANCE_TOOLS = [
    {
        "name": "list_instances",
        "description": "List the Cascadeur instances whose MCP Plus answers on ports 48766-48775: port, pid, scene "
        "name/path, Cascadeur version and MCP Plus version, marking the default and the active one. Use it when more "
        "than one Cascadeur is open, before set_active_instance or before passing instance= to another tool.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_active_instance",
        "description": "Choose which Cascadeur later tool calls go to, by port, pid or scene name, and remember it for "
        "this proxy process. A choice made this way is held: if that Cascadeur closes and another one takes its port, "
        "calls are refused until you choose again. instance=default (port 48766) is not held; it follows whatever "
        "MCP Plus runs there. Returns the active instance. Pass instance= to one tool call instead to route only "
        "that call.",
        "inputSchema": {"type": "object", "properties": {"instance": INSTANCE_PARAM}, "required": ["instance"]},
    },
]

_LOCAL_TOOL_NAMES = frozenset(tool["name"] for tool in INSTANCE_TOOLS)


def list_instances() -> dict:
    """Result of the list_instances tool: every answering instance plus the active one."""
    instances = discover_instances()
    result = {
        "ok": True,
        "count": len(instances),
        "instances": instances,
        "active": _active_summary(active_instance()),
        "scan": {"first_port": DEFAULT_PORT, "last_port": DEFAULT_PORT + INSTANCE_PORT_COUNT - 1},
    }
    if not instances:
        result["hint"] = "No Cascadeur is answering. Open Cascadeur; " + _manual_start_hint()
    elif len(instances) == 1:
        result["hint"] = "One instance: calls without instance go to the active instance, as before."
    else:
        result["hint"] = (
            "Pass instance=<port|pid|scene> to one call, or call set_active_instance to change where later calls go."
        )
    return result


def set_active_instance(arguments: dict) -> dict:
    """Result of the set_active_instance tool: switch the active instance, or report why it did not."""
    spec = arguments.get("instance") if isinstance(arguments, dict) else None
    if spec is None:
        return {
            "ok": False,
            "error": "set_active_instance needs instance: a port (48766-48775), a pid, or a scene name.",
            "hint": "Call list_instances to see the running instances.",
        }
    if _names_default(spec):
        entry = _default_entry()
        health = plus_health(PLUS_URL)
        if health is not None:
            entry = _instance_entry(entry["port"], health)
        _activate(entry, pinned=False)
        return {
            "ok": True,
            "active": _active_summary(dict(entry, pinned=False)),
            "note": f"Calls without instance now go to {entry['url']} (the default instance).",
        }
    try:
        entry = _match_fresh(spec)
    except InstanceError as e:
        if not discover_instances():
            return {
                "ok": False,
                "error": "No Cascadeur MCP Plus instance is reachable.",
                "hint": _manual_start_hint(),
                "instances": [],
            }
        return {"ok": False, "error": str(e), "hint": "Call list_instances to see the running instances."}
    _activate(entry, pinned=True)
    return {
        "ok": True,
        "active": _active_summary(dict(entry, pinned=True)),
        "note": f"Calls without instance now go to port {entry['port']}; a later Cascadeur on that port is refused.",
    }


def run_local_tool(name: str, arguments: dict) -> dict:
    if name == "list_instances":
        return list_instances()
    return set_active_instance(arguments)


def local_response(message: dict, value: dict) -> dict:
    """A tools/call response for a tool the proxy answers itself (same shape as the in-app server)."""
    return {
        "jsonrpc": "2.0",
        "id": message.get("id"),
        "result": {
            "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=1)}],
            "isError": not (isinstance(value, dict) and value.get("ok") is True),
        },
    }


def _with_instance(schema):
    """The tool's schema plus the optional instance argument every forwarded tool accepts."""
    schema = dict(schema) if isinstance(schema, dict) else {"type": "object"}
    properties = schema.get("properties")
    properties = dict(properties) if isinstance(properties, dict) else {}
    properties.setdefault("instance", INSTANCE_PARAM)
    schema["properties"] = properties
    return schema


def advertise_tools(tools: list) -> list:
    """The forwarded tool list plus the instance argument and the proxy's own instance tools."""
    result = []
    names = set()
    for tool in tools:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        entry = dict(tool)
        entry["inputSchema"] = _with_instance(entry.get("inputSchema"))
        names.add(entry["name"])
        result.append(entry)
    for tool in INSTANCE_TOOLS:
        if tool["name"] not in names:
            result.append(dict(tool))
    return result


# The official /run returns an expression's repr as "value", so the probe is one expression rather than a print:
# printed lines are not always captured. Keep the attribute name in sync with dispatch.STATE_KEY.
PROBE_CODE = (
    "{'pid': __import__('os').getpid(), "
    "'port': (getattr(__import__('sys'), '_cascadeur_mcp_plus_state', None) or {}).get('port')}"
)
NOTHING_STARTED = (
    "Nothing was started, because restarting MCP Plus there could stop another window's server and lose its "
    "running jobs. Retry when Cascadeur is idle, or start MCP Plus in that Cascadeur yourself."
)


class ProbeError(Exception):
    """The official server did not answer the status probe, so it is unknown where MCP Plus runs."""


def _probe_owner() -> dict | None:
    """Where MCP Plus runs in the Cascadeur behind the official script server: {'pid', 'port'}.

    port is None when that Cascadeur runs no MCP Plus, and the result is None when the reply cannot be read.
    Raises ProbeError when the official server does not answer at all. The official /run queue executes on the
    next scene-idle event like any script, so this costs one idle round.
    """
    try:
        result = http_json(f"{OFFICIAL_URL}/run", {"code": PROBE_CODE}, timeout=40.0)
    except Exception as e:
        raise ProbeError(
            f"Could not ask the Cascadeur at {OFFICIAL_URL} whether MCP Plus is already running ({e}). "
            f"{NOTHING_STARTED}"
        ) from e
    if not isinstance(result, dict) or result.get("ok") is not True:
        detail = result.get("error") if isinstance(result, dict) else result
        raise ProbeError(
            f"The Cascadeur at {OFFICIAL_URL} did not answer the MCP Plus status probe ({detail}). {NOTHING_STARTED}"
        )
    try:
        state = ast.literal_eval(str(result.get("value", "")))
    except Exception:
        return None
    return state if isinstance(state, dict) else None


def _owner_port_elsewhere(owner: dict, port: int) -> int | None:
    """The port where the Cascadeur behind the official server runs a live MCP Plus other than port, else None.

    A recorded port that nothing answers any more is stale (the server stopped without cleaning up), so it does not
    count. The check waits longer than a discovery probe: a busy MCP Plus that is missed here would be restarted.
    """
    current = _as_int(owner.get("port"))
    if current is None or current == port:
        return None
    health = plus_health(_base_url(current), timeout=OWNER_CHECK_TIMEOUT)
    if health is None:
        return None
    owner_pid = owner.get("pid")
    if owner_pid is not None and health.get("pid") not in (None, owner_pid):
        return None
    return current


def _adopt_pid(base: str, pid, pinned: bool) -> None:
    """Record the pid that answers on the active instance's port.

    An unpinned (default) instance follows whatever answers there, so its pid is only informative. A pinned
    instance keeps the pid it was selected with; it learns one on first contact only when the selection had none.
    """
    if pid is None:
        return
    with _active_lock:
        if _base_key(_active["url"]) != _base_key(base):
            return
        if pinned and _active["pid"] is not None:
            return
        _active["pid"] = pid


def _expected_pid(entry: dict, pinned: bool):
    """The pid a call must find on its port: only a pinned instance has one (see _active)."""
    return entry.get("pid") if pinned else None


def _check_instance_identity(base: str, expected_pid) -> tuple[bool, str, dict | None]:
    """Refuse a call when its port now answers with another Cascadeur's pid; returns (ok, error, health).

    A port can change owner: the instance that was selected closes and a new Cascadeur starts MCP Plus on
    that port. expected_pid is None when nothing can be compared (an unpinned instance, or an older MCP Plus
    without a pid), and then the call proceeds; /health still proves the port is MCP Plus.
    """
    health = plus_health(base)
    if expected_pid is None or health is None:
        return True, "", health  # a dead port is reported by ensure_plus_running with its own message
    actual = health.get("pid")
    if actual is not None and actual != expected_pid:
        invalidate_instances()
        return (
            False,
            (
                f"MCP Plus at {base} now belongs to a different Cascadeur (pid {actual}, expected {expected_pid}), "
                "so this call was not sent. The instance you selected has closed, or another Cascadeur took its "
                "port. Call list_instances and set_active_instance again, or pass instance=<port|pid|scene>."
            ),
            health,
        )
    return True, "", health


def ensure_plus_running(base: str | None = None) -> tuple[bool, str]:
    """Return (running, note) for base. Starts the default instance through the official server when needed.

    Never sends anything to a port unless /health proves it is the expected Cascadeur server.
    Only the proxy's own default instance can be started this way: the official script server
    (8765) belongs to one Cascadeur, so starting any other instance through it would run the
    start script in the wrong window. If that Cascadeur already runs MCP Plus on another port,
    nothing is started: restarting it would stop its server and lose running jobs.
    """
    target = base or PLUS_URL
    who = identify(target)
    if who == "plus":
        return True, ""
    if who is not None:
        return False, port_taken(target, "Cascadeur MCP Plus")
    invalidate_instances()
    if not _is_default(target):
        return False, _other_instance_down(target)
    with _start_lock:
        if identify(target) == "plus":
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
        port = _port_of(target)
        try:
            owner = _probe_owner()
        except ProbeError as e:
            return False, str(e)
        if owner is not None:
            elsewhere = _owner_port_elsewhere(owner, port)
            if elsewhere is not None:
                return False, (
                    f"The Cascadeur that owns {OFFICIAL_URL} already runs MCP Plus on port {elsewhere}, so nothing "
                    f"was started on {port}: restarting it would stop that server and mark its running "
                    "render/mocap jobs as lost. Use that instance with list_instances/set_active_instance, or stop it "
                    "first if you really want to move it."
                )
        else:
            # The reply did not say where MCP Plus runs (an official server that does not evaluate the probe).
            # Then start only when no other MCP Plus answers: that one could be the owner's.
            others = [entry["port"] for entry in discover_instances(force=True) if entry["port"] != port]
            if others:
                return False, (
                    f"Another Cascadeur already runs MCP Plus on port {others[0]}, and the Cascadeur at {OFFICIAL_URL} "
                    f"did not say whether it is that one, so nothing was started on {port}: restarting a server that "
                    "belongs to another window would stop it and lose its running jobs. Use that instance with "
                    "list_instances/set_active_instance, or start MCP Plus in the window you mean yourself."
                )
        log("MCP Plus is down; starting it through the official script server")
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
            if identify(target) == "plus":
                return True, "started MCP Plus automatically"
            time.sleep(0.3)
        return False, (
            "MCP Plus start script ran but the server did not come up; check Cascadeur's log. "
            "It may have moved to another port: call list_instances."
        )


def load_manifest() -> list:
    try:
        with open(MANIFEST, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return []


def save_manifest(tools: list) -> None:
    temporary = Path(str(MANIFEST) + ".tmp")
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(tools, handle, ensure_ascii=False, indent=1)
        os.replace(temporary, MANIFEST)
    except Exception as e:
        log(f"could not save tool manifest: {e}")


def load_resource_manifest() -> list:
    try:
        with open(RESOURCE_MANIFEST, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return []


def save_resource_manifest(resources: list) -> None:
    temporary = Path(str(RESOURCE_MANIFEST) + ".tmp")
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(resources, handle, ensure_ascii=False, indent=1)
        os.replace(temporary, RESOURCE_MANIFEST)
    except Exception as e:
        log(f"could not save resource manifest: {e}")


def forward(message: dict, timeout: float, base: str | None = None):
    return http_json(f"{base or PLUS_URL}/mcp", message, timeout=timeout)


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
                "The in-app server starts automatically when Cascadeur's script server is running. "
                "With several Cascadeur windows open, call list_instances and pass instance=<port|pid|scene> to one "
                "call, or call set_active_instance to choose where later calls go.",
            },
        }
    if method and method.startswith("notifications/"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        base = active_url()
        tools = None
        if identify(base) == "plus":
            try:
                tools = forward(message, 10.0, base)["result"]["tools"]
                save_manifest(tools)
            except Exception as e:
                log(f"live tools/list failed: {e}")
        if tools is None:
            tools = load_manifest()
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": advertise_tools(tools)}}

    if method == "resources/list":
        base = active_url()
        resources = None
        if identify(base) == "plus":
            try:
                resources = forward(message, 10.0, base)["result"]["resources"]
                save_resource_manifest(resources)
            except Exception as e:
                log(f"live resources/list failed: {e}")
        if resources is None:
            resources = load_resource_manifest()
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": resources}}

    if method == "resources/templates/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resourceTemplates": []}}

    if method == "resources/read":
        active = active_instance()
        base = active["url"]
        pinned = bool(active["pinned"])
        # Reading scene state goes to the app server, so verify who owns the port first and start
        # MCP Plus through the official script server when Cascadeur is open but the server is not.
        running, note = ensure_plus_running(base)
        if not running:
            return rpc_error(message, note)
        ok, identity_error, health = _check_instance_identity(base, _expected_pid(active, pinned))
        if not ok:
            return rpc_error(message, identity_error)
        if health is not None:
            _adopt_pid(base, health.get("pid"), pinned)
        try:
            # The server's own GUI wait is 30 s; 40 s lets its error message arrive first.
            return forward(message, 40.0, base)
        except (TimeoutError, urllib.error.URLError) as e:
            invalidate_instances()
            if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
                return rpc_error(message, "No response from Cascadeur within 40s; it may be busy or frozen.")
            return rpc_error(
                message, f"Lost connection to Cascadeur MCP Plus ({e}). Cascadeur may have closed or crashed."
            )

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            # Forward a well-formed call even when a client sends arguments of the wrong type.
            arguments = {}
            params["arguments"] = arguments
        if name in _LOCAL_TOOL_NAMES:
            return local_response(message, run_local_tool(name, arguments))
        timeout = tool_timeout(arguments)
        # A per-call instance routes only this call; the active instance stays where it was.
        spec = arguments.pop("instance", None)
        if spec is not None:
            try:
                target = resolve_instance(spec)
            except InstanceError as e:
                return tool_error(message, str(e))
            base = target["url"]
            pinned = _pinned(spec)
        else:
            target = active_instance()
            base = target["url"]
            pinned = bool(target["pinned"])
        expected_pid = _expected_pid(target, pinned)
        # Verify who is listening first (the unpinned default is cached for a minute): sending tool
        # calls, which can carry Python code, to whatever program happens to own the port must never happen.
        global _verified_until
        note = ""
        cached = not pinned and _is_default(base) and time.monotonic() < _verified_until
        if not cached:
            running, note = ensure_plus_running(base)
            if not running:
                return tool_error(message, note)
            if _is_default(base):
                _verified_until = time.monotonic() + 60
        # A port can belong to a newer Cascadeur than the one that was selected; never edit its scene.
        ok, identity_error, health = _check_instance_identity(base, expected_pid)
        if not ok:
            return tool_error(message, identity_error)
        if health is not None and spec is None:
            _adopt_pid(base, health.get("pid"), pinned)
        try:
            try:
                response = forward(message, timeout, base)
            except urllib.error.URLError as e:
                if not _refused(e):
                    raise
                # MCP Plus went away (e.g. Cascadeur restarted): verify/start it, then retry once.
                invalidate_instances()
                if _is_default(base):
                    _verified_until = 0.0
                running, note = ensure_plus_running(base)
                if not running:
                    return tool_error(message, note)
                if _is_default(base):
                    _verified_until = time.monotonic() + 60
                ok, identity_error, _ = _check_instance_identity(base, expected_pid)
                if not ok:
                    return tool_error(message, identity_error)
                response = forward(message, timeout, base)
        except (TimeoutError, urllib.error.URLError) as e:
            invalidate_instances()
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
