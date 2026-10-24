"""Foot-slide and collision cleanup through Cascadeur's own cleaning tools (T19).

Cascadeur 2026.2.3 ships three cleaning commands that operate on the selected character and
the selected timeline interval:

* View.FixFoot - Fulcrum Motion Cleaning: removes the horizontal sliding of the fulcrum
  (contact) points while they stay on the ground.
* View.FixCollisions - Collision Penetration Cleaning: pushes colliding bodies out of each
  other (floor, kinematic and dynamic collisions are separate settings).
* View.AutoInterpolation_Keys - Auto keys: prepares/rebuilds keyframes from the fulcrum
  motion, so the cleaned contact motion is represented by fewer, better-placed keys.

All three can take a long time, so each tool is a T03 background job: the call returns a
job_id immediately and get_job reports progress and the measured before/after values.
Nothing in this module sleeps or blocks Cascadeur's GUI thread: fn validates and starts the
job, poll only reads state, answers dialogs and finally measures the result.

The tools measure their own effect with csc.tools.FulcrumCollisionInfo (the same class
Cascadeur's bundled ml/editable_animation.py uses):

* fulcrum points per frame -> the summed horizontal movement of a point between two frames
  in which it is still a fulcrum (the foot-slide metric);
* get_frame_collision_info_points() -> the penetration depth of every colliding point
  (the collision metric).

The cleaning actions themselves are Cascadeur commands, so the undo step carries
Cascadeur's own name instead of the "MCP: " prefix (same as T18's physics_snap and T21's
cycle actions). The description says so and the result lists the command names found in
Cascadeur's log.

The user's layer lock is enforced before anything is written: ensure_unlocked() from T23 is
called with the resolved control points, because Cascadeur's Python editor API does not
enforce layer locks on its own.
"""

from __future__ import annotations

import functools
import math
import re
import sys
import time
import traceback

from .. import common, rig, timeline
from ..errors import ToolError
from ._schema import schema

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACTION_FIX_FOOT = "View.FixFoot"
ACTION_FIX_COLLISIONS = "View.FixCollisions"
ACTION_PREPARE_KEYS = "View.AutoInterpolation_Keys"

FIX_FOOT = "fix_foot_sliding"
FIX_COLLISIONS = "fix_collisions"
PREPARE_KEYS = "prepare_keys_by_fulcrums"
KINDS = (FIX_FOOT, FIX_COLLISIONS, PREPARE_KEYS)

ACTIONS = {
    FIX_FOOT: ACTION_FIX_FOOT,
    FIX_COLLISIONS: ACTION_FIX_COLLISIONS,
    PREPARE_KEYS: ACTION_PREPARE_KEYS,
}

TITLES = {
    FIX_FOOT: "Fulcrum Motion Cleaning",
    FIX_COLLISIONS: "Collision Penetration Cleaning",
    PREPARE_KEYS: "Auto keys",
}

STATE_KEY = "_cascadeur_mcp_plus_cleanup"
MAX_LISTED_POINTS = 40
MAX_LISTED_FRAMES = 200
MAX_TRANSITIONS = 5000
PROBE_OBJECTS = 8
PROBE_FRAMES = 6
STABLE_POLLS = 8
SETTLE_SECONDS = 1.0
POST_COMPLETE_SECONDS = 0.6
NO_CHANGE_SECONDS = 90.0

# The cleaning tools reject ranges that are too short ("Interval is less than 3 frames").
MIN_CLEAN_FRAMES = 3

FOOT_DONE = re.compile(r"Fulcrum motion cleaning completed from (\d+) to (\d+) frames", re.IGNORECASE)
COLLISION_DONE = re.compile(r"Collision cleaning completed from (\d+) to (\d+) frames", re.IGNORECASE)
AUTO_KEYS_DONE = re.compile(r"Auto keys:\s*done", re.IGNORECASE)
ANY_DONE = re.compile(r"cleaning completed from (\d+) to (\d+) frames", re.IGNORECASE)

FOOT_ERRORS = (
    "fulcrum motion cleaning failed",
    "interval is less than 3 frames",
    "actual interval is less than 3 frames",
    "geometric filtration failed",
    "no touch points found",
    "no local coordinate system found",
    "no points for foot cm calculation",
    "wrong frame indices for fix foot",
    "empty intersection for rotation point projection",
)
COLLISION_ERRORS = (
    "collision cleaning failed",
    "fixcollisions: no centerofmass",
    "no center of mass found",
    "interval is less than 3 frames",
)
PREPARE_ERRORS = (
    "action 'autokeys' failed",
    "autointerpolation torchmodel error",
    "there are several models selected",
    "there are several models;",
    "no center of mass found",
    "there are several centers of mass",
)

ERROR_MARKERS = {
    FIX_FOOT: FOOT_ERRORS,
    FIX_COLLISIONS: COLLISION_ERRORS,
    PREPARE_KEYS: PREPARE_ERRORS,
}

SAFE_CLOSE_LABELS = ("ok", "close", "cancel")
SAFE_YES_LABELS = ("yes", "add", "ok")
SAFE_RUN_LABELS = ("run", "apply", "start")
UNSAFE_BUTTON_LABELS = (
    "don't save",
    "dont save",
    "discard",
    "discard changes",
    "save",
    "save as",
    "save as...",
)

# Only these phrases identify a dialog that the cleaning tools themselves opened. Generic words
# such as "clean" or "collision" must not be used: a scene named "walk_clean" produces a
# "Closing scene" prompt whose text contains "clean", and that prompt must never be answered here.
UNSAFE_DIALOG_MARKERS = (
    "closing scene",
    "save changes",
    "save your changes",
    "unsaved changes",
    "do you want to save",
)

CLEANUP_DIALOG_MARKERS = (
    "fulcrum motion cleaning",
    "collision penetration cleaning",
    "fix foot",
    "fixcollisions",
    "auto keys",
    "autokeys",
    "auto interpolation",
    "interval is less than 3 frames",
    "geometric filtration failed",
    "no touch points found",
    "no center of mass found",
    "no local coordinate system found",
    "no points for foot cm calculation",
)

# A job's busy state is released when it cannot be shown to belong to a running T03 job. The
# binding runs on the first poll (jobs.start_job creates the id after begin() returns).
JOB_BIND_GRACE_SECONDS = 5.0
STATE_STALE_SECONDS = 120.0
STATE_MAX_AGE_SECONDS = 3600.0
JOB_START_MATCH_SECONDS = 60.0


class _BadArgument(ToolError):
    """A tool argument is invalid; carries the hint that belongs in the result."""

    def __init__(self, message, hint):
        super().__init__(message)
        self.hint = hint


def _tool(hint):
    """Turn exceptions into the shared {"ok": false, "error", "hint"} result."""

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
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


# ---------------------------------------------------------------------------
# Pure helpers (unit tested without Cascadeur)
# ---------------------------------------------------------------------------


def minimum_frame_count(kind: str) -> int:
    """Frames a cleanup action needs: three for cleaning, two for Auto keys."""
    return MIN_CLEAN_FRAMES if kind in (FIX_FOOT, FIX_COLLISIONS) else 2


