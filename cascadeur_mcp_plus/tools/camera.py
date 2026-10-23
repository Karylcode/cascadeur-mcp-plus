"""Viewport camera tools: read/write, preset views, framing and tracking (T31).

camera_preset is the tool T32 and T33 use to put the camera in a repeatable place before
rendering: front/back/left/right follow the character's own facing (pelvis, chest and shoulder
line) instead of the world axes, and the distance defaults to framing the whole character.
frame_objects keeps the current view direction but fits the given objects. track_object uses
Cascadeur's ObjectWatchingTool action, which is a toggle without a Python state query.
"""

from __future__ import annotations

import traceback

from .. import camera, common, rig
from ._schema import schema


def _unexpected(exc):
    return common.fail(
        f"Camera operation failed: {type(exc).__name__}: {exc}",
        hint="Open a scene with a 3D viewport in Cascadeur and try again; get_log has the traceback.",
        traceback=traceback.format_exc(),
    )


def _failure(exc):
    if isinstance(exc, rig.RigError):
        return exc.result()
    if isinstance(exc, (ValueError, common.ToolError)):
        return common.fail(str(exc))
    return _unexpected(exc)


def get_camera() -> dict:
    """Return the main viewport's camera and field of view."""
    try:
        result = camera.read_camera()
    except Exception as exc:
        return _failure(exc)
    return common.ok(camera=result)


def set_camera(position=None, target=None) -> dict:
    """Move the viewport camera to position and/or target (cm)."""
    try:
        result = camera.set_camera(position=position, target=target)
    except Exception as exc:
        return _failure(exc)
    return common.ok(camera=result["camera"], previous=result["previous"])


def camera_preset(view, target="character", distance=None) -> dict:
    """Apply one of the six preset views to the whole character or a control point."""
    try:
        result = camera.apply_preset(view, target=target, distance=distance)
    except Exception as exc:
        return _failure(exc)
    return common.ok(**result)


def frame_objects(names) -> dict:
    """Move the camera so the named objects are all in view."""
    try:
        if isinstance(names, str):
            names = [names]
        if not isinstance(names, (list, tuple)) or not names:
            raise ValueError("names must be a non-empty list of object names or aliases.")
        result = camera.frame_objects([str(name) for name in names])
    except Exception as exc:
        return _failure(exc)
    return common.ok(**result)


def track_object(name=None) -> dict:
    """Start camera tracking for an object/character, or stop it with no name."""
    try:
        result = camera.track_object(name)
    except Exception as exc:
        return _failure(exc)
    return common.ok(**result)


TOOLS = [
    {
        "name": "get_camera",
        "description": (
            "Return the main 3D viewport camera: position and target in world coordinates (cm), type "
            "(PERSPECTIVE or ISOMETRIC), distance (cm), view direction and vertical field of view (degrees). "
            "Use it before moving the camera or to check what a render will show. Read-only."
        ),
        "inputSchema": schema(),
        "fn": get_camera,
    },
    {
        "name": "set_camera",
        "description": (
            "Move the viewport camera by setting its world position and/or look-at target (cm); omitted "
            "fields keep their current value. Use it for a specific shot, or camera_preset for "
            "front/back/side views of a character. The camera cannot roll: its up is the world up "
            "projected onto the view plane, like the viewport itself. Returns the camera as read back "
            "and the previous values so a caller can restore them."
        ),
        "inputSchema": schema(
            {
                "position": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": "Camera position [x, y, z] in world cm.",
                },
                "target": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": "Look-at target [x, y, z] in world cm.",
                },
            }
        ),
        "fn": set_camera,
    },
    {
        "name": "camera_preset",
        "description": (
            "Put the camera in a standard view of a character: front, back, left, right, top or "
            "three_quarter. front/back/left/right use the character's own facing, derived from the pelvis, "
            "chest and shoulder control points, so front still shows the face after the character turns; "
            "they do not use world axes. target is 'character' (default, needs exactly one character in the "
            "scene), a character name from list_characters, or a control point name/alias from "
            "list_controllers to aim at that point. distance is the distance from target to camera in cm; "
            "omit it to frame the whole character with a small margin. The camera is switched to "
            "PERSPECTIVE (the framing maths assumes it) and the camera is returned as read back."
        ),
        "inputSchema": schema(
            {
                "view": {
                    "type": "string",
                    "enum": list(camera.VIEWS),
                    "description": "Standard view: front, back, left, right, top or three_quarter.",
                },
                "target": {
                    "type": "string",
                    "description": "Character name, or a control point name/alias to aim at (default 'character').",
                },
                "distance": {
                    "type": "number",
                    "description": "Distance from the target to the camera in cm (default: fit the whole character).",
                },
            },
            ["view"],
        ),
        "fn": camera_preset,
    },
    {
        "name": "frame_objects",
        "description": (
            "Move the camera so every named object is inside the view, keeping the current view direction. "
            "names accepts object names and character aliases (left_hand, pelvis, head, ...). Use it to look "
            "at a set of control points or a prop; camera_preset is better for a standard view of a whole "
            "character. Returns the new camera, the aim point (cm) and each object's normalized screen "
            "position. Keeps the character smaller than the frame by a small margin."
        ),
        "inputSchema": schema(
            {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "Object names or character aliases to frame.",
                }
            },
            ["names"],
        ),
        "fn": frame_objects,
    },
    {
        "name": "track_object",
        "description": (
            "Make the viewport camera follow an object or character (name), or stop following when name is "
            "omitted. Use it while an animation plays and the character moves out of frame. Cascadeur only "
            "exposes tracking as the toggle action 'Track selection with camera' with no Python state query, "
            "so this tool remembers whether it turned tracking on and track_object() only turns it off again "
            "when it did; it also changes the scene selection to the tracked object and restores the previous "
            "selection when tracking is turned off."
        ),
        "inputSchema": schema(
            {
                "name": {
                    "type": "string",
                    "description": "Object name, character name or control point alias to follow; omit to stop.",
                }
            }
        ),
        "fn": track_object,
    },
]
