"""Timeline, frame and keyframe helpers (T14).

T16 (pose), T17 (interpolation), T19 (cleanup), T20 (AI inbetweening) and T21 (cycles) all
set keys, read keys or select the timeline's interval, so the mechanics live here instead of
in the tool module:

* parse_frame / parse_frames / interpolation_name and summarize_keys are pure Python and unit
  tested outside Cascadeur (tests/test_timeline.py).
* layer ids, keys, interpolation and the interval selection need Cascadeur's GUI thread; those
  helpers import cascadeur_mcp_plus.common lazily and are meant to be called from a tool fn
  (or from inside a scene.modify/modify_update callback, where the model editor is available).
* require_keyed_range(scene, start=None, end=None) is the shared "does this range have real keyed
  animation?" guard used by T17, T18, T19 and T21. The animation boundary is only the timeline
  range (Cascy is 0-100 with a single key at frame 0), so the guard reads the keyed content and
  returns (start, end) or a common.fail(...) result.

The tool module is cascadeur_mcp_plus/tools/timeline.py.
"""

from __future__ import annotations

from .errors import ToolError

MAX_FRAME = 1_000_000

# csc.layers.layer.Interpolation values (Cascadeur 2026.2.3). Kept here so the pure helpers
# work without importing csc; the enum's own .name is preferred whenever it is available.
INTERPOLATION_NAMES = {
    0: "bezier",
    1: "low_amplitude_bezier",
    2: "linear",
    3: "step",
    4: "fixed",
    5: "none",
    6: "clamped_bezier",
    7: "ai",
}

# ---------------------------------------------------------------------------
# Argument parsing and pure helpers
# ---------------------------------------------------------------------------


def parse_frame(value, parameter: str = "frame") -> int:
    """Return value as a whole frame number, raising ToolError when it is not one."""
    if isinstance(value, bool):
        raise ToolError(f"{parameter} must be a whole frame number, not {value!r}.")
    if isinstance(value, float) and not value.is_integer():
        raise ToolError(f"{parameter} must be a whole frame number, not {value!r}.")
    try:
        number = int(str(value).strip()) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        raise ToolError(f"{parameter} must be a whole frame number, not {value!r}.") from None
    if number < 0:
        raise ToolError(f"{parameter} must be 0 or more (got {number}).")
    if number > MAX_FRAME:
        raise ToolError(f"{parameter} is too large ({number}); the maximum is {MAX_FRAME}.")
    return number


def parse_frames(frames) -> list[int]:
    """Return sorted, unique whole frame numbers from an int, a sequence or "0,10,20"."""
    if frames is None:
        raise ToolError("frames is required: pass a frame number or a list of frame numbers.")
    if isinstance(frames, str):
        parts = [part.strip() for part in frames.replace(";", ",").split(",")]
        values = [parse_frame(part, "frames") for part in parts if part]
    elif isinstance(frames, (list, tuple, set)):
        values = [parse_frame(item, "frames") for item in frames]
    else:
        values = [parse_frame(frames, "frames")]
    if not values:
        raise ToolError("frames must contain at least one frame number.")
    return sorted(set(values))


def interpolation_name(value):
    """Return the lower-case name of a csc.layers.layer.Interpolation value.

    The enum's own .name wins (so a future Cascadeur version's extra value still reads
    correctly); plain ints fall back to INTERPOLATION_NAMES. None stays None.
    """
    if value is None:
        return None
    name = getattr(value, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip().lower()
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value).strip().lower() or None
    return INTERPOLATION_NAMES.get(number, f"unknown({number})")