def minimum_range_error(first: int, last: int, kind: str) -> str | None:
    """Return the "range too short" error for a resolved range, or None when it is long enough."""
    if first > last:
        return f"start ({first}) must not be greater than end ({last})."
    minimum = minimum_frame_count(kind)
    if last - first + 1 < minimum:
        return f"The range {first}-{last} is only {last - first + 1} frame(s); {TITLES[kind]} needs at least {minimum}."
    return None


def parse_controllers(controllers):
    """Return None (whole character) or a non-empty list of controller names."""
    if controllers is None:
        return None
    if isinstance(controllers, str):
        values = [controllers]
    elif isinstance(controllers, (list, tuple)):
        values = list(controllers)
    else:
        raise _BadArgument(
            "controllers must be a control point name, alias, or a list of them.",
            "Pass for example controllers=['left_foot', 'right_foot'], or omit it for the whole character.",
        )
    if not values:
        raise _BadArgument(
            "controllers must not be an empty list.",
            "Omit controllers to target the whole character.",
        )
    cleaned = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise _BadArgument(
                "controllers must contain only non-empty strings.",
                "Use a control point name or alias such as 'left_foot'.",
            )
        cleaned.append(value.strip())
    return cleaned


def _vector(value):
    """A numeric list for a position/rotation value, or None."""
    if value is None:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _position(value):
    """A three-element position list, or None when the value is not a position."""
    numbers = _vector(value)
    if numbers is None or len(numbers) < 3:
        return None
    return numbers[:3]


def horizontal_distance(one, other) -> float:
    """Distance in cm between two positions ignoring the vertical axis (Cascadeur: Y is up)."""
    return math.hypot(float(one[0]) - float(other[0]), float(one[2]) - float(other[2]))


def slide_summary(points_by_frame, positions, first: int, last: int) -> dict:
    """Sum the horizontal movement of fulcrum points that stay in contact between frames.

    points_by_frame maps a frame to the point keys that are fulcrums on that frame.
    positions maps a point key to {frame: [x, y, z]}. Only pairs of consecutive frames where
    the same point is a fulcrum in both are counted: that is exactly the contact slide the
    Fulcrum Motion Cleaning action removes. A perfectly planted foot has a total near 0 cm.
    """
    totals = {}
    transitions = 0
    frames = set()
    pairs = []
    for frame in range(int(first), int(last)):
        current = set(points_by_frame.get(frame, ()))
        following = set(points_by_frame.get(frame + 1, ()))
        for key in current & following:
            one = positions.get(key, {}).get(frame)
            other = positions.get(key, {}).get(frame + 1)
            one = _position(one)
            other = _position(other)
            if one is None or other is None:
                continue
            distance = horizontal_distance(one, other)
            totals[key] = totals.get(key, 0.0) + distance
            transitions += 1
            frames.add(frame)
            if len(pairs) < MAX_TRANSITIONS:
                pairs.append({"point": key, "frame": frame, "slide_cm": round(distance, 6)})
    total = sum(totals.values())
    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return {
        "pairs": pairs,
        "truncated_pairs": transitions > MAX_TRANSITIONS,
        "slide_total_cm": round(total, 6),
        "slide_max_cm": round(ordered[0][1], 6) if ordered else 0.0,
        "contact_transitions": transitions,
        "contact_frames": len(frames),
        "points": [{"point": key, "slide_cm": round(value, 6)} for key, value in ordered[:MAX_LISTED_POINTS]],
        "point_count": len(ordered),
        "truncated": len(ordered) > MAX_LISTED_POINTS,
    }


def collision_summary(frame_infos) -> dict:
    """Summarize {frame: {point key: penetration depth cm}} from FulcrumCollisionInfo."""
    total = 0.0
    maximum = 0.0
    maximum_frame = None
    maximum_point = None
    frames = []
    points = set()
    detail = []
    point_depths = []
    for frame in sorted(frame_infos or {}):
        entries = frame_infos.get(frame) or {}
        if not entries:
            continue
        frame_total = 0.0
        for key, depth in entries.items():
            value = abs(float(depth))
            frame_total += value
            total += value
            points.add(key)
            if len(point_depths) < MAX_TRANSITIONS:
                point_depths.append({"frame": int(frame), "point": key, "depth_cm": round(value, 6)})
            if value > maximum:
                maximum = value
                maximum_frame = int(frame)
                maximum_point = key
        frames.append(int(frame))
        detail.append(
            {
                "frame": int(frame),
                "depth_cm": round(frame_total, 6),
                "points": len(entries),
            }
        )
    return {
        "point_depths": point_depths,
        "truncated_points": len(points) > MAX_TRANSITIONS,
        "penetration_total_cm": round(total, 6),
        "penetration_max_cm": round(maximum, 6),
        "max_depth_frame": maximum_frame,
        "max_depth_point": maximum_point,
        "colliding_frames": len(frames),
        "colliding_points": len(points),
        "frames": frames[:MAX_LISTED_FRAMES],
        "frame_detail": detail[:MAX_LISTED_FRAMES],
        "truncated_frames": len(frames) > MAX_LISTED_FRAMES,
    }


def total_delta(before, after, key: str) -> float:
    """before - after for a summary field, rounded; positive means the metric improved."""
    try:
        return round(float(before.get(key, 0.0)) - float(after.get(key, 0.0)), 6)
    except (TypeError, ValueError):
        return 0.0


def reduction_percent(before, after, key: str):
    """Percentage decrease of a metric, or None when the before value is zero."""
    try:
        base = float(before.get(key, 0.0))
        current = float(after.get(key, 0.0))
    except (TypeError, ValueError):
        return None
    if base <= 0.0:
        return None
    return round((base - current) / base * 100.0, 3)


def restrict_slide(before, after) -> dict:
    """Compare the after slide on exactly the point/frame pairs the before measurement saw.

    Cleaning usually teaches Cascadeur about more contact points, so the full after total is
    not comparable with the before total. Restricting to the original pairs answers the real
    question: did the movement of the points that were already in contact get smaller?
    """
    pairs = before.get("pairs") or []
    current = {(item["point"], item["frame"]): float(item["slide_cm"]) for item in after.get("pairs") or []}
    total = 0.0
    maximum = 0.0
    matched = 0
    for item in pairs:
        key = (item["point"], item["frame"])
        if key not in current:
            continue
        value = current[key]
        total += value
        maximum = max(maximum, value)
        matched += 1
    return {
        "slide_total_cm": round(total, 6),
        "slide_max_cm": round(maximum, 6),
        "contact_transitions": matched,
        "contact_frames": len({item["frame"] for item in pairs if (item["point"], item["frame"]) in current}),
        "matched_pairs": matched,
        "before_pairs": len(pairs),
        "after_truncated_pairs": bool(after.get("truncated_pairs")),
    }


def restrict_collisions(before, after) -> dict:
    """Compare the after penetration on exactly the point/frame pairs the before saw."""
    pairs = before.get("point_depths") or []
    current = {(item["frame"], item["point"]): float(item["depth_cm"]) for item in after.get("point_depths") or []}
    total = 0.0
    maximum = 0.0
    matched = 0
    for item in pairs:
        key = (item["frame"], item["point"])
        value = current.get(key, 0.0)
        total += value
        maximum = max(maximum, value)
        matched += 1
    return {
        "penetration_total_cm": round(total, 6),
        "penetration_max_cm": round(maximum, 6),
        "colliding_frames": len({item["frame"] for item in pairs}),
        "colliding_points": len({item["point"] for item in pairs}),
        "matched_pairs": matched,
        "before_pairs": len(pairs),
        "after_truncated_points": bool(after.get("truncated_points")),
    }


