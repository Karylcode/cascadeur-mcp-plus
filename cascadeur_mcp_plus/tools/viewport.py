"""Viewport screenshots and scene-to-window projection."""

from __future__ import annotations

import time

from .. import dispatch, ui
from ._schema import REF, WINDOW, schema


def _screenshot(window, ref, max_width, fmt, restore):
    if restore:
        restored = dispatch.call_on_gui(ui.restore_window, {"window": window}, 30.0)
        if restored.get("restored"):
            time.sleep(0.8)  # let the window repaint before capturing
    return dispatch.call_on_gui(ui.screenshot, {"window": window, "ref": ref, "max_width": max_width, "fmt": fmt}, 30.0)


TOOLS = [
    {
        "name": "screenshot",
        "description": "Capture a Cascadeur window (or one element by ref) as an image. window_units_per_pixel converts image pixels to the window coordinates used by ui_click x/y.",
        "inputSchema": schema(
            {
                "window": WINDOW,
                "ref": REF,
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
        "name": "project_point",
        "description": "Project scene objects (by name) to main-window coordinates in the 3D viewport, for ui_click/ui_drag x/y. "
        "Uses the viewport camera and a 30 degree vertical FOV (verified for Cascadeur 2026.2.3).",
        "inputSchema": schema(
            {
                "names": {"type": "array", "items": {"type": "string"}},
                "fov_deg": {"type": "number"},
            },
            ["names"],
        ),
        "fn": ui.project_points,
    },
]
