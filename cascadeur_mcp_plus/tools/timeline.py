"""Frame, timeline and keyframe tools (T14).

get_timeline and set_frame move the playhead; set_animation_range changes the animation's
first/last frame; set_key and delete_key add or remove keys (one undo step per call) through
Cascadeur's own layers editor; list_keys reports the keys and their interpolation; select_frames
selects the timeline interval that the "Timeline.* on selected interval" actions operate on.

The mechanics live in cascadeur_mcp_plus/timeline.py so T16, T17, T19, T20 and T21 can reuse
them (set_key_at, unset_key_at, select_interval, list_keys' summary helpers).
"""

from __future__ import annotations

import functools
import traceback

from .. import common, rig, timeline
from ..errors import ToolError
from ._schema import schema
from .layers import ensure_unlocked

FRAMES_SCHEMA = {
    "type": ["integer", "array", "string"],
    "items": {"type": "integer"},
    "description": "One frame number or a list of frame numbers (0 or more).",
}
CONTROLLERS_SCHEMA = {
    "type": ["string", "array"],
    "items": {"type": "string"},
    "description": "Controller names or aliases such as left_hand (omit for the whole character).",
}
FRAME_SCHEMA = {
    "type": ["integer", "string"],
    "description": "Frame number (0 or more).",
}


class _BadArgument(ToolError):
    """A tool argument is invalid; carries the hint that belongs in the result."""

    def __init__(self, message: str, hint: str):
        super().__init__(message)
        self.hint = hint


def _tool(hint: str):
    """Turn exceptions into the shared {"ok": false, "error", "hint"} result."""

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except rig.RigError as exc:
                return exc.result()
            except ToolError as exc:
                return common.fail(str(exc), getattr(exc, "hint", None) or hint)
            except Exception as exc:  # noqa: BLE001 - tool boundary: report instead of raising
                return common.fail(
                    f"{type(exc).__name__}: {exc}",
                    hint + " get_log has the traceback.",
                    traceback=traceback.format_exc(),
                )

        return wrapper

    return decorate


def _objects(controllers) -> list:
    """Resolve the controllers argument; omitted means every controller in the scene."""
    if controllers is None or controllers == "" or controllers == []:
        return timeline.target_object_ids()
    return rig.resolve(controllers)


def _layer_ids_outside(object_ids) -> list:
    """Layer ids of object ids using the scene's layers viewer (read path)."""
    _app, _view_scene, scene = common.scenes()
    return timeline.layer_ids_for_viewer(scene.layers_viewer(), object_ids)


@_tool("Check that an animated scene is open; get_log may explain a rejected scene change.")
def set_frame(frame=None) -> dict:
    """Move the playhead to a frame."""
    wanted = timeline.parse_frame(frame, "frame")
    _app, view_scene, scene = common.scenes()
    info = timeline.boundary_info(view_scene)
    scene.set_current_frame(wanted)
    # get_current_frame() clamps its answer to the animation range by default; the playhead can
    # sit outside it (set_current_frame does not clamp), so read the real position.
    actual = int(scene.get_current_frame(False))
    result = common.ok(frame=actual, requested=wanted)
    if actual != wanted:
        result["hint"] = (
            f"Cascadeur put the playhead on frame {actual} instead of {wanted}; "
            "check that the frame is valid and try again."
        )
    elif actual < info["first_frame"] or actual > info["last_frame"]:
        result["hint"] = (
            f"Frame {actual} is outside the animation range {info['first_frame']}-{info['last_frame']}. "
            "Keys on it are not visible on the timeline; use set_animation_range to widen the range."
        )
    return result