def key_summary(layers, first: int, last: int) -> dict:
    """Summarize keys and interpolation of layers (layers: [(name, keys, interpolation)])."""
    total = 0
    frames = set()
    interpolation = {}
    with_keys = 0
    details = []
    for name, keys, mapping in layers:
        inside = sorted({int(frame) for frame in keys if int(first) <= int(frame) <= int(last)})
        total += len(inside)
        frames.update(inside)
        if inside:
            with_keys += 1
        for frame in inside:
            value = (mapping or {}).get(frame)
            if value:
                interpolation[value] = interpolation.get(value, 0) + 1
        details.append({"layer": name, "keys": len(inside), "key_frames": inside[:MAX_LISTED_FRAMES]})
    return {
        "key_count": total,
        "layers_with_keys": with_keys,
        "key_frames": sorted(frames)[:MAX_LISTED_FRAMES],
        "key_frame_count": len(frames),
        "interpolation": interpolation,
        "layers": details[:MAX_LISTED_POINTS],
        "truncated_layers": len(details) > MAX_LISTED_POINTS,
    }


def parse_log(lines, kind: str) -> dict:
    """Classify the new log lines: completion range, errors and Cascadeur commands."""
    text_lines = [str(line) for line in lines or ()]
    joined = "\n".join(text_lines).lower()
    completed = None
    patterns = (
        (AUTO_KEYS_DONE,) if kind == PREPARE_KEYS else (FOOT_DONE if kind == FIX_FOOT else COLLISION_DONE, ANY_DONE)
    )
    for pattern in patterns:
        for line in text_lines:
            match = pattern.search(line)
            if match:
                groups = match.groups()
                completed = (
                    {"first": int(groups[0]), "last": int(groups[1])}
                    if len(groups) >= 2
                    else {"first": None, "last": None, "message": line.strip()}
                )
                break
        if completed:
            break
    errors = []
    for marker in ERROR_MARKERS.get(kind, ()):
        if marker in joined:
            errors.extend(line for line in text_lines if marker in line.lower())
    commands = []
    for line in text_lines:
        marker = "Command: "
        if marker in line:
            commands.append(line.split(marker, 1)[1].strip())
    return {"completed": completed, "errors": _unique(errors), "commands": _unique(commands)}


def _unique(values):
    result = []
    seen = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


# ---------------------------------------------------------------------------
# Cascadeur-side helpers (GUI thread)
# ---------------------------------------------------------------------------


def _scene():
    _app, _view_scene, scene = common.scenes()
    return scene


def _is_null(value) -> bool:
    try:
        return bool(value.is_null())
    except Exception:
        return value is None


def _key(object_id) -> str:
    try:
        return object_id.to_string()
    except Exception:
        return str(object_id)


def _object_names(object_ids) -> list[str]:
    try:
        viewer = _scene().model_viewer()
    except Exception:
        return [_key(object_id) for object_id in object_ids]
    names = []
    for object_id in object_ids:
        try:
            names.append(str(viewer.get_object_name(object_id)))
        except Exception:
            names.append(_key(object_id))
    return names


def _resolve_controllers(controllers):
    """Return (object ids, display names, explicit) for the controllers argument."""
    if controllers is None:
        ids = timeline.target_object_ids()
        return ids, _object_names(ids), False
    try:
        ids = rig.resolve(controllers)
    except rig.RigError as exc:
        return None, exc.result(), True
    if not ids:
        return (
            None,
            common.fail(
                f"No control points matched {controllers!r}.",
                hint="Call list_controllers to see the names and aliases, then retry.",
            ),
            True,
        )
    return ids, _object_names(ids), True


def _ensure_unlocked(object_ids):
    """Call T23's guard when it is available; return its failure or None."""
    try:
        from .layers import ensure_unlocked
    except Exception:
        return None
    try:
        return ensure_unlocked(object_ids)
    except Exception as exc:  # noqa: BLE001 - a guard failure must not crash the tool
        return common.fail(
            f"Could not check the layer lock state: {type(exc).__name__}: {exc}",
            hint="Unlock the layers manually or check get_log, then retry.",
        )


def _select_objects(object_ids) -> dict:
    """Select the objects the cleaning tool should work on (T13's direct selector route)."""
    if not object_ids:
        return {"selected": 0}
    scene = _scene()
    selector = scene.selector()
    selector.select(set(object_ids), object_ids[0])
    return {"selected": len(object_ids)}


def _null_object_id():
    try:
        import csc

        return csc.model.ObjectId.null()
    except Exception:
        return None


def _object_selection(scene) -> list:
    """The selected objects in interface order (empty when the selector cannot be read)."""
    try:
        selector = scene.selector()
        selection = selector.selected()
        try:
            ordered = list(selection.ordered_ids)
        except Exception:
            ordered = []
        return ordered or list(selection.ids)
    except Exception:
        return []


def _timeline_selection(scene) -> dict:
    """The selected timeline layers and frame interval (empty/None when unreadable)."""
    try:
        container = scene.get_layers_selector().selection()
    except Exception:
        return {"timeline_items": [], "timeline_first": None, "timeline_last": None}
    try:
        items = list(container.item_ids())
    except Exception:
        items = []
    try:
        interval = container.frames_interval()
        first, last = int(interval.first), int(interval.last)
    except Exception:
        first, last = None, None
    return {"timeline_items": items, "timeline_first": first, "timeline_last": last}


def capture_selection(scene) -> dict:
    """Snapshot the object selection and the timeline layer/frame selection before a cleaning job."""
    return {"objects": _object_selection(scene), **_timeline_selection(scene)}


def restore_selection(scene, snapshot) -> bool:
    """Put the object and timeline selection back; returns True when both parts were applied.

    The timeline selector only accepts a layer list plus a frame interval, so the original layer
    set and interval are restored with T14's set_full_selection_by_parts(). Selection changes are
    not undo steps, so this never touches Cascadeur's undo stack.
    """
    if not isinstance(snapshot, dict):
        return False
    applied = True
    objects = list(snapshot.get("objects") or [])
    try:
        scene.selector().select(set(objects), objects[0] if objects else _null_object_id())
    except Exception:
        applied = False
    items = list(snapshot.get("timeline_items") or [])
    first = snapshot.get("timeline_first")
    last = snapshot.get("timeline_last")
    if items and first is not None and last is not None and int(first) <= int(last):
        try:
            scene.get_layers_selector().set_full_selection_by_parts(items, int(first), int(last))
        except Exception:
            applied = False
    return applied


def _restore_state_selection(state) -> None:
    """Restore the selection stored on a job state, once; never let a restore error escape."""
    snapshot = state.get("selection_snapshot")
    if not snapshot:
        return
    state["selection_snapshot"] = None
    try:
        _app, _view_scene, scene = common.scenes()
        restore_selection(scene, snapshot)
    except Exception:
        pass


