"""Tool registry: every module in this package exports a TOOLS list.

Adding a tool means adding a module here (or a dict to an existing module's TOOLS); server.py
loads them all through load_tools(), so new tools never edit a shared central list.

Tool dict fields
----------------
name : str
    Unique snake_case name, verb first (e.g. ``set_pose``).
description : str
    English. The first sentence says what the tool does, then when to use it, what it
    returns, and the units (length cm, angles degrees).
inputSchema : dict
    JSON Schema of the arguments.
fn : callable, optional
    Called as ``fn(**arguments)`` on the GUI thread by default. Return a JSON-serializable
    value: ``{"ok": true, ...}`` on success, ``{"ok": false, "error": "...", "hint": "..."}``
    on failure. Exactly one of ``fn`` and ``handler`` is required.
handler : callable, optional
    Called as ``handler(**arguments)`` on the HTTP thread. Use it for work that must not
    block the GUI (sleeping, waiting, orchestrating several GUI jobs); call
    dispatch.call_on_gui for the parts that need Cascadeur.
gui : bool, optional (default True)
    Run ``fn`` on the GUI thread. Set False for functions that touch no Qt or scene state.
idle : bool, optional (default False)
    Queue ``fn`` for the scene-idle event instead of the 25 ms QTimer, like the official
    script server. Needed for rig mode and other scene-graph edits.
settle : bool, optional (default False)
    After the call, sleep ``settle_ms`` from the arguments (default 150, capped at 5000) on
    the HTTP thread so the UI can repaint before the next request.
image : bool, optional (default False)
    The handler returns ``(base64_data, mime_type, metadata)``; the result becomes an MCP
    image content item plus the metadata as JSON text.
timeout_arg : bool, optional (default False)
    Read ``timeout`` (seconds, capped at 600) from the arguments as the GUI wait timeout;
    otherwise the timeout is 60 s.
background : bool, optional (default False)
    Long-running tool (T03). _call_tool runs ``fn`` on the GUI thread only to start the work,
    then returns {"ok": true, "job_id": ...} with hint "call get_job" instead of waiting. ``fn``
    is the begin callable: it must return None or {"ok": true, ...}. Returning {"ok": false,
    "error": ..., "hint": ...} (common.fail) or raising aborts the call synchronously with that
    error and creates no job, so bad arguments are reported before any polling starts. The dict
    must also provide ``poll`` (called with the same arguments; returns {"done": bool,
    "progress": 0..1 or None, "result": ...}; {"ok": false, "error": ..., "hint": ...} fails the
    job) and may provide ``cancel`` (same arguments) and ``job_timeout`` (seconds, default no
    timeout). The poll result is converted with common.jsonable once, on the GUI thread, when the
    job finishes. See jobs.py; never block or sleep inside fn or poll.
requires : list of str
    What the tool cannot work without (T54). Required on every tool; an empty list means the tool
    needs no Cascadeur API (a pure process, file or Qt-only tool). Entries are dotted API paths
    rooted at the ``csc`` module (``"csc.tools.RenderToFile.take_image"``), action keys prefixed
    with ``action:`` (``"action:AutoPhysicsTool.Snap to Auto Physics"``) and official script
    modules prefixed with ``module:`` (``"module:rig_mode.on:run_raw"``). compat.py checks every
    entry against the running Cascadeur at startup and get_status re-runs the check. A missing
    entry keeps the tool in tools/list, but its description is prefixed with "Unavailable in this
    Cascadeur version:" and calls return the missing entries instead of failing halfway. Add a new
    entry whenever a tool starts using another API or action.

New flags may be added; unknown fields are ignored by the server so a module can carry extra
metadata.
"""

from __future__ import annotations

import importlib
import pkgutil

from .. import compat


def load_tools() -> list[dict]:
    """Import every public tool module in file-name order and return their tools.

    Modules whose name starts with an underscore are helpers, not tool modules. A duplicate
    tool name is an error rather than a silent override.
    """
    tools: list[dict] = []
    origin: dict[str, str] = {}
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda module: module.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        module_tools = getattr(module, "TOOLS", None)
        if not isinstance(module_tools, list):
            raise RuntimeError(f"{module.__name__} must define TOOLS as a list of tool dicts")
        for tool in module_tools:
            for key in ("name", "description", "inputSchema"):
                if key not in tool:
                    raise RuntimeError(f"{module.__name__} has a tool without {key!r}: {tool!r}")
            if ("fn" in tool) == ("handler" in tool):
                raise RuntimeError(f"Tool {tool['name']!r} in {module.__name__} needs exactly one of fn/handler")
            if "requires" not in tool:
                raise RuntimeError(f"Tool {tool['name']!r} in {module.__name__} has no requires field (T54)")
            try:
                compat.validate_requires(tool["requires"])
            except ValueError as e:
                raise RuntimeError(f"Tool {tool['name']!r} in {module.__name__} has a bad requires field: {e}") from e
            name = tool["name"]
            if name in origin:
                raise RuntimeError(f"Duplicate tool {name!r} in {module.__name__} and {origin[name]}")
            origin[name] = module.__name__
            tools.append(tool)
    return tools