@_tool("Check that an animated scene is open; get_log may explain a rejected scene change.")
def get_timeline() -> dict:
    """Return the current frame, animation range, visible range, length and fps."""
    _app, view_scene, scene = common.scenes()
    info = timeline.boundary_info(view_scene)
    first, last = info["first_frame"], info["last_frame"]
    length = last - first + 1
    fps = timeline.scene_fps(view_scene)
    data = {
        # The playhead can be outside the animation range; get_current_frame() would clamp it.
        "frame": int(scene.get_current_frame(False)),
        **info,
        "length": length,
        "fps": fps,
    }
    if fps:
        data["seconds"] = round(length / fps, 3)
    return common.ok(**common.jsonable(data))


@_tool("Check that an animated scene is open and that first/last fit the timeline.")
def set_animation_range(first=None, last=None) -> dict:
    """Set the animation's first and/or last frame."""
    if first is None and last is None:
        raise _BadArgument(
            "Pass first and/or last.",
            "For example set_animation_range(first=0, last=120).",
        )
    wanted_first = None if first is None else timeline.parse_frame(first, "first")
    wanted_last = None if last is None else timeline.parse_frame(last, "last")
    if wanted_first is not None and wanted_last is not None and wanted_first > wanted_last:
        raise _BadArgument(
            f"first ({wanted_first}) must not be greater than last ({wanted_last}).",
            "Widen the range in two calls or pass a smaller first / larger last.",
        )
    _app, view_scene, scene = common.scenes()
    boundary = view_scene.animation_boundary()
    new_first = int(boundary.first_frame) if wanted_first is None else wanted_first
    new_last = int(boundary.last_frame) if wanted_last is None else wanted_last
    if new_first > new_last:
        raise _BadArgument(
            f"first ({new_first}) must not be greater than last ({new_last}).",
            "Widen the range in two calls or pass a smaller first / larger last.",
        )

    if wanted_first is not None:
        boundary.first_frame = wanted_first
    if wanted_last is not None:
        boundary.last_frame = wanted_last

    info = timeline.boundary_info(view_scene)
    data = {
        "requested_first": wanted_first,
        "requested_last": wanted_last,
        **info,
        "frame": int(scene.get_current_frame(False)),
        # Verified on Cascadeur 2026.2.3: wrapping this in scene.modify_update creates an
        # "MCP: set_animation_range" log entry, but undo() leaves the boundary at the new value.
        "undoable": False,
    }
    result = common.ok(**common.jsonable(data))
    notes = []
    if (wanted_first is not None and info["first_frame"] != wanted_first) or (
        wanted_last is not None and info["last_frame"] != wanted_last
    ):
        notes.append(
            "Cascadeur adjusted the requested range (see first_frame/last_frame); "
            "keys outside the animation range are not visible on the timeline."
        )
    notes.append(
        "The animation range is a scene setting, not model data, so Cascadeur's undo does not "
        "revert it; call set_animation_range with the old first/last to restore a range."
    )
    result["hint"] = " ".join(notes)
    return result


@_tool("Check that the scene is animated and the frames/controllers are valid.")
def set_key(frames=None, controllers=None) -> dict:
    """Key the requested frames on the requested controllers (whole character by default)."""
    wanted = timeline.parse_frames(frames)
    object_ids = _objects(controllers)
    if not object_ids:
        return common.fail(
            "No controllers matched, so no keys were set.",
            hint="Call list_controllers to see the control point names and aliases.",
        )
    if not _layer_ids_outside(object_ids):
        return common.fail(
            "None of the matched objects has a timeline layer, so no keys were set.",
            hint="Open an animated scene with RigInfo/AnimationInfo (for example Cascy.casc).",
        )
    blocked = ensure_unlocked(object_ids)
    if blocked is not None:
        return blocked
    names = timeline.object_names(object_ids)
    _app, view_scene, scene = common.scenes()
    info = timeline.boundary_info(view_scene)
    stats = {"layers": 0, "added": 0, "already": 0}

    def mod(model_editor, update_editor, scene_updater):
        layer_ids = timeline.layer_ids_for_editor(model_editor, object_ids)
        stats["layers"] = len(layer_ids)
        for frame in wanted:
            entry = timeline.set_key_on_layers(model_editor, layer_ids, frame)
            stats["added"] += entry["changed"]
            stats["already"] += entry["already"]
        if stats["added"]:
            model_editor.layers_editor().normalize_sections(scene)

    result = common.edit("set_key", mod)
    if not result.get("ok"):
        return result
    result.update(
        frames=wanted,
        controllers=common.jsonable(names),
        layers=stats["layers"],
        keys_added=stats["added"],
        keys_already=stats["already"],
    )
    outside = [frame for frame in wanted if frame < info["first_frame"] or frame > info["last_frame"]]
    if outside:
        result["warning"] = (
            f"Frames outside the animation range {info['first_frame']}-{info['last_frame']}: {outside}. "
            "They are keyed but not visible on the timeline."
        )
        result["hint"] = "Use set_animation_range to include them."
    elif stats["added"] == 0:
        result["hint"] = "Every requested frame already had a key on all matched layers."
    return common.jsonable(result)