def _target_layers(viewer, object_ids):
    """Return (layer ids, used_all_layers) for the objects the action should cover.

    A control point with no layer makes Cascadeur's action fall back to every timeline layer, so
    the caller must then check the lock state of every layer rather than only of the control
    points (see _start_job).
    """
    layer_ids = timeline.layer_ids_for_viewer(viewer, object_ids) if object_ids else []
    if layer_ids:
        return layer_ids, False
    return [layer_id for layer_id in viewer.all_layer_ids() if not _is_null(layer_id)], True


def _layer_object_ids(viewer, layer_ids) -> list:
    """Every object id on the given layers, deduplicated (safe read path)."""
    result = []
    seen = set()
    for layer_id in layer_ids or ():
        try:
            object_ids = list(viewer.layer(layer_id).obj_ids)
        except Exception:
            continue
        for object_id in object_ids:
            if object_id not in seen:
                seen.add(object_id)
                result.append(object_id)
    return result


def _select_range(first: int, last: int, layer_ids) -> dict:
    """Select the frame range on the given timeline layers."""
    selected = timeline.select_interval(int(first), int(last), layer_ids)
    return selected


def _first_center_of_mass():
    """(behaviour, object id, count) of the scene's CenterOfMass, or (None, None, count)."""
    scene = _scene()
    behaviour_viewer = scene.model_viewer().behaviour_viewer()
    behaviours = list(behaviour_viewer.get_behaviours("CenterOfMass"))
    if not behaviours:
        return None, None, 0
    behaviour = behaviours[0]
    owner = None
    try:
        owner = behaviour_viewer.get_behaviour_owner(behaviour)
    except Exception:
        owner = None
    return behaviour, owner, len(behaviours)


def _centre_of_mass_refs(behaviour):
    scene = _scene()
    behaviour_viewer = scene.model_viewer().behaviour_viewer()
    return list(behaviour_viewer.get_behaviour_reference_range(behaviour, "rigid_bodies"))


def _fulcrum_info(first: int, last: int):
    """Construct Cascadeur's FulcrumCollisionInfo for the scene, or raise ToolError."""
    import csc

    scene = _scene()
    behaviour, owner, count = _first_center_of_mass()
    if behaviour is None:
        raise ToolError(
            "The scene has no CenterOfMass behaviour, so Cascadeur's fulcrum/collision information is unavailable."
        )
    if count > 1:
        raise ToolError(
            f"The scene has {count} CenterOfMass behaviours (several characters); "
            "cleaning needs exactly one character with a Center of Mass."
        )
    refs = _centre_of_mass_refs(behaviour)
    static_points = csc.tools.StaticPointsTypes(scene, refs)
    info = csc.tools.FulcrumCollisionInfo(int(first), int(last), scene, static_points)
    return info, behaviour, owner


def _position_data_ids(object_ids):
    """{point key: global_position data id} for the given ObjectIds."""
    scene = _scene()
    behaviour_viewer = scene.model_viewer().behaviour_viewer()
    result = {}
    for object_id in object_ids:
        key = _key(object_id)
        try:
            behaviour = behaviour_viewer.get_behaviour_by_name(object_id, "Transform")
            if _is_null(behaviour):
                continue
            data_id = behaviour_viewer.get_behaviour_data(behaviour, "global_position")
            if data_id is None or _is_null(data_id):
                continue
            result[key] = data_id
        except Exception:
            continue
    return result


def _read_positions(data_ids, frames):
    """{point key: {frame: [x, y, z]}} for the requested frames."""
    scene = _scene()
    data_viewer = scene.model_viewer().data_viewer()
    positions = {}
    for key, data_id in data_ids.items():
        per_frame = {}
        for frame in frames:
            try:
                value = _position(data_viewer.get_data_value(data_id, int(frame)))
            except Exception:
                value = None
            if value is not None:
                per_frame[int(frame)] = value
        positions[key] = per_frame
    return positions


def measure_slide(first: int, last: int) -> dict:
    """Measure the contact slide in first..last with FulcrumCollisionInfo (GUI thread)."""
    info, behaviour, owner = _fulcrum_info(first, last)
    raw = info.get_fulcrum_points() or {}
    points_by_frame = {}
    ids_by_key = {}
    for frame, object_ids in raw.items():
        frame = int(frame)
        if not (int(first) <= frame <= int(last)):
            continue
        keys = []
        for object_id in object_ids:
            key = _key(object_id)
            keys.append(key)
            ids_by_key.setdefault(key, object_id)
        if keys:
            points_by_frame[frame] = sorted(keys)
    all_frames = sorted(points_by_frame)
    positions = _read_positions(_position_data_ids(ids_by_key.values()), all_frames)
    summary = slide_summary(points_by_frame, positions, first, last)
    summary["center_of_mass"] = _object_names([owner])[0] if owner is not None else None
    summary["fulcrum_frames"] = len(all_frames)
    summary["point_names"] = {key: _object_names([ids_by_key[key]])[0] for key in list(ids_by_key)[:MAX_LISTED_POINTS]}
    return summary


def measure_collisions(first: int, last: int) -> dict:
    """Measure collision penetration in first..last with FulcrumCollisionInfo (GUI thread)."""
    info, behaviour, owner = _fulcrum_info(first, last)
    raw = info.get_frame_collision_info_points() or {}
    frame_infos = {}
    names = {}
    for frame, entries in raw.items():
        frame = int(frame)
        if not (int(first) <= frame <= int(last)):
            continue
        values = {}
        for object_id, collision in (entries or {}).items():
            key = _key(object_id)
            try:
                depth = float(collision.penetration_depth)
            except Exception:
                continue
            values[key] = depth
            if key not in names:
                names[key] = _object_names([object_id])[0]
        if values:
            frame_infos[frame] = values
    summary = collision_summary(frame_infos)
    summary["center_of_mass"] = _object_names([owner])[0] if owner is not None else None
    summary["point_names"] = names
    return summary


def _all_layers_key_summary(first: int, last: int) -> dict:
    """Key/interpolation summary of every timeline layer in first..last."""
    scene = _scene()
    viewer = scene.layers_viewer()
    model_viewer = scene.model_viewer()
    layers = []
    for layer_id in viewer.all_layer_ids():
        if _is_null(layer_id):
            continue
        try:
            layer = viewer.layer(layer_id)
        except Exception:
            continue
        try:
            name = str(viewer.header(layer_id).name)
        except Exception:
            try:
                name = str(model_viewer.get_object_name(layer_id))
            except Exception:
                name = _key(layer_id)
        layers.append((name, timeline.key_frames(layer), timeline.interpolation_by_key(layer)))
    return key_summary(layers, first, last)


def _pose_snapshot(object_ids, frames):
    """A cheap sample of global positions for change detection (not the metric)."""
    data_ids = _position_data_ids(object_ids[:PROBE_OBJECTS])
    if not data_ids:
        return {}
    return _read_positions(data_ids, frames)


def _key_probe(first: int, last: int) -> dict:
    """A compact key/interpolation snapshot for change detection (T20-style Auto keys)."""
    summary = _all_layers_key_summary(first, last)
    return {
        "key_count": summary.get("key_count"),
        "key_frames": (summary.get("key_frames") or [])[: PROBE_FRAMES * 4],
        "interpolation": summary.get("interpolation") or {},
        "layers_with_keys": summary.get("layers_with_keys"),
    }


