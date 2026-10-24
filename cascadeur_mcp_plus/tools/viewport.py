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
        "description": (
            "Last resort: Capture a Cascadeur window (or one element by ref) as an image. Use render_image to see "
            "the 3D scene and render_contact_sheet to inspect a motion; screenshot is only for the interface itself "
            "(a widget, menu or dialog). window_units_per_pixel converts image pixels to the window coordinates used "
            "by ui_click x/y. Returns image content plus metadata (window title/size, image size, crop rectangle and "
            "scale); restore=true un-minimizes the window first."
        ),
        "inputSchema": schema(
            {
                "window": WINDOW,
                "ref": REF,
                "max_width": {"type": "integer", "description": "Downscale to this width (default 1280)."},
                "format": {
                    "type": "string",
                    "enum": ["jpeg", "png"],
                    "description": "Image encoding: jpeg (default) or png.",
                },
                "restore": {
                    "type": "boolean",
                    "description": "Un-minimize the window first (a minimized window renders nothing).",
                },
            }
        ),
        "requires": [],
        "handler": lambda window=None, ref=None, max_width=1280, format="jpeg", restore=False: _screenshot(
            window, ref, max_width, format, restore
        ),
        "image": True,
    },
    {
        "name": "project_point",
        "description": (
            "Last resort: Project scene objects by name to main-window coordinates in the 3D viewport, for the "
            "last-resort ui_click/ui_drag tools. Use set_pose to move control points, select_objects to select them "
            "and the camera tools for normal work; this only computes coordinates. Uses the viewport camera and a "
            "30 degree vertical FOV (verified for Cascadeur 2026.2.3). Returns each object's world position, depth, "
            "window x/y and whether it is on screen."
        ),
        "inputSchema": schema(
            {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Exact scene object names to project, e.g. ['mixamorig:LeftHand_MainPoint']. Aliases such "
                        "as left_hand are not resolved; list_controllers gives the names."
                    ),
                },
                "fov_deg": {
                    "type": "number",
                    "description": "Vertical field of view in degrees (default 30, measured for Cascadeur 2026.2.3).",
                },
            },
            ["names"],
        ),
        "requires": [
            "csc.app.get_application",
            "csc.domain.Scene.get_current_frame",
            "csc.domain.Scene.model_viewer",
            "csc.model.BehaviourViewer.get_behaviours",
            "csc.model.ModelViewer.get_objects",
            "csc.view.Viewport.domain_viewport",
        ],
        "fn": ui.project_points,
    },
]