def summarize_keys(keys, interpolation, start: int, end: int) -> dict:
    """Summarize one layer's keys for frames start..end (inclusive).

    keys is every key position of the layer (frames outside the range are allowed).
    interpolation maps a key position to its interval's interpolation name; the interval that
    starts at a key covers the frames up to the next key, which is what the timeline shows.

    Returns keys (inside the range), interpolation ({frame: name} for those keys) and segments:
    every run of frames start..end that shares one interpolation, each carrying "key": whether
    the run starts on a key. A frame is a key exactly when it appears in "keys"; every other
    frame belongs to the segment that contains it.
    """
    if end < start:
        raise ToolError(f"start ({start}) must not be greater than end ({end}).")
    ordered = sorted({int(frame) for frame in keys or ()})
    key_set = set(ordered)
    names = {int(frame): str(name) for frame, name in (interpolation or {}).items() if name is not None}
    inside = [frame for frame in ordered if start <= frame <= end]

    def name_at(frame: int):
        # The interval that starts at the closest key at or before frame.
        found = None
        for key in ordered:
            if key > frame:
                break
            found = names.get(key)
        return found

    starts = [start] + [frame for frame in inside if frame > start]
    segments = []
    for index, first in enumerate(starts):
        last = starts[index + 1] - 1 if index + 1 < len(starts) else end
        segments.append(
            {
                "first": first,
                "last": last,
                "key": first in key_set,
                "interpolation": name_at(first),
            }
        )
    return {
        "keys": inside,
        "interpolation": {frame: names.get(frame) for frame in inside},
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# Layer lookup (Cascadeur GUI thread)
# ---------------------------------------------------------------------------


def layer_id_of(viewer, object_id):
    """The layer (csc.Guid) that holds an object, or None when it has none."""
    try:
        layer_id = viewer.layer_id_by_obj_id_or_null(object_id)
    except Exception:
        return None
    if layer_id is None:
        return None
    try:
        if layer_id.is_null():
            return None
    except Exception:
        pass
    return layer_id


def _unique(values):
    result = []
    seen = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def layer_ids_for_viewer(viewer, object_ids) -> list:
    """Layer ids for object ids, in order, without duplicates (read path)."""
    result = []
    for object_id in object_ids:
        layer_id = layer_id_of(viewer, object_id)
        if layer_id is not None:
            result.append(layer_id)
    return _unique(result)


def layer_ids_for_editor(model_editor, object_ids) -> list:
    """Layer ids for object ids through the model being edited (inside modify only)."""
    container = model_editor.layers().layers()
    result = []
    for object_id in object_ids:
        try:
            layer_id = container.layer_id_by_obj_id_or_null(object_id)
        except Exception:
            continue
        if layer_id is None:
            continue
        try:
            if layer_id.is_null():
                continue
        except Exception:
            pass
        result.append(layer_id)
    return _unique(result)


def key_frames(layer) -> list[int]:
    """Every key position of a layer, ascending (empty when it has no keys)."""
    try:
        return sorted({int(frame) for frame in layer.key_frame_indices()})
    except Exception:
        return []


def interpolation_by_key(layer) -> dict[int, str]:
    """Map key positions of a layer to the interpolation of the interval that starts there."""
    result = {}
    try:
        sections = layer.sections
    except Exception:
        return result
    try:
        items = sections.items()
    except Exception:
        return result
    for position, section in items:
        try:
            result[int(position)] = interpolation_name(section.interval.interpolation)
        except Exception:
            continue
    return result


# ---------------------------------------------------------------------------
# Editing keys (inside a scene.modify/modify_update callback)
# ---------------------------------------------------------------------------


def set_key_on_layers(model_editor, layer_ids, frame: int) -> dict:
    """Add a key at frame to the given layers; returns how many layers changed.

    set_fixed_interpolation_or_key_if_need is Cascadeur's own key insertion used by its
    import and animation scripts: it creates a fixed key where the interval needs one and
    leaves an existing key alone.
    """
    editor = model_editor.layers_editor()
    changed = 0
    already = 0
    for layer_id in layer_ids:
        try:
            if editor.set_fixed_interpolation_or_key_if_need(layer_id, int(frame), True):
                changed += 1
            else:
                already += 1
        except Exception as exc:  # noqa: BLE001 - report the layer that failed
            raise ToolError(f"Could not set a key at frame {frame} on layer {layer_id}: {exc}") from exc
    return {"layers": len(layer_ids), "changed": changed, "already": already}


def set_key_at(model_editor, object_ids, frame: int) -> dict:
    """Add a key at frame to every layer of object_ids (helper for T16 and later tickets)."""
    return set_key_on_layers(model_editor, layer_ids_for_editor(model_editor, object_ids), frame)


def unset_key_on_layers(model_editor, viewer, layer_ids, frame: int) -> dict:
    """Remove the key at frame from the given layers; returns how many keys were removed."""
    editor = model_editor.layers_editor()
    removed = 0
    missing = 0
    for layer_id in layer_ids:
        try:
            layer = viewer.layer(layer_id)
            is_key = bool(layer.is_key(int(frame)))
        except Exception:
            is_key = False
        if not is_key:
            missing += 1
            continue
        try:
            editor.unset_section(int(frame), layer_id)
            removed += 1
        except Exception as exc:  # noqa: BLE001 - report the layer that failed
            raise ToolError(f"Could not remove the key at frame {frame} on layer {layer_id}: {exc}") from exc
    return {"layers": len(layer_ids), "removed": removed, "missing": missing}


def unset_key_at(model_editor, viewer, object_ids, frame: int) -> dict:
    """Remove the key at frame from every layer of object_ids (helper for T19 and later)."""
    return unset_key_on_layers(model_editor, viewer, layer_ids_for_editor(model_editor, object_ids), frame)


# ---------------------------------------------------------------------------
# What "the whole character" means and object names for results
# ---------------------------------------------------------------------------


def target_object_ids() -> list:
    """Object ids of every controller in the scene, which is what "the whole character" means.

    rig.controllers() returns the control points (main points, direction controllers, boxes,
    joints and other rig objects) of every RigInfo/AnimationInfo character, or every
    controller-type object in a scene without one. A scene with no controllers at all (a
    plain mesh scene) falls back to every object, so set_key still has something to key.
    """
    from . import common, rig

    ids = [entry["id"] for entry in rig.controllers()]
    if ids:
        return ids
    _app, _view_scene, scene = common.scenes()
    return list(scene.model_viewer().get_objects())


def object_names(object_ids) -> list:
    """Names of object ids in order; an object that disappears meanwhile becomes ""."""
    from . import common

    _app, _view_scene, scene = common.scenes()
    model_viewer = scene.model_viewer()
    names = []
    for object_id in object_ids:
        try:
            name = model_viewer.get_object_name(object_id)
        except Exception:
            name = None
        names.append(name or "")
    return names


# ---------------------------------------------------------------------------
# Animation boundary, selection and fps
# ---------------------------------------------------------------------------


def boundary_info(view_scene) -> dict:
    """First/last and visible first/last frames of the active scene."""
    boundary = view_scene.animation_boundary()
    return {
        "first_frame": int(boundary.first_frame),
        "last_frame": int(boundary.last_frame),
        "first_visible_frame": int(boundary.first_visible_frame),
        "last_visible_frame": int(boundary.last_visible_frame),
    }


def _common():
    """The common module, imported lazily so importing timeline does not import csc."""
    from . import common

    return common


def require_keyed_range(scene, start=None, end=None):
    """Validate a frame range against the scene's keyed animation and return it.

    The animation boundary is only the timeline range: Cascy.casc reports 0-100 while only
    frame 0 has a key. Actions such as creating a cycle (T21), changing interpolation or IK/FK
    (T17), AutoPhysics (T18) and cleanup (T19) need real keyed frames, and Cascadeur 2026.2.3 can
    crash when one of them runs on a one-frame animation. This is the shared check for those
    tools; it reads the keyed content, never the cycle structures.

    start/end are frame numbers (or strings parse_frame accepts) or None. A None start defaults
    to the animation boundary's first frame; a None end defaults to the last keyed frame. The
    keyed end is min(layers_viewer().last_key_pos(), data_viewer().get_animation_size() - 1);
    the boundary is not used for that limit.

    Returns (start, end), the resolved inclusive range, when it is usable. Otherwise returns a
    common.fail(...) dict with error/hint (and first_frame/last_frame/animation_size/
    last_key_frame/keyed_last_frame) that the caller returns as its own tool result. A
    single-frame range (start == end) is accepted here because interpolation tools need it;
    requiring at least two different frames is up to the caller.
    """
    common = _common()
    _app, view_scene, _active_scene = common.scenes()
    try:
        boundary = view_scene.animation_boundary()
        boundary_first = int(boundary.first_frame)
        boundary_last = int(boundary.last_frame)
    except Exception as exc:  # noqa: BLE001 - report a scene that cannot be read
        return common.fail(
            f"Could not read the animation range: {type(exc).__name__}: {exc}",
            hint="Make sure an animated scene is open, then retry; get_timeline shows the range.",
        )
    try:
        size = int(scene.model_viewer().data_viewer().get_animation_size())
    except Exception:
        size = None
    if size is not None and size < 0:
        size = None
    try:
        last_key = int(scene.layers_viewer().last_key_pos())
    except Exception:
        last_key = -1
    content_last = last_key if size is None else min(last_key, size - 1)
    limits = {
        "first_frame": boundary_first,
        "last_frame": boundary_last,
        "animation_size": size,
        "last_key_frame": last_key,
        "keyed_last_frame": content_last,
    }
    first = boundary_first if start is None else parse_frame(start, "start")
    last = content_last if end is None else parse_frame(end, "end")
    if first > last:
        return common.fail(
            f"start ({first}) must not be greater than end ({last}).",
            hint="Pass start <= end; a range whose start is past the last keyed frame is empty.",
            **limits,
        )
    if first < boundary_first or last > boundary_last:
        return common.fail(
            f"Frames {first}-{last} are outside the animation range {boundary_first}-{boundary_last}.",
            hint="Widen the timeline with set_animation_range (or get_timeline to read the range), then retry.",
            **limits,
        )
    frames = max(0, last_key + 1)
    if size is not None and frames:
        frames = min(frames, size)
    if frames < 2:
        count = (
            "no keyed frames" if frames == 0 else f"only {frames} frame{'' if frames == 1 else 's'} of keyed animation"
        )
        return common.fail(
            f"The timeline has {count}; at least two keyed frames are needed.",
            hint=(
                "Key the frames you want to use first (set_key with frames such as 0 and 30), or open an "
                "animated scene such as Dracorex_walk_cycle.casc. Cascadeur 2026.2.3 can crash when an "
                "action runs on a one-frame animation."
            ),
            **limits,
        )
    if last > content_last:
        return common.fail(
            f"Frames {first}-{last} go past the keyed animation (frames 0-{content_last}).",
            hint=f"Pass an end frame of {content_last} or less, or key more frames with set_key first.",
            **limits,
        )
    return first, last


def select_interval(start: int, end: int, layer_ids=None) -> dict:
    """Select frames start..end on the timeline for layer_ids (all layers when None).

    The selection is what Cascadeur's "Timeline.* on selected interval" actions operate on.
    Returns the interval the selector reports afterwards and the number of layers selected.
    """
    from . import common

    _app, _view_scene, scene = common.scenes()
    viewer = scene.layers_viewer()
    if layer_ids is None:
        ids = [layer_id for layer_id in viewer.all_layer_ids() if not _is_null(layer_id)]
    else:
        ids = [layer_id for layer_id in layer_ids if not _is_null(layer_id)]
    if not ids:
        raise ToolError(
            "Nothing to select: the scene has no timeline layers. Open an animated scene "
            "(for example Cascadeur's Cascy.casc sample) and try again."
        )
    selector = scene.get_layers_selector()
    selector.set_full_selection_by_parts(ids, int(start), int(end))
    interval = selector.selection().frames_interval()
    return {"first": int(interval.first), "last": int(interval.last), "layers": len(ids)}


def _is_null(value) -> bool:
    try:
        return bool(value.is_null())
    except Exception:
        return value is None


def scene_fps(view_scene):
    """The scene's playback frame rate, or None when Cascadeur does not report one.

    Cascadeur 2026.2.3 keeps the timeline FPS in the scene settings: the QML control writes
    Scene/Fps and get_setting_handler().get_float_value("Scene", "Fps") reads it (30.0 on the
    bundled Cascy sample; the other guessed group/key combinations and the QML tree report 0
    or nothing). This is the value T16/T18 need for 980 / fps**2 and T32 for video timing.
    """
    try:
        number = float(view_scene.get_setting_handler().get_float_value("Scene", "Fps"))
    except Exception:
        return None
    return number if 0.0 < number < 1000.0 else None