def _probe(state) -> dict:
    if state["kind"] == PREPARE_KEYS:
        return _key_probe(state["first"], state["last"])
    return _pose_snapshot(state["object_ids"], state["probe_frames"])


def _probe_changed(state, probe, before) -> bool:
    if state["kind"] == PREPARE_KEYS:
        return probe != before
    return not _same_snapshot(probe, before)


def _probe_stable(state, probe, last) -> bool:
    if state["kind"] == PREPARE_KEYS:
        return probe == last
    return _same_snapshot(probe, last)


def _same_snapshot(one, other) -> bool:
    if one == other:
        return True
    if not one or not other:
        return False
    keys = set(one) & set(other)
    if not keys:
        return False
    for key in keys:
        for frame in set(one[key]) & set(other[key]):
            a = one[key][frame]
            b = other[key][frame]
            if any(abs(float(x) - float(y)) > 1e-4 for x, y in zip(a, b, strict=False)):
                return False
    return True


def _probe_frames(first: int, last: int) -> list[int]:
    if last <= first:
        return [first]
    span = last - first
    if span <= PROBE_FRAMES:
        return list(range(first, last + 1))
    return sorted({first + round(span * index / (PROBE_FRAMES - 1)) for index in range(PROBE_FRAMES)})


# ---------------------------------------------------------------------------
# UI helpers (dialogs and the tool's settings panel)
# ---------------------------------------------------------------------------


def _ui():
    from .. import ui

    return ui


def _dialogs():
    try:
        return list(_ui().dialogs().get("dialogs") or [])
    except Exception:
        return []


def _dialog_text(dialog) -> str:
    parts = list(dialog.get("texts") or [])
    parts.extend(button.get("text", "") for button in dialog.get("buttons") or [])
    return " ".join(part for part in parts if part)


def _click_dialog(dialog, labels) -> str | None:
    wanted = tuple(label.lower() for label in labels)
    for button in dialog.get("buttons") or []:
        label = (button.get("text") or "").strip()
        if label.lower() in wanted:
            try:
                _ui().click(ref=button["ref"])
            except Exception:
                continue
            return label
    return None


def _click_labeled(dialog, labels) -> str | None:
    """Click a dialog button when its label contains one of the labels."""
    wanted = tuple(label.lower() for label in labels)
    for button in dialog.get("buttons") or []:
        label = (button.get("text") or "").strip()
        lowered = label.lower()
        if any(needle in lowered for needle in wanted):
            try:
                _ui().click(ref=button["ref"])
            except Exception:
                continue
            return label
    return None


def _dialog_failure(kind: str, text: str) -> str | None:
    lowered = text.lower()
    for marker in ERROR_MARKERS.get(kind, ()):
        if marker in lowered:
            return text
    return None


def _dialog_signature(dialog) -> tuple:
    """A stable identity for a dialog, used to tell pre-existing dialogs from new ones."""
    return (
        str(dialog.get("kind") or ""),
        str(dialog.get("type") or ""),
        tuple(str(item) for item in dialog.get("texts") or ()),
        tuple(str(button.get("text") or "").strip().lower() for button in dialog.get("buttons") or ()),
    )


def _dialog_signatures() -> list:
    return [_dialog_signature(dialog) for dialog in _dialogs()]


def _is_new_dialog(state, dialog) -> bool:
    before = state.get("dialogs_before") or []
    return _dialog_signature(dialog) not in before


def _dialog_already_clicked(state, dialog) -> bool:
    return _dialog_signature(dialog) in (state.get("clicked_dialogs") or [])


def _mark_dialog_clicked(state, dialog) -> None:
    state.setdefault("clicked_dialogs", []).append(_dialog_signature(dialog))


def _is_cleanup_dialog(dialog) -> bool:
    """True only for a dialog created by Cascadeur's cleaning tools themselves.

    The test is deliberately narrow: generic words such as "clean" or "collision" also occur in
    the user's scene names and in the "Closing scene" prompt, and that prompt must not be touched.
    """
    text = _dialog_text(dialog).lower()
    if any(marker in text for marker in UNSAFE_DIALOG_MARKERS):
        return False
    return any(marker in text for marker in CLEANUP_DIALOG_MARKERS)


def _is_unsafe_label(label: str) -> bool:
    lowered = label.strip().lower()
    if lowered in UNSAFE_BUTTON_LABELS:
        return True
    return any(word in lowered for word in ("discard", "don't save", "dont save", "save"))


def _safe_click(dialog, labels) -> str | None:
    """Click a button whose label exactly matches one of labels, never a Save/Discard one."""
    wanted = {str(label).strip().lower() for label in labels}
    for button in dialog.get("buttons") or []:
        label = (button.get("text") or "").strip()
        if not label or _is_unsafe_label(label):
            continue
        if label.lower() not in wanted:
            continue
        try:
            _ui().click(ref=button["ref"])
        except Exception:
            continue
        return label
    return None


def _handle_dialogs(state) -> None:
    """Answer a cleaning tool dialog that appeared during this job; store clicks/failures."""
    dialogs = _dialogs()
    state["dialogs_open"] = bool(dialogs)
    for dialog in dialogs:
        if dialog.get("kind") != "dialog":
            continue
        if not _is_new_dialog(state, dialog) or not _is_cleanup_dialog(dialog):
            continue
        if _dialog_already_clicked(state, dialog):
            continue
        text = _dialog_text(dialog)
        failure = _dialog_failure(state["kind"], text)
        if failure:
            state["error"] = failure
            label = _safe_click(dialog, SAFE_CLOSE_LABELS)
            if label:
                state.setdefault("dialog_actions", []).append(label)
                _mark_dialog_clicked(state, dialog)
            continue
        lowered = text.lower()
        if "missing a keyframe" in lowered or "would you like to add" in lowered:
            label = _safe_click(dialog, SAFE_YES_LABELS)
            if label:
                state.setdefault("dialog_actions", []).append(label)
                _mark_dialog_clicked(state, dialog)
            continue
        if any(word in lowered for word in ("run", "apply", "start")):
            label = _safe_click(dialog, SAFE_RUN_LABELS)
            if label:
                state.setdefault("dialog_actions", []).append(label)
                _mark_dialog_clicked(state, dialog)
            continue


def _close_ui(state) -> list[str]:
    """Close only cleaning-tool dialogs that appeared during this job; return all left open.

    Pre-existing dialogs and any dialog without a cleaning-specific phrase are left untouched.
    The user's "Closing scene" prompt for a scene such as walk_clean therefore never loses data.
    """
    actions = []
    for _ in range(2):
        for dialog in _dialogs():
            if dialog.get("kind") != "dialog":
                continue
            if not _is_new_dialog(state, dialog) or not _is_cleanup_dialog(dialog):
                continue
            if _dialog_already_clicked(state, dialog):
                continue
            label = _safe_click(dialog, SAFE_CLOSE_LABELS)
            if label:
                actions.append(label)
                _mark_dialog_clicked(state, dialog)
        pending = [
            dialog
            for dialog in _dialogs()
            if dialog.get("kind") == "dialog"
            and _is_new_dialog(state, dialog)
            and _is_cleanup_dialog(dialog)
            and not _dialog_already_clicked(state, dialog)
        ]
        if not pending:
            break
    state.setdefault("dialog_actions", []).extend(actions)
    remaining = []
    for dialog in _dialogs():
        remaining.append(_dialog_text(dialog) or dialog.get("type") or "dialog")
    return remaining