@_tool("Check that the scene is animated and the frames/controllers are valid.")
def delete_key(frames=None, controllers=None) -> dict:
    """Remove keys on the requested frames from the requested controllers."""
    wanted = timeline.parse_frames(frames)
    object_ids = _objects(controllers)
    if not object_ids:
        return common.fail(
            "No controllers matched, so no keys were removed.",
            hint="Call list_controllers to see the control point names and aliases.",
        )
    names = timeline.object_names(object_ids)
    _app, _view_scene, scene = common.scenes()
    viewer = scene.layers_viewer()
    if not _layer_ids_outside(object_ids):
        return common.fail(
            "None of the matched objects has a timeline layer, so no keys were removed.",
            hint="Open an animated scene with RigInfo/AnimationInfo (for example Cascy.casc).",
        )
    blocked = ensure_unlocked(object_ids)
    if blocked is not None:
        return blocked
    stats = {"layers": 0, "removed": 0, "missing": 0}

    def mod(model_editor, update_editor, scene_updater):
        layer_ids = timeline.layer_ids_for_editor(model_editor, object_ids)
        stats["layers"] = len(layer_ids)
        for frame in wanted:
            entry = timeline.unset_key_on_layers(model_editor, viewer, layer_ids, frame)
            stats["removed"] += entry["removed"]
            stats["missing"] += entry["missing"]
        if stats["removed"]:
            model_editor.layers_editor().normalize_sections(scene)

    result = common.edit("delete_key", mod)
    if not result.get("ok"):
        return result
    result.update(
        frames=wanted,
        controllers=common.jsonable(names),
        layers=stats["layers"],
        keys_removed=stats["removed"],
        frames_without_key=stats["missing"],
    )
    if stats["removed"] == 0:
        result["hint"] = "None of the requested frames had a key on the matched layers."
    return common.jsonable(result)


