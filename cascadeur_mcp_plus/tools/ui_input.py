"""Qt Quick UI inspection and synthetic mouse/keyboard input, plus Cascadeur actions."""

from __future__ import annotations

import time

from .. import dispatch, ui
from ._schema import QUERY, REF, SETTLE, WINDOW, schema


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
    begin = dispatch.call_on_gui(
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
    try:
        time.sleep(max(0.0, float(hold_ms)) / 1000.0)
        for i in range(1, steps + 1):
            t = i / steps
            dispatch.call_on_gui(ui.drag_move, {"x": sx + (ex - sx) * t, "y": sy + (ey - sy) * t}, 30.0)
            time.sleep(interval)
    finally:
        # Always release, even if a move failed, so Cascadeur is not left with a held button.
        released = dispatch.call_on_gui(ui.drag_end, {}, 30.0)
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
    dialogs = dispatch.call_on_gui(ui.dialogs, {}, 30.0)["dialogs"]
    if dialogs:
        result["dialogs"] = dialogs
        result["warning"] = (
            "A dialog is open; the drag may not have applied. Answer it with ui_click(ref=...) and drag again."
        )
    return result


TOOLS = [
    {
        "name": "ui_tree",
        "description": "List open windows and on-screen UI elements (text, tooltip, actionId, type, rect, clickable/input flags) with refs for the other ui_* tools.",
        "inputSchema": schema(
            {
                "window": WINDOW,
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
        "inputSchema": schema(
            {
                "ref": REF,
                "query": QUERY,
                "window": WINDOW,
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
                "settle_ms": SETTLE,
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
        "inputSchema": schema(
            {
                "object": {
                    "type": "string",
                    "description": "Scene object to start from, e.g. 'mixamorig:LeftHand_MainPoint'.",
                },
                "ref": REF,
                "query": QUERY,
                "window": WINDOW,
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
                "settle_ms": SETTLE,
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
        "handler": _drag,
    },
    {
        "name": "ui_hover",
        "description": "Move the mouse over an element or point (shows tooltips, hover menus).",
        "inputSchema": schema(
            {
                "ref": REF,
                "query": QUERY,
                "window": WINDOW,
                "x": {"type": "number"},
                "y": {"type": "number"},
                "settle_ms": SETTLE,
            }
        ),
        "fn": ui.hover,
        "settle": True,
    },
    {
        "name": "ui_key",
        "description": "Press keys or shortcuts, space-separated combos, e.g. 'ctrl+s', 'Escape', 'ctrl+a Delete', 'F'.",
        "inputSchema": schema({"keys": {"type": "string"}, "window": WINDOW, "settle_ms": SETTLE}, ["keys"]),
        "fn": lambda keys, window=None: ui.keys(keys, window),
        "settle": True,
    },
    {
        "name": "ui_type",
        "description": "Type text into the focused element, or focus an element by ref/query first. replace clears it; submit presses Enter.",
        "inputSchema": schema(
            {
                "text": {"type": "string"},
                "ref": REF,
                "query": QUERY,
                "window": WINDOW,
                "replace": {"type": "boolean"},
                "submit": {"type": "boolean"},
                "release_focus": {
                    "type": "boolean",
                    "description": "Drop keyboard focus afterwards so shortcuts reach Cascadeur again.",
                },
                "settle_ms": SETTLE,
            },
            ["text"],
        ),
        "fn": ui.type_text,
        "settle": True,
    },
    {
        "name": "ui_set_property",
        "description": "Set a QML property on an element (e.g. checked, value, text, currentIndex). Bypasses input handlers; prefer ui_click when possible.",
        "inputSchema": schema({"ref": REF, "name": {"type": "string"}, "value": {}}, ["ref", "name", "value"]),
        "fn": ui.set_property,
    },
    {
        "name": "ui_invoke",
        "description": "Invoke a no-argument QML method or signal on an element, e.g. 'clicked', 'toggle', 'open', 'close'. Useful when an element cannot be clicked on screen.",
        "inputSchema": schema({"ref": REF, "method": {"type": "string"}}, ["ref", "method"]),
        "fn": ui.invoke,
    },
    {
        "name": "dialogs",
        "description": "List visible dialogs/popups and secondary windows with their texts and buttons (as refs).",
        "inputSchema": schema(),
        "fn": ui.dialogs,
    },
    {
        "name": "list_actions",
        "description": "List Cascadeur action keys (actionId) found in the UI, e.g. 'Commands.Rig mode.On', with labels.",
        "inputSchema": schema({"query": {"type": "string"}, "include_hidden": {"type": "boolean"}}),
        "fn": ui.list_actions,
    },
    {
        "name": "call_action",
        "description": "Trigger a Cascadeur action by key via ActionManager.call_action, exactly like its toolbar button or menu item.",
        "inputSchema": schema({"key": {"type": "string"}, "settle_ms": SETTLE}, ["key"]),
        "fn": ui.call_action,
        "settle": True,
    },
]