# ---------------------------------------------------------------------------
# Background job state
# ---------------------------------------------------------------------------


def _raw_state():
    state = getattr(sys, STATE_KEY, None)
    return state if isinstance(state, dict) else None


def _set_state(state):
    setattr(sys, STATE_KEY, state)


def _clear_state():
    setattr(sys, STATE_KEY, None)


def _jobs():
    """The T03 jobs module, or None when tests loaded cleanup without it."""
    try:
        from .. import jobs

        return jobs
    except Exception:
        return None


def _job_snapshot(job_id):
    """The snapshot of job_id, or None when the job is unknown/pruned/unavailable."""
    jobs = _jobs()
    if jobs is None or not job_id:
        return None
    try:
        return jobs.get(job_id)
    except Exception:
        return None


def _matching_running_job(state):
    """The newest running job whose kind and start time match this cleanup state.

    begin() runs before jobs.start_job creates the id, so the first poll binds the id this way.
    """
    jobs = _jobs()
    if jobs is None:
        return None
    try:
        snapshots = jobs.recent(10)
    except Exception:
        return None
    started_wall = float(state.get("started_wall") or 0.0)
    for snapshot in snapshots:
        if snapshot.get("status") != "running" or snapshot.get("kind") != state.get("kind"):
            continue
        try:
            delta = abs(float(snapshot.get("started_at") or 0.0) - started_wall)
        except (TypeError, ValueError):
            delta = JOB_START_MATCH_SECONDS + 1.0
        if delta <= JOB_START_MATCH_SECONDS:
            return snapshot
    return None


def _bind_job(state):
    """Record the T03 job id on the state, on the first poll; returns it or None."""
    job_id = state.get("job_id")
    if job_id:
        return job_id
    snapshot = _matching_running_job(state)
    if snapshot is not None:
        state["job_id"] = snapshot.get("job_id")
        return state["job_id"]
    return None


def _state_released(state) -> bool:
    """True when this busy flag no longer belongs to a running cleanup job.

    Covers the three cases that used to wedge the flag: the server restarted (jobs marks the job
    lost), the job finished/failed without calling cleanup (poll exception, timer creation
    failure, prune), and a begin() that never produced a job. A time limit is the final backstop.
    """
    now = time.monotonic()
    started = float(state.get("started") or now)
    last_poll = state.get("last_poll_at")
    last_poll = float(last_poll) if last_poll is not None else started
    if state.get("polling"):
        # The action itself can keep one poll busy for minutes; that is not a stale flag.
        return False
    if now - last_poll > STATE_STALE_SECONDS:
        return True
    if now - started > STATE_MAX_AGE_SECONDS:
        return True
    job_id = state.get("job_id")
    if job_id:
        snapshot = _job_snapshot(job_id)
        return snapshot is None or snapshot.get("status") != "running"
    snapshot = _matching_running_job(state)
    if snapshot is not None:
        state["job_id"] = snapshot.get("job_id")
        return False
    # begin() ran but its job has not appeared yet; allow a short binding grace only.
    return now - started > JOB_BIND_GRACE_SECONDS


def _release_state(state, restore=True) -> None:
    """Restore the selection (when asked) and drop the busy flag."""
    if restore:
        _restore_state_selection(state)
    _clear_state()


def _state():
    """The live cleanup state, or None when there is no running cleanup job."""
    state = _raw_state()
    if state is None:
        return None
    if _state_released(state):
        _release_state(state)
        return None
    return state


def _log_size():
    try:
        import os

        return os.path.getsize(common.LOG_PATH)
    except OSError:
        return None


def _new_log(state):
    if state.get("log_offset") is None:
        return common.log_tail(lines=120)
    return common.log_tail(since=state["log_offset"], lines=200)


def _failure(state, error, hint, **extra):
    remaining = _close_ui(state)
    _restore_state_selection(state)
    _clear_state()
    result = {"ok": False, "error": str(error), "hint": str(hint)}
    if remaining:
        result["open_dialogs"] = remaining
    result.update(extra)
    return result


def _start_job(kind: str, start, end, controllers) -> dict:
    """Validate, select, measure the before state and arm the job (begin callable)."""
    if _state() is not None:
        return common.fail(
            "Another cleanup job is still running.",
            "Call get_job for its id, or cancel_job it before starting another cleanup.",
        )
    wanted = parse_controllers(controllers)
    _app, view_scene, scene = common.scenes()
    try:
        required = timeline.require_keyed_range(scene, start, end)
    except ToolError as exc:
        return common.fail(
            str(exc),
            getattr(exc, "hint", None)
            or "Pass whole frame numbers inside the keyed animation (get_timeline shows the range).",
        )
    if isinstance(required, dict):
        return required
    if required is None:
        # Compatibility with the guard's documented "None when valid" form. The current
        # implementation returns (first, last); either shape is accepted.
        info = timeline.boundary_info(view_scene)
        try:
            first = info["first_frame"] if start is None else timeline.parse_frame(start, "start")
            last = info["last_frame"] if end is None else timeline.parse_frame(end, "end")
        except ToolError as exc:
            return common.fail(str(exc), getattr(exc, "hint", None))
    else:
        first, last = int(required[0]), int(required[1])
    error = minimum_range_error(first, last, kind)
    if error:
        hint = (
            "Pass start <= end, or omit both to use the whole keyed animation."
            if first > last
            else f"Pass at least {minimum_frame_count(kind)} keyed frames (get_timeline shows the range)."
        )
        return common.fail(error, hint)
    object_ids, names, explicit = _resolve_controllers(wanted)
    if object_ids is None:
        return names
    selection_snapshot = capture_selection(scene)
    viewer = scene.layers_viewer()
    layer_ids, used_all_layers = _target_layers(viewer, object_ids)
    unlock_ids = object_ids
    if used_all_layers:
        # The action will fall back to every timeline layer, so a locked layer that holds no
        # resolved control point must be checked too.
        layer_objects = _layer_object_ids(viewer, layer_ids)
        if layer_objects:
            unlock_ids = layer_objects
    blocked = _ensure_unlocked(unlock_ids)
    if blocked is not None:
        return blocked
    try:
        selection = _select_objects(object_ids)
        interval = _select_range(first, last, layer_ids)
    except Exception:
        restore_selection(scene, selection_snapshot)
        raise
    if int(interval.get("first", -1)) != first or int(interval.get("last", -1)) != last:
        restore_selection(scene, selection_snapshot)
        return common.fail(
            f"Cascadeur selected frames {interval.get('first')}-{interval.get('last')} instead of {first}-{last}.",
            hint="Widen the animation range with set_animation_range, or pass a range inside it.",
            selection=interval,
        )
    probe = _probe_frames(first, last)
    before_probe = _key_probe(first, last) if kind == PREPARE_KEYS else _pose_snapshot(object_ids, probe)
    try:
        if kind == FIX_FOOT:
            before = measure_slide(first, last)
        elif kind == FIX_COLLISIONS:
            before = measure_collisions(first, last)
        else:
            before = _all_layers_key_summary(first, last)
        measurement_error = None
    except Exception as exc:  # noqa: BLE001 - measurement must not block the cleaning itself
        before = {}
        measurement_error = f"{type(exc).__name__}: {exc}"
    measurement_hint = None
    if kind == FIX_FOOT and not before.get("pairs"):
        measurement_hint = (
            "Cascadeur has no cached fulcrum contacts for this range yet, so there is no before value; "
            "run prepare_keys_by_fulcrums on the same range first if you need the comparison."
        )
    elif kind == FIX_COLLISIONS and not before.get("point_depths"):
        measurement_hint = (
            "Cascadeur has no cached collision points for this range yet, so there is no before value; "
            "run prepare_keys_by_fulcrums on the same range first if you need the comparison."
        )
    started = time.monotonic()
    state = {
        "kind": kind,
        "action": ACTIONS[kind],
        "first": first,
        "last": last,
        "controllers": names,
        "explicit_controllers": explicit,
        "object_ids": object_ids,
        "probe_frames": probe,
        "before_probe": before_probe,
        "last_probe": before_probe,
        "before": before,
        "measurement_error": measurement_error,
        "measurement_hint": measurement_hint,
        "selection": selection,
        "selection_snapshot": selection_snapshot,
        "interval": interval,
        "started": started,
        "started_wall": time.time(),
        "last_poll_at": started,
        "job_id": None,
        "dialogs_before": _dialog_signatures(),
        "triggered_at": None,
        "phase": "trigger",
        "poll_count": 0,
        "stable": 0,
        "changed": False,
        "completed": None,
        "error": None,
        "log_offset": _log_size(),
        "dialog_actions": [],
        "commands": [],
        "first_change": None,
    }
    _set_state(state)
    return common.ok(
        started=True,
        frames={"first": first, "last": last},
        controllers=names,
        before=common.jsonable(before),
        measurement_error=measurement_error,
        measurement_hint=measurement_hint,
        hint="call get_job",
    )