@_tool("Check that an animated scene is open and that start/end are valid frames.")
def list_keys(controllers=None, start=None, end=None) -> dict:
    """List keyframes and interpolation for the given controllers and frame range."""
    wanted_first = None if start is None else timeline.parse_frame(start, "start")
    wanted_last = None if end is None else timeline.parse_frame(end, "end")
    if wanted_first is not None and wanted_last is not None and wanted_first > wanted_last:
        raise _BadArgument(
            f"start ({wanted_first}) must not be greater than end ({wanted_last}).",
            "Pass start <= end; omit both for the animation range.",
        )
    _app, view_scene, scene = common.scenes()
    info = timeline.boundary_info(view_scene)
    first = info["first_frame"] if wanted_first is None else wanted_first
    last = info["last_frame"] if wanted_last is None else wanted_last
    if first > last:
        raise _BadArgument(
            f"start ({first}) must not be greater than end ({last}).",
            "Pass start <= end; omit both for the animation range.",
        )
    explicit = controllers is not None and controllers != "" and controllers != []
    object_ids = _objects(controllers) if explicit else timeline.target_object_ids()
    if explicit and not object_ids:
        return common.fail(
            "No controllers matched, so there is nothing to list.",
            hint="Call list_controllers to see the control point names and aliases.",
        )

    viewer = scene.layers_viewer()
    model_viewer = scene.model_viewer()
    groups = {}
    order = []
    skipped = []
    for object_id in object_ids:
        layer_id = timeline.layer_id_of(viewer, object_id)
        try:
            name = model_viewer.get_object_name(object_id)
        except Exception:
            name = None
        if layer_id is None:
            if explicit and name:
                skipped.append(name)
            continue
        if layer_id not in groups:
            groups[layer_id] = []
            order.append(layer_id)
        if name:
            groups[layer_id].append(name)

    entries = []
    for layer_id in order:
        try:
            layer = viewer.layer(layer_id)
        except Exception:
            continue
        summary = timeline.summarize_keys(timeline.key_frames(layer), timeline.interpolation_by_key(layer), first, last)
        if not explicit and not summary["keys"]:
            continue
        entries.append({"name": groups[layer_id][0], "names": groups[layer_id], **summary})

    key_frames = sorted({frame for entry in entries for frame in entry["keys"]})
    data = {
        "start": first,
        "end": last,
        "frame_count": last - first + 1,
        "key_frames": key_frames,
        "count": len(entries),
        "controllers": entries,
    }
    if not explicit:
        data["scanned_layers"] = len(order)
        data["without_keys"] = len(order) - len(entries)
    if skipped:
        data["without_layer"] = skipped
    result = common.ok(**common.jsonable(data))
    if not entries:
        result["hint"] = (
            f"No keyframes between frames {first} and {last}. Use set_key to add some, or pass "
            "controllers to list controllers that have no keys yet."
        )
    return result


@_tool("Check that an animated scene is open and that start/end are valid frames.")
def select_frames(start=None, end=None, controllers=None) -> dict:
    """Select a timeline interval for the "Timeline.* on selected interval" actions."""
    first = timeline.parse_frame(start, "start")
    last = timeline.parse_frame(end, "end")
    if first > last:
        raise _BadArgument(
            f"start ({first}) must not be greater than end ({last}).",
            "Pass start <= end, for example select_frames(start=0, end=20).",
        )
    explicit = controllers is not None and controllers != "" and controllers != []
    object_ids = _objects(controllers) if explicit else None
    names = timeline.object_names(object_ids) if object_ids else []
    layer_ids = None
    if object_ids is not None:
        layer_ids = _layer_ids_outside(object_ids)
        if not layer_ids:
            return common.fail(
                "None of the matched controllers has a timeline layer, so the interval was not selected.",
                hint="Call list_controllers for valid names, or omit controllers to select every layer.",
            )
    selected = timeline.select_interval(first, last, layer_ids)
    result = common.ok(
        requested_first=first,
        requested_last=last,
        first=selected["first"],
        last=selected["last"],
        layers=selected["layers"],
    )
    if explicit:
        result["controllers"] = common.jsonable(names)
    if selected["first"] != first or selected["last"] != last:
        result["hint"] = (
            "Cascadeur reported a different interval than requested (see first/last); "
            "use set_animation_range to make the frames selectable."
        )
    return common.jsonable(result)


