"""HTTP client for live tests against a running Cascadeur MCP Plus server.

This module runs in ordinary Python (no Cascadeur imports). It is deliberately
self-contained: it must not import anything from docs/ or from the package that only
works inside Cascadeur.

The two URLs come from the same environment variables proxy.py reads:
CASCADEUR_PLUS_URL (default http://127.0.0.1:48766) and CASCADEUR_OFFICIAL_URL
(default http://127.0.0.1:8765).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

PLUS_URL = os.environ.get("CASCADEUR_PLUS_URL", "http://127.0.0.1:48766")
OFFICIAL_URL = os.environ.get("CASCADEUR_OFFICIAL_URL", "http://127.0.0.1:8765")
DEFAULT_TIMEOUT = 120.0

NOT_RUNNING = (
    f"MCP Plus is not reachable at {PLUS_URL}. Open Cascadeur and click Scripts > MCP > Start script server, "
    "take the live-test lock (python docs/tickets/bin/live.py lock <ticket>), restart MCP Plus from this "
    "checkout, then rerun."
)


class LiveError(RuntimeError):
    """A live call failed: server unreachable, JSON-RPC error, or a tool returned isError."""


def _http(url: str, payload=None, timeout: float = DEFAULT_TIMEOUT):
    """GET (no payload) or POST JSON, returning the decoded JSON response."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="GET" if data is None else "POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1000]
        raise LiveError(f"HTTP {exc.code} from {url}: {body}") from exc
    except (TimeoutError, OSError, urllib.error.URLError) as exc:
        raise LiveError(f"Cannot reach {url}: {exc}") from exc
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LiveError(f"Non-JSON response from {url}: {text[:1000]!r}") from exc


def health(timeout: float = 3.0) -> dict | None:
    """Return MCP Plus's /health payload, or None when nothing valid answers there."""
    try:
        info = _http(f"{PLUS_URL}/health", timeout=timeout)
    except LiveError:
        return None
    if not isinstance(info, dict) or info.get("server") != "cascadeur-mcp-plus":
        return None
    return info


def available(timeout: float = 3.0) -> bool:
    return health(timeout) is not None


def _rpc(method: str, params: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    response = _http(f"{PLUS_URL}/mcp", payload, timeout=timeout)
    if not isinstance(response, dict):
        raise LiveError(f"Empty or invalid MCP response for {method}: {response!r}")
    if "error" in response:
        error = response["error"]
        raise LiveError(f"MCP error for {method}: {error.get('message', error)}")
    return response


def tools(timeout: float = 15.0) -> list[dict]:
    """Return the tool list the live server currently exposes."""
    response = _rpc("tools/list", timeout=timeout)
    return list((response.get("result") or {}).get("tools") or [])


def _parse_text(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def call(tool: str, http_timeout: float = DEFAULT_TIMEOUT, **arguments):
    """Call a tool and return its parsed result.

    Most tools return an object with "ok": true as JSON text; that dict is returned
    directly. Image tools (screenshot) return a dict with content/images/text/meta.
    A tool result with isError raises LiveError with the server's text.

    http_timeout bounds the HTTP request. Tools that take their own "timeout" argument
    (run_python) still receive it normally, because this parameter is not forwarded.
    """
    response = _rpc("tools/call", {"name": tool, "arguments": arguments}, timeout=http_timeout)
    result = response.get("result") or {}
    content = list(result.get("content") or [])
    texts = [item.get("text", "") for item in content if item.get("type") == "text"]
    text = " ".join(part for part in texts if part)
    if result.get("isError"):
        raise LiveError(f"{tool} failed: {text or 'no error text'}")
    images = [item for item in content if item.get("type") == "image"]
    if images:
        meta = _parse_text(texts[0]) if texts else {}
        return {"content": content, "images": images, "text": text, "meta": meta}
    if len(texts) == 1:
        return _parse_text(texts[0])
    if not content:
        return None
    return {"content": content, "text": text}


def restart(root: str, timeout: float = 60.0) -> dict:
    """Restart MCP Plus inside Cascadeur from root through the official script server.

    This mirrors docs/tickets/bin/live.py's restart: the official server executes
    start_mcp_plus.py with __file__ set to that checkout, which also clears the package
    modules so the new checkout is imported. Returns the new /health payload.
    """
    start = os.path.join(os.path.abspath(root), "start_mcp_plus.py")
    if not os.path.isfile(start):
        raise LiveError(f"No start_mcp_plus.py in {os.path.abspath(root)}")
    port = urllib.parse.urlsplit(PLUS_URL).port or 48766
    code = f'exec(open(r"{start}", encoding="utf-8").read(), {{"__file__": r"{start}", "MCP_PLUS_PORT": {port}}})'
    try:
        response = _http(f"{OFFICIAL_URL}/run", {"code": code}, timeout=timeout)
    except LiveError as exc:
        raise LiveError(
            f"Cannot restart MCP Plus through Cascadeur's script server ({OFFICIAL_URL}). "
            "In Cascadeur click Scripts > MCP > Start script server, then retry. "
            f"Details: {exc}"
        ) from exc
    if not isinstance(response, dict) or not response.get("ok"):
        raise LiveError(f"Cascadeur rejected the MCP Plus start script: {response!r}")
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        info = health()
        if info is not None:
            return info
        time.sleep(0.2)
    raise LiveError(
        f"MCP Plus did not answer at {PLUS_URL} within 30 s after restart. Check get_log or Cascadeur's log."
    )