def _trigger(state) -> None:
    import csc

    csc.app.get_application().get_action_manager().call_action(state["action"])
    state["triggered_at"] = time.monotonic()


def _poll(**_kwargs):
    """QTimer poll wrapper: never leave the busy flag behind when the poll itself fails."""
    try:
        return _poll_impl(**_kwargs)
    except Exception:
        state = _raw_state()
        if state is not None:
            _release_state(state)
        raise
    finally:
        state = _raw_state()
        if state is not None:
            state["polling"] = False


def _poll_impl(**_kwargs):
    """QTimer poll: trigger the action, answer dialogs, wait, measure and close the UI."""
    state = _state()
    if state is None:
        return {
            "ok": False,
            "error": "The cleanup job lost its state.",
            "hint": "Restart Cascadeur and try again.",
        }
    _bind_job(state)
    now = time.monotonic()
    state["last_poll_at"] = now
    state["polling"] = True
    elapsed = now - state["started"]
    kind = state["kind"]
    if state["phase"] == "trigger":
        try:
            _trigger(state)
        except Exception as exc:  # noqa: BLE001 - report the rejection
            state["error"] = f"Cascadeur refused the action {state['action']!r}: {type(exc).__name__}: {exc}"
            return _failure(
                state,
                state["error"],
                "Make sure a character with a Center of Mass and a timeline selection is open; get_log may explain it.",
            )
        state["phase"] = "running"
        return {"done": False, "progress": 0.02}

    state["poll_count"] += 1
    check_ui = state["poll_count"] <= 3 or state["poll_count"] % 3 == 0
    if check_ui:
        _handle_dialogs(state)
        if state["error"]:
            return _failure(
                state,
                state["error"],
                "Fix the scene setup (one character with a Center of Mass, at least three keyed frames) and retry; "
                "get_log has the full message.",
                log=_new_log(state)[-20:],
            )
    if state["phase"] == "finishing":
        if now - state.get("finish_at", now) >= POST_COMPLETE_SECONDS:
            return _finish(state)
        return {"done": False, "progress": 0.92}

    log = parse_log(_new_log(state), kind)
    if log["commands"]:
        state["commands"] = log["commands"][-10:]
    if log["completed"]:
        state["completed"] = log["completed"]
        state["phase"] = "finishing"
        state["finish_at"] = now
        return {"done": False, "progress": 0.9}
    if log["errors"]:
        state["error"] = log["errors"][0]
        return _failure(
            state,
            state["error"],
            "Cascadeur could not clean this motion. Check that the character has a Center of Mass and fulcrum "
            "groups, that the interval has at least three frames, and that the selected keys are inside it.",
            log=log["errors"][:20],
        )

    probe = _probe(state)
    if _probe_changed(state, probe, state["before_probe"]):
        state["changed"] = True
        if state["first_change"] is None:
            state["first_change"] = now
    if _probe_stable(state, probe, state["last_probe"]):
        state["stable"] += 1
    else:
        state["stable"] = 0
        state["last_probe"] = probe

    if state["changed"] and state["stable"] >= STABLE_POLLS and elapsed >= SETTLE_SECONDS:
        state["completed"] = state["completed"] or {"first": state["first"], "last": state["last"]}
        state["completion"] = "data_stable"
        state["phase"] = "finishing"
        state["finish_at"] = now
        return {"done": False, "progress": 0.9}

    if state["changed"]:
        return {"done": False, "progress": min(0.85, 0.1 + elapsed / max(1.0, NO_CHANGE_SECONDS) * 0.7)}
    if elapsed > NO_CHANGE_SECONDS:
        return _failure(
            state,
            f"{TITLES[kind]} did not change the animation within {NO_CHANGE_SECONDS:g}s.",
            "Check Cascadeur's log for the tool's error, make sure the interval has at least three frames of "
            "contact/collision data, and retry.",
            log=_new_log(state)[-20:],
        )
    return {"done": False, "progress": min(0.2, elapsed / max(1.0, NO_CHANGE_SECONDS) * 0.2)}