TOOLS = [
    {
        "name": "get_timeline",
        "description": "Return the timeline state of the current scene: the current frame (the real playhead position, "
        "even when it is outside the animation range), the animation's first and last frame, the visible frame range, the "
        "animation length in frames and the frames per second (seconds are added when fps is known; fps is null when "
        "this Cascadeur version does not expose it). Use it before switching frames or changing the animation range. "
        "Read-only.",
        "inputSchema": schema(),
        "fn": get_timeline,
    },
    {
        "name": "set_frame",
        "description": "Move the timeline playhead to a frame. Use it before reading or editing the pose at that frame, "
        "or before calling a Timeline action that works on the current frame. Returns the frame the playhead is on "
        "afterwards; a frame outside the animation range is still reached, but keys there are not visible on the "
        "timeline, so widen the range with set_animation_range when the hint says so.",
        "inputSchema": schema({"frame": FRAME_SCHEMA}, ["frame"]),
        "fn": set_frame,
    },
    {
        "name": "set_animation_range",
        "description": "Set the animation's first and/or last frame, widening or shortening the timeline. Use it "
        "before keying frames beyond the current animation range. Pass first, last, or both. Returns the requested "
        "values, the range Cascadeur reports afterwards and the visible frame range, plus the playhead's frame (it can "
        "move when the range shrinks). The range is a scene setting, not model data: Cascadeur's undo does not revert "
        "it (there is no MCP: undo step), so call it again with the old first/last to restore a range; the range is "
        "saved with the scene.",
        "inputSchema": schema({"first": FRAME_SCHEMA, "last": FRAME_SCHEMA}),
        "fn": set_animation_range,
    },
    {
        "name": "set_key",
        "description": "Add keyframes on the given frames for the given controllers, or for the whole character when "
        "controllers is omitted. Use it to pin a pose before interpolation, AI in-betweening or cleanup. frames is one "
        "frame number or a list; controllers accepts controller names, aliases such as left_hand, or partial names "
        "(list_controllers shows them). Returns the frames, the controller names, how many layers were keyed and how "
        "many keys were added or already existed. The whole call is one undoable step named 'MCP: set_key'.",
        "inputSchema": schema({"frames": FRAMES_SCHEMA, "controllers": CONTROLLERS_SCHEMA}, ["frames"]),
        "fn": set_key,
    },
    {
        "name": "delete_key",
        "description": "Remove the keyframes on the given frames from the given controllers, or from the whole "
        "character when controllers is omitted. Use it to clear keys before re-keying, to remove a bad key, or to "
        "shorten an animation. frames is one frame number or a list; controllers accepts controller names, aliases or "
        "partial names. Returns the frames, the controller names and how many keys were removed, plus how many frames "
        "had no key. The whole call is one undoable step named 'MCP: delete_key'.",
        "inputSchema": schema({"frames": FRAMES_SCHEMA, "controllers": CONTROLLERS_SCHEMA}, ["frames"]),
        "fn": delete_key,
    },
    {
        "name": "list_keys",
        "description": "List keyframes for controllers in a frame range: for each controller the key frames, the "
        "interpolation type of the interval each key starts (bezier, linear, step, fixed, clamped_bezier, "
        "low_amplitude_bezier, none, ai) and the interpolation segments, plus the union of key frames. Use it to find "
        "where keys are before editing, or to confirm that an interpolation action changed a range. controllers "
        "defaults to the whole character and then only reports controllers that have keys in the range; pass specific "
        "controllers to list them even when they have none. start/end default to the animation range. Does not change "
        "the scene.",
        "inputSchema": schema(
            {
                "controllers": CONTROLLERS_SCHEMA,
                "start": {
                    "type": ["integer", "string"],
                    "description": "First frame to inspect (default: animation start).",
                },
                "end": {
                    "type": ["integer", "string"],
                    "description": "Last frame to inspect (default: animation end).",
                },
            }
        ),
        "fn": list_keys,
    },
    {
        "name": "select_frames",
        "description": "Select a frame interval on the timeline so Cascadeur's 'Timeline.* on selected interval' "
        "actions (interpolation, IK/FK, cycles) apply to it. Use it before those actions; every timeline layer is "
        "selected by default, or pass controllers to restrict the selection to their layers (for example after "
        "list_controllers). Returns the requested interval, the interval the selector actually reports and how many "
        "layers are selected. Does not change keyframes.",
        "inputSchema": schema(
            {
                "start": {"type": ["integer", "string"], "description": "First frame of the interval (0 or more)."},
                "end": {"type": ["integer", "string"], "description": "Last frame of the interval (start or more)."},
                "controllers": CONTROLLERS_SCHEMA,
            },
            ["start", "end"],
        ),
        "fn": select_frames,
    },
]
