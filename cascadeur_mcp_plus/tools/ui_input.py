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
        "description": (
            "Last resort: List open windows and on-screen UI elements (text, tooltip, actionId, objectName, "
            "type, rectangle, clickable/input flags) with refs for the other interface tools. Use it only to "
            "diagnose the interface or to locate an element that no dedicated tool exposes; get_scene_info, "
            "list_controllers, get_timeline and get_display_state cover normal scene work. Returns windows and "
            "elements (each with a ref, plus matched/truncated counts); refs reset on every ui_tree or dialogs call."
        ),
        "inputSchema": schema(
            {
                "window": WINDOW,
                "query": {
                    "type": "string",
                    "description": "Only elements whose text/tooltip/actionId/objectName/type contains this.",
                },
                "interactive_only": {"type": "boolean", "description": "Only clickable, input or action elements."},
                "include_hidden": {
                    "type": "boolean",
                    "description": "Include hidden windows and elements (default false).",
                },
                "limit": {"type": "integer", "description": "Max elements (default 150)."},
            }
        ),
        "fn": ui.tree,
    },
    {
        "name": "ui_click",
        "description": (
            "Last resort: Click like a real user (synthesized mouse events) on an element by ref or query, or at "
            "window coordinates x/y. Use it only when no dedicated tool performs the action: set_pose for posing, "
            "select_objects for selection, set_key for keying and call_action for a Cascadeur action. Supports "
            "right/middle button, double click and modifiers. Returns the clicked window/point, the element "
            "details and other query matches; a disabled element is flagged."
        ),
        "inputSchema": schema(
            {
                "ref": REF,
                "query": QUERY,
                "window": WINDOW,
                "x": {
                    "type": "number",
                    "description": "Window x coordinate in physical pixels; pass with y instead of ref/query.",
                },
                "y": {
                    "type": "number",
                    "description": "Window y coordinate in physical pixels; pass with x instead of ref/query.",
                },
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "description": "Mouse button (default left).",
                },
                "double": {
                    "type": "boolean",
                    "description": "Send a double click instead of a single click (default false).",
                },
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
        "description": (
            "Last resort: Drag with the mouse like a user: press at the start, move in steps (button held), release "
            "at the end. Use it only for a widget that has no dedicated tool or action; to pose a character use "
            "set_pose instead of dragging controllers, and to move the camera use set_camera or camera_preset. "
            "Start: object (scene object name, projected into the 3D viewport), ref, query or window x/y. End: "
            "to_x/to_y or offset dx/dy in window coordinates. Each step runs separately so Cascadeur updates "
            "between moves. Returns the actual start/end points, the method and any dialog that blocked the drag."
        ),
        "inputSchema": schema(
            {
                "object": {
                    "type": "string",
                    "description": "Scene object to start from, e.g. 'mixamorig:LeftHand_MainPoint'.",
                },
                "ref": REF,
                "query": QUERY,
                "window": WINDOW,
                "x": {
                    "type": "number",
                    "description": "Window starting x in physical pixels; pass with y when not using object/ref/query.",
                },
                "y": {
                    "type": "number",
                    "description": "Window starting y in physical pixels; pass with x when not using object/ref/query.",
                },
                "to_x": {"type": "number", "description": "Window end x; pass with to_y instead of dx/dy."},
                "to_y": {"type": "number", "description": "Window end y; pass with to_x instead of dx/dy."},
                "dx": {"type": "number", "description": "Horizontal offset from the start point in window pixels."},
                "dy": {
                    "type": "number",
                    "description": "Vertical offset from the start point; negative moves up on screen.",
                },
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "description": "Mouse button held during the drag (default left).",
                },
                "modifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": 'Modifier keys held during the drag, e.g. ["ctrl"].',
                },
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
        "description": (
            "Last resort: Move the mouse over an element or point to show tooltips and hover menus. Use it only "
            "to inspect a widget that no dedicated tool describes; get_scene_info, list_controllers and "
            "get_display_state cover scene state. Returns the hovered window and point."
        ),
        "inputSchema": schema(
            {
                "ref": REF,
                "query": QUERY,
                "window": WINDOW,
                "x": {
                    "type": "number",
                    "description": "Window x coordinate in physical pixels; pass with y instead of ref/query.",
                },
                "y": {
                    "type": "number",
                    "description": "Window y coordinate in physical pixels; pass with x instead of ref/query.",
                },
                "settle_ms": SETTLE,
            }
        ),
        "fn": ui.hover,
        "settle": True,
    },
    {
        "name": "ui_key",
        "description": (
            "Last resort: Press keys or shortcuts, space-separated combos such as 'ctrl+s', 'Escape', "
            "'ctrl+a Delete' or 'F'. Use it only for a shortcut with no dedicated tool or action; prefer "
            "set_frame, undo/redo, call_action and the scene tools for the same operation. Returns the window and "
            "the combos that were pressed."
        ),
        "inputSchema": schema(
            {
                "keys": {
                    "type": "string",
                    "description": "Space-separated key combos, e.g. 'ctrl+s' or 'ctrl+a Delete'.",
                },
                "window": WINDOW,
                "settle_ms": SETTLE,
            },
            ["keys"],
        ),
        "fn": lambda keys, window=None: ui.keys(keys, window),
        "settle": True,
    },
    {
        "name": "ui_type",
        "description": (
            "Last resort: Type text into the focused element, or focus an element by ref/query first; replace "
            "clears it and submit presses Enter. Use it only for a text field that no dedicated tool sets; "
            "call_action and the state tools should handle normal edits. Returns the window, the typed text and "
            "the target element."
        ),
        "inputSchema": schema(
            {
                "text": {"type": "string", "description": "Text to type; Unicode characters are sent as key events."},
                "ref": REF,
                "query": QUERY,
                "window": WINDOW,
                "replace": {"type": "boolean", "description": "Clear the field before typing (default false)."},
                "submit": {"type": "boolean", "description": "Press Enter after typing (default false)."},
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
        "description": (
            "Last resort: Set a QML property on an element (for example checked, value, text or currentIndex). "
            "Use it only when no dedicated tool or action exists: set_view_mode, set_ghosts, set_trajectories, "
            "set_silhouette and set_surface cover the display state. It bypasses input handlers. Returns ok, the "
            "value before and the value after."
        ),
        "inputSchema": schema(
            {
                "ref": REF,
                "name": {"type": "string", "description": "QML property name, e.g. checked, value or text."},
                "value": {"description": "New property value (string, number, boolean or null)."},
            },
            ["ref", "name", "value"],
        ),
        "fn": ui.set_property,
    },
    {
        "name": "ui_invoke",
        "description": (
            "Last resort: Invoke a no-argument QML method or signal on an element, for example 'clicked', "
            "'toggle', 'open' or 'close'. Use it only when the behavior has no dedicated tool or Cascadeur action; "
            "prefer call_action when the action has an actionId. Returns ok and the element details."
        ),
        "inputSchema": schema(
            {
                "ref": REF,
                "method": {
                    "type": "string",
                    "description": "No-argument QML method or signal name, e.g. clicked or toggle.",
                },
            },
            ["ref", "method"],
        ),
        "fn": ui.invoke,
    },
    {
        "name": "dialogs",
        "description": (
            "List visible dialogs/popups and secondary windows with their texts and button refs. Use it to check "
            "that a tool left no dialog open, or to find a prompt that needs an answer. Returns dialogs (kind, "
            "window index, type, texts, buttons with refs) and a hint to press a button with ui_click(ref=...); "
            "refreshing this list resets the ui_tree refs."
        ),
        "inputSchema": schema(),
        "fn": ui.dialogs,
    },
    {
        "name": "list_actions",
        "description": (
            "List Cascadeur action keys (actionId) found in the live UI, for example 'Commands.Rig mode.On', with "
            "labels. Use it when no dedicated tool exists for an operation, then trigger the key with call_action. "
            "Returns actions (actionId, label, on_screen); actions of tools that have never been opened may be missing."
        ),
        "inputSchema": schema(
            {
                "query": {"type": "string", "description": "Only actions whose actionId contains this text."},
                "include_hidden": {
                    "type": "boolean",
                    "description": "Include actions of hidden windows (default true).",
                },
            }
        ),
        "fn": ui.list_actions,
    },
    {
        "name": "call_action",
        "description": (
            "Trigger a Cascadeur action by key through ActionManager.call_action, exactly like its toolbar button "
            "or menu item. Use a dedicated tool first when one exists (set_pose, set_key, select_objects, ...); "
            "call_action is for actions that have no wrapper. Use list_actions to discover keys. Returns the called key."
        ),
        "inputSchema": schema(
            {
                "key": {
                    "type": "string",
                    "description": "Cascadeur actionId from list_actions, e.g. 'Commands.Rig mode.On'.",
                },
                "settle_ms": SETTLE,
            },
            ["key"],
        ),
        "fn": ui.call_action,
        "settle": True,
    },
]