def _finish(state) -> dict:
    kind = state["kind"]
    try:
        after = (
            measure_slide(state["first"], state["last"])
            if kind == FIX_FOOT
            else measure_collisions(state["first"], state["last"])
            if kind == FIX_COLLISIONS
            else _all_layers_key_summary(state["first"], state["last"])
        )
        measurement_error = None
    except Exception as exc:  # noqa: BLE001 - the cleaning itself may still have worked
        after = {}
        measurement_error = f"{type(exc).__name__}: {exc}"
    remaining = _close_ui(state)
    before = state.get("before") or {}
    same_points = None
    if kind == FIX_FOOT:
        if before.get("pairs"):
            same_points = restrict_slide(before, after)
            compared = same_points
        else:
            compared = after
        change = {
            "slide_reduction_cm": total_delta(before, compared, "slide_total_cm"),
            "slide_reduction_percent": reduction_percent(before, compared, "slide_total_cm"),
            "basis": "same_point_frames" if same_points else "full_after",
        }
        if change["slide_reduction_cm"] < 0:
            change["warning"] = "The contact slide increased; check that the range covers a contact interval."
    elif kind == FIX_COLLISIONS:
        if before.get("point_depths"):
            same_points = restrict_collisions(before, after)
            compared = same_points
        else:
            compared = after
        change = {
            "penetration_reduction_cm": total_delta(before, compared, "penetration_total_cm"),
            "penetration_reduction_percent": reduction_percent(before, compared, "penetration_total_cm"),
            "basis": "same_point_frames" if same_points else "full_after",
        }
        if change["penetration_reduction_cm"] < 0:
            change["warning"] = "The penetration depth increased; check the selected range and collision settings."
    else:
        change = {
            "keys_before": before.get("key_count"),
            "keys_after": after.get("key_count"),
            "key_delta": (after.get("key_count") or 0) - (before.get("key_count") or 0),
        }
    result = {
        "ok": True,
        "tool": kind,
        "action": state["action"],
        "frames": {"first": state["first"], "last": state["last"]},
        "controllers": state["controllers"],
        "before": common.jsonable(before),
        "after": common.jsonable(after),
        "same_point_frames": common.jsonable(same_points),
        "change": common.jsonable(change),
        "completion": state.get("completion") or "log",
        "duration_s": round(time.monotonic() - state["started"], 3),
        "dialog_actions": state.get("dialog_actions") or [],
        "commands": state.get("commands") or [],
        "undo": (
            "Cascadeur's cleaning command is its own undo step (the command names in 'commands'); "
            "call undo() once and compare the result."
        ),
    }
    if state.get("measurement_error") or measurement_error:
        result["measurement_error"] = measurement_error or state.get("measurement_error")
    if state.get("measurement_hint"):
        result["measurement_hint"] = state["measurement_hint"]
    if remaining:
        result["open_dialogs"] = remaining
        result["hint"] = "A dialog could not be closed; call dialogs and press its safe button."
    if state["changed"] is False and not result.get("measurement_error"):
        result["hint"] = (
            "Cascadeur reported no measurable change. The motion may already be clean, or the range may not "
            "contain contact/collision frames."
        )
    _restore_state_selection(state)
    _clear_state()
    return {"done": True, "progress": 1.0, "result": common.jsonable(result)}


def _cancel(**_kwargs):
    state = _raw_state()
    if state is None:
        return
    _close_ui(state)
    _restore_state_selection(state)
    _clear_state()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@_tool("Check that a character with a Center of Mass and a timeline selection is open; get_log explains failures.")
def fix_foot_sliding(start=None, end=None, controllers=None) -> dict:
    """Start the Fulcrum Motion Cleaning background job."""
    result = _start_job(FIX_FOOT, start, end, controllers)
    if result.get("ok"):
        result["hint"] = "call get_job"
    return result


@_tool("Check that a character with a Center of Mass and a timeline selection is open; get_log explains failures.")
def fix_collisions(start=None, end=None, controllers=None) -> dict:
    """Start the Collision Penetration Cleaning background job."""
    result = _start_job(FIX_COLLISIONS, start, end, controllers)
    if result.get("ok"):
        result["hint"] = "call get_job"
    return result


@_tool("Check that the scene has an animated character and a timeline selection; get_log explains failures.")
def prepare_keys_by_fulcrums(start=None, end=None) -> dict:
    """Start the Auto keys background job."""
    result = _start_job(PREPARE_KEYS, start, end, None)
    if result.get("ok"):
        result["hint"] = "call get_job"
    return result


TOOLS = [
    {
        "name": "fix_foot_sliding",
        "description": "Remove foot sliding in a contact interval with Cascadeur's Fulcrum Motion Cleaning "
        "(View.FixFoot) as a background job: returns a job_id immediately, and get_job reports progress plus the "
        "contact-slide measurement before and after. The tool selects the controllers (or the whole character when "
        "controllers is omitted) and the start..end interval, then runs Cascadeur's cleaning. It measures the summed "
        "horizontal movement of fulcrum points that stay in contact between two frames (cm) and reports the "
        "reduction. Use it after keying a walk/run and before rendering. The range must be inside the animation and "
        "at least three frames long; omitted bounds mean the whole animation. Cleaning is Cascadeur's own undo "
        "command (not an 'MCP: ' step). Locked layers are refused (unlock with set_layer_locked first).",
        "inputSchema": schema(
            {
                "start": {
                    "type": ["integer", "string"],
                    "description": "First frame of the interval (default: animation start).",
                },
                "end": {
                    "type": ["integer", "string"],
                    "description": "Last frame of the interval (default: animation end).",
                },
                "controllers": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": "Control point name/alias or a list; omit for the whole character.",
                },
            }
        ),
        "fn": fix_foot_sliding,
        "poll": _poll,
        "cancel": _cancel,
        "background": True,
        "job_timeout": 300,
    },
    {
        "name": "fix_collisions",
        "description": "Push colliding bodies apart with Cascadeur's Collision Penetration Cleaning "
        "(View.FixCollisions) as a background job: returns a job_id immediately, and get_job reports progress plus the "
        "penetration measurement before and after. It selects the controllers (or the whole character) and the "
        "start..end interval, runs Cascadeur's cleaning, and measures the total and maximum penetration depth (cm) "
        "reported by get_frame_collision_info_points. Use it on a pose/interval where a limb penetrates the body or "
        "the floor. The range must be inside the animation and at least three frames long; omitted bounds mean the "
        "whole animation. Cleaning is Cascadeur's own undo command (not an 'MCP: ' step). Locked layers are refused.",
        "inputSchema": schema(
            {
                "start": {
                    "type": ["integer", "string"],
                    "description": "First frame of the interval (default: animation start).",
                },
                "end": {
                    "type": ["integer", "string"],
                    "description": "Last frame of the interval (default: animation end).",
                },
                "controllers": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": "Control point name/alias or a list; omit for the whole character.",
                },
            }
        ),
        "fn": fix_collisions,
        "poll": _poll,
        "cancel": _cancel,
        "background": True,
        "job_timeout": 300,
    },
    {
        "name": "prepare_keys_by_fulcrums",
        "description": "Prepare the keyframes of an interval from the fulcrum motion with Cascadeur's Auto keys "
        "(View.AutoInterpolation_Keys) as a background job: returns a job_id immediately, and get_job reports "
        "progress plus the key/interpolation summary before and after. Use it after cleaning contacts, so the "
        "cleaned motion is carried by fewer, better-placed keys instead of dense frames. The tool selects the whole "
        "character and the start..end interval; omitted bounds mean the whole animation. The result lists how many "
        "keys and which interpolation types the layers now have. Auto keys is Cascadeur's own undo command (not an "
        "'MCP: ' step). Locked layers are refused.",
        "inputSchema": schema(
            {
                "start": {
                    "type": ["integer", "string"],
                    "description": "First frame of the interval (default: animation start).",
                },
                "end": {
                    "type": ["integer", "string"],
                    "description": "Last frame of the interval (default: animation end).",
                },
            }
        ),
        "fn": prepare_keys_by_fulcrums,
        "poll": _poll,
        "cancel": _cancel,
        "background": True,
        "job_timeout": 300,
    },
]
