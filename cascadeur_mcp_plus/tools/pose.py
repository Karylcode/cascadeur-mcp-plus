"""Pose reading and writing (T16): get_pose and set_pose.

set_pose replaces dragging bones with numbers: it writes control-point positions (cm) and
rotations (Euler XYZ degrees) at a frame, keys the written controllers and lets AutoPosing pose
the rest of the body. get_pose reads the same numbers back, in world or local space.

The order of the AutoPosing calls is what makes the body follow (verified on Cascadeur 2026.2.3,
T15 handoff):

1. autoposing.lock_pose(frame, moved_ids) runs Cascadeur's AutoPosing.Autolock command *before*
   the write. Locking after the write locks the moved chain as well, so the body cannot follow.
2. The values are written inside one "MCP: set_pose" undo step (scene.modify_update_with_session),
   together with the keys and the AutoPosing update.
3. autoposing.run_update(scene, frame, session=...) runs AutoPosingTool.update inside that same
   undo step, so the write and the solve are one step. Without the lock, update restores the
   AutoPosing prediction and the written values disappear.
4. The read-back of every requested controller is checked against tolerance_cm (position) and 1.0
   degree (rotation). When AutoPosing left one off target, the same absolute values are re-applied
   without AutoPosing in a second undo step inside the same call, so the targets are exact and the
   body does not follow on that frame (autoposing_applied is false and a warning says so). A
   controller that still misses makes the call fail (ok false).

Cascadeur's Autolock is a native command with its own undo step ("AutoPosing.Autolock") and the
Python API cannot merge it into "MCP: set_pose", so fully undoing a set_pose takes two undos.
The tool result says so in undo_steps/undo_count/undo_note.

Writing control-point data with the data editor has one hierarchy limitation (T15 handoff): when
the pelvis is moved, the rig hierarchy drags the feet along before the AutoPosing solver runs, so
the feet follow the pelvis even when they are AutoPosing-locked. The tool description repeats it.

The pure helpers at the top (space/vector/value parsing, angle and distance comparisons) do not
import Cascadeur and are unit tested outside it (tests/test_pose.py). Everything that reads or
writes the scene must run on Cascadeur's GUI thread; set_pose is a handler tool so it can wait
for and answer Cascadeur's AutoPosing prompt on the HTTP thread without leaving a dialog open.
"""

from __future__ import annotations

import math
import traceback

from .. import autoposing as autoposing_helpers
from .. import common, dispatch, rig, timeline
from ..errors import ToolError
from ._schema import schema
from .layers import ensure_unlocked

SPACES = ("world", "local")
VALUE_KEYS = ("position", "offset", "rotation")
POSITION_DATA = {"world": "global_position", "local": "local_position"}
ROTATION_DATA = {"world": "global_rotation", "local": "local_rotation"}

UNDO_STEP_NAME = "set_pose"
SET_POSE_UNDO = "MCP: " + UNDO_STEP_NAME
FALLBACK_STEP_NAME = "set_pose (without AutoPosing)"
FALLBACK_UNDO = "MCP: " + FALLBACK_STEP_NAME
AUTOLOCK_UNDO = "AutoPosing.Autolock"

# A main point that moved more than this between the snapshot and the read-back counts as
# "AutoPosing adjusted it".
MOVED_CM = 0.1
# A requested position may miss by tolerance_cm (default 1.0 cm, set per call); a requested rotation
# may miss by ROTATION_TOLERANCE_DEG. Anything further off is a miss: AutoPosing fallback, then an error.
DEFAULT_TOLERANCE_CM = 1.0
ROTATION_TOLERANCE_DEG = 1.0

FALLBACK_WARNING = (
    "AutoPosing reverted the requested pose on this frame; the values were applied without AutoPosing, "
    "so the rest of the body did not follow."
)

UNDO_NOTE = (
    "Cascadeur's AutoPosing.Autolock step is native and cannot be merged into MCP: set_pose, so a "
    "full undo of a set_pose with autoposing=true takes two undos: first MCP: set_pose, then "
    "AutoPosing.Autolock (undo_steps is newest first). When AutoPosing reverted a target and the call "
    "re-applied the values without it, that re-application is one more step, MCP: set_pose (without "
    "AutoPosing), and a full undo takes three undos. With autoposing=false only MCP: set_pose exists."
)

NO_ROTATION_HINT = (
    "Cascadeur 2026.2.3 stores no rotation on main control points: a main point object only has a "
    "global position and its orientation is derived from the rig, so {name!r} cannot be rotated. "
    "Rotations work on boxes (for example hand_Box_l) and joints; use position/offset for main points."
)

NO_VALUE_HINT = (
    "Cascadeur has no {name_of_data} value for {name!r} at frame {frame} yet: a data channel reaches "
    "only the frames the scene's keys cover, and get_data_value refuses the rest. Pass key=true (the "
    "default) so set_pose keys the frame first, or call set_key(frames=[{frame}], controllers=[{name!r}]) "
    "before set_pose with key=false."
)

VALUES_HINT = (
    "Pass values as an object of controller name or alias to position, offset and/or rotation, for "
    'example {"left_hand": {"offset": [0, 40, 0]}} or {"pelvis": {"position": [0, 95, 0]}, '
    '"left_hand": {"rotation": [0, 0, 30]}}.'
)


# ---------------------------------------------------------------------------
# Argument parsing and pure comparisons (no Cascadeur import; unit tested)
# ---------------------------------------------------------------------------


def parse_space(space) -> str:
    """Return "world" or "local" (world when omitted), raising ToolError for anything else."""
    text = "world" if space is None or str(space).strip() == "" else str(space).strip().lower()
    if text not in SPACES:
        raise ToolError(f"space must be 'world' or 'local' (got {space!r}).")
    return text


def _number(value, field: str) -> float:
    if isinstance(value, bool):
        raise ToolError(f"{field} must be a number, not {value!r}.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ToolError(f"{field} must be a number, not {value!r}.") from None
    if not math.isfinite(number):
        raise ToolError(f"{field} must be a finite number, not {value!r}.")
    return number


def parse_vector(value, field: str) -> list[float]:
    """Return three finite floats (a position, offset or Euler rotation) or raise ToolError."""
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ToolError(f"{field} must be a list of three numbers [x, y, z] (got {value!r}).")
    if len(value) != 3:
        raise ToolError(f"{field} must have exactly three numbers [x, y, z] (got {value!r}).")
    return [_number(item, field) for item in value]


def parse_values(values) -> list[tuple[str, dict]]:
    """Validate the values argument into [(name, {op: [x, y, z]})] in the caller's order.

    values maps a controller name or alias to an object with position, offset and/or rotation.
    position and offset are alternatives (position is absolute, offset is added to the value at
    the frame); rotation may be combined with either.
    """
    if isinstance(values, (str, bytes)):
        raise ToolError(f"values must be an object, not a string: {VALUES_HINT}")
    if not isinstance(values, dict) or not values:
        raise ToolError(f"values must be a non-empty object: {VALUES_HINT}")
    parsed = []
    for raw_name, spec in values.items():
        name = str(raw_name).strip()
        if not name:
            raise ToolError(f"values has an empty controller name: {VALUES_HINT}")
        if not isinstance(spec, dict):
            raise ToolError(f"values[{name!r}] must be an object with position, offset or rotation (got {spec!r}).")
        if not spec:
            raise ToolError(f"values[{name!r}] is empty; pass position, offset or rotation.")
        unknown = sorted(str(key) for key in spec if key not in VALUE_KEYS)
        if unknown:
            raise ToolError(f"values[{name!r}] has unknown key(s) {unknown}; use position, offset or rotation.")
        if "position" in spec and "offset" in spec:
            raise ToolError(
                f"values[{name!r}] has both position and offset; position is absolute and offset is relative, "
                "so pass one of them (rotation can be combined with either)."
            )
        operations = {key: parse_vector(item, f"values[{name!r}][{key!r}]") for key, item in spec.items()}
        parsed.append((name, operations))
    return parsed


def quaternion_angle_deg(first, second) -> float:
    """Angle in degrees between two rotations given as {"w","x","y","z"} dicts (0..180).

    Comparing whole rotations avoids the Euler wrap problem: a rotation that differs by a few
    degrees can have per-axis Euler values that differ by almost 180 (for example |ry| > 90).
    """
    keys = ("w", "x", "y", "z")
    left = [float(first[key]) for key in keys]
    right = [float(second[key]) for key in keys]
    dot = abs(sum(a * b for a, b in zip(left, right, strict=True)))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    if norm <= 0.0:
        return 0.0
    return math.degrees(2.0 * math.acos(max(-1.0, min(1.0, dot / norm))))


def distance(first, second) -> float:
    """Euclidean distance between two 3D points (cm)."""
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(first, second, strict=True)))


def parse_tolerance(value) -> float:
    """tolerance_cm: a positive number of centimetres (DEFAULT_TOLERANCE_CM when omitted)."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return DEFAULT_TOLERANCE_CM
    number = _number(value, "tolerance_cm")
    if number <= 0.0:
        raise ToolError(f"tolerance_cm must be greater than 0 (got {value!r}).")
    return number


def misses(controllers, tolerance_cm=DEFAULT_TOLERANCE_CM, rotation_tolerance_deg=ROTATION_TOLERANCE_DEG) -> list:
    """The requested controllers that are off target after a write, with the errors that failed.

    controllers is the list of per-controller reports of a set_pose. A requested value that has no
    reading counts as a miss: a value that cannot be read back cannot be reported as on target.
    """
    missed = []
    for item in controllers or ():
        requested = item.get("requested") or {}
        failed = {}
        if requested.get("position") is not None:
            error = item.get("position_error_cm")
            if error is None or error > tolerance_cm:
                failed["position_error_cm"] = None if error is None else round(error, 3)
        if requested.get("rotation") is not None:
            error = item.get("rotation_error_deg")
            if error is None or error > rotation_tolerance_deg:
                failed["rotation_error_deg"] = None if error is None else round(error, 3)
        if failed:
            missed.append({"name": item.get("name"), **failed})
    return missed


def _boolean(value, name: str) -> bool:
    """Accept JSON booleans plus the usual true/false strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off"):
            return False
    raise ToolError(f"{name} must be true or false (got {value!r}).")


def _names(controllers) -> list[str]:
    """The controllers argument as a list of names (one string is one name)."""
    if controllers is None or controllers == "" or controllers == []:
        return []
    if isinstance(controllers, str):
        return [controllers]
    if not isinstance(controllers, (list, tuple)):
        raise ToolError(f"controllers must be a name or a list of names (got {controllers!r}).")
    return [str(item) for item in controllers]


# ---------------------------------------------------------------------------
# Scene access (Cascadeur GUI thread)
# ---------------------------------------------------------------------------


def _is_null(value) -> bool:
    try:
        return bool(value.is_null())
    except Exception:
        return value is None


def _transform_of(behaviour_viewer, object_id):
    """The object's Transform behaviour id, or None when it has none."""
    try:
        transform = behaviour_viewer.get_behaviour_by_name(object_id, "Transform")
    except Exception:
        return None
    return None if _is_null(transform) else transform


def _data_id(behaviour_viewer, transform, name):
    """The behaviour data id called name, or None when the object has no such data."""
    if transform is None:
        return None
    try:
        data_id = behaviour_viewer.get_behaviour_data(transform, name)
    except Exception:
        return None
    return None if _is_null(data_id) else data_id


def _range_ids(behaviour_viewer, object_id, name):
    """The non-null data ids of a CustomTransformData range property, in order."""
    try:
        custom = behaviour_viewer.get_behaviour_by_name(object_id, "CustomTransformData")
    except Exception:
        return []
    if custom is None or _is_null(custom):
        return []
    try:
        ids = behaviour_viewer.get_behaviour_data_range(custom, name)
    except Exception:
        return []
    return [item for item in ids if not _is_null(item)]


def _position_data(behaviour_viewer, object_id, space):
    """(data id, source) for the object's position in space, or (None, None)."""
    transform = _transform_of(behaviour_viewer, object_id)
    data_id = _data_id(behaviour_viewer, transform, POSITION_DATA[space])
    if data_id is not None:
        return data_id, "Transform"
    ids = _range_ids(behaviour_viewer, object_id, POSITION_DATA[space])
    if ids:
        return ids[0], "CustomTransformData"
    return None, None


def _rotation_data(behaviour_viewer, object_id, space):
    """(data id, source) for the object's rotation in space, or (None, None).

    Main control points have no rotation data in Cascadeur 2026.2.3: the point object only gets
    a global Position node (rig builder: add_global_position_to_object), and its orientation is
    derived from the rig triangle, so reading a main point's rotation is null and writing it is
    refused. Joints and boxes do have Transform global_rotation/local_rotation data.
    """
    transform = _transform_of(behaviour_viewer, object_id)
    data_id = _data_id(behaviour_viewer, transform, ROTATION_DATA[space])
    if data_id is not None:
        return data_id, "Transform"
    ids = _range_ids(behaviour_viewer, object_id, ROTATION_DATA[space])
    if ids:
        return ids[0], "CustomTransformData"
    return None, None


def _to_floats(raw):
    """A data value as a list of floats, or None when it is not numeric."""
    try:
        return [float(value) for value in raw]
    except (TypeError, ValueError):
        return None


def _quaternion_dict(quaternion) -> dict:
    """{"w","x","y","z"} floats from a csc Quaternion."""
    return {
        "w": float(quaternion.w()),
        "x": float(quaternion.x()),
        "y": float(quaternion.y()),
        "z": float(quaternion.z()),
    }


def _quaternion_euler_degrees(quaternion) -> list[float]:
    """Euler XYZ degrees from a csc Quaternion, using Cascadeur's quaternion->Euler pair.

    Reading through quaternion_to_euler_angles_x_y_z keeps the convention identical to the write
    path (euler_angles_to_quaternion_x_y_z); from_euler()/to_euler_angles_x_y_z() are not inverse
    conventions in 2026.2.3 (measured with a two-axis rotation, see the handoff note).
    """
    import csc

    return [math.degrees(float(value)) for value in csc.math.quaternion_to_euler_angles_x_y_z(quaternion)]


def _binary_read(reader, frame, low):
    """(value, frame) for the highest readable frame <= frame, searching down to low.

    Cascadeur refuses get_data_value() for frames past a data channel's range ("cannot get
    value on frame 10 ... last frame in animation is 0") when the scene has no keys there, so a
    read that needs a value before the frame is keyed falls back to the closest readable frame.
    low is the animation's first frame. Returns (None, None) when nothing is readable.
    """
    try:
        return reader(int(frame)), int(frame)
    except Exception:
        pass
    if frame <= low:
        return None, None
    best = None
    best_frame = None
    lower, upper = int(low), int(frame) - 1
    while lower <= upper:
        middle = (lower + upper) // 2
        try:
            value = reader(middle)
        except Exception:
            upper = middle - 1
            continue
        best = value
        best_frame = middle
        lower = middle + 1
    return best, best_frame


def _data_vector(data_viewer, data_id, frame, low=None):
    """(raw value, [x, y, z], frame used) for a position data id, or (None, None, None).

    low is the animation's first frame: pass it to allow a fallback to the closest readable
    frame when the requested frame has no data yet; pass None to read only that frame.
    """
    if data_id is None:
        return None, None, None

    def reader(position):
        raw = data_viewer.get_data_value(data_id, position)
        values = _to_floats(raw)
        if values is None or len(values) < 3:
            raise ValueError("not a three-component value")
        return raw, values[:3]

    if low is None:
        try:
            raw, values = reader(int(frame))
        except Exception:
            return None, None, None
        return raw, values, int(frame)
    value, used = _binary_read(reader, frame, low)
    if value is None:
        return None, None, None
    return value[0], value[1], used


def _data_rotation(data_viewer, data_id, frame, low=None):
    """{"euler", "quaternion", "frame"} for a rotation data id, or None.

    low is the animation's first frame (see _data_vector); None reads only the requested frame.
    """
    if data_id is None:
        return None

    def reader(position):
        rotation = data_viewer.get_data_value(data_id, position)
        quaternion = rotation.to_quaternion()
        return {
            "euler": _quaternion_euler_degrees(quaternion),
            "quaternion": _quaternion_dict(quaternion),
        }

    if low is None:
        try:
            value = reader(int(frame))
        except Exception:
            return None
        value["frame"] = int(frame)
        return value
    value, used = _binary_read(reader, frame, low)
    if value is None:
        return None
    value["frame"] = used
    return value


def _rotation_object(scene, object_id, space, frame):
    """The raw csc Rotation of an object at frame in space, or None."""
    model_viewer = scene.model_viewer()
    data_id, _source = _rotation_data(model_viewer.behaviour_viewer(), object_id, space)
    if data_id is None:
        return None
    try:
        return model_viewer.data_viewer().get_data_value(data_id, frame)
    except Exception:
        return None


def _controller_entries():
    """Every controller of the scene (rig.controllers) with id -> entry lookup."""
    entries = rig.controllers()
    by_id = {}
    for entry in entries:
        by_id[entry["id"]] = entry
    return entries, by_id


def _meta(by_id, model_viewer, object_id):
    """name/alias/kind/character for one object, also for objects that are not controllers."""
    entry = by_id.get(object_id)
    if entry is not None:
        return {
            "name": entry.get("name"),
            "alias": entry.get("alias"),
            "kind": entry.get("kind"),
            "character": entry.get("character"),
        }
    try:
        name = model_viewer.get_object_name(object_id)
    except Exception:
        name = None
    return {"name": name, "alias": None, "kind": rig.name_kind(name), "character": None}


def _read_points(scene, object_ids, frame, space, by_id, low=None):
    """Read position/rotation of object_ids at frame in space (GUI thread).

    low is the animation's first frame; when it is given, a controller whose data channel has no
    value at frame yet reports the closest readable frame in "frame_used".
    """
    model_viewer = scene.model_viewer()
    behaviour_viewer = model_viewer.behaviour_viewer()
    data_viewer = model_viewer.data_viewer()
    points = []
    for object_id in object_ids:
        position_id, _position_source = _position_data(behaviour_viewer, object_id, space)
        rotation_id, _rotation_source = _rotation_data(behaviour_viewer, object_id, space)
        _raw, position, position_frame = _data_vector(data_viewer, position_id, frame, low)
        rotation = _data_rotation(data_viewer, rotation_id, frame, low)
        item = _meta(by_id, model_viewer, object_id)
        item.update(
            position=position,
            rotation=None if rotation is None else rotation["euler"],
            quaternion=None if rotation is None else rotation["quaternion"],
        )
        if rotation_id is None:
            item["rotation_available"] = False
        used = {
            value for value in (position_frame, None if rotation is None else rotation["frame"]) if value is not None
        }
        if used and used != {int(frame)}:
            item["frame_used"] = max(used)
        points.append(item)
    return points


def _points_by_id(object_ids, points):
    """{object id: point} for a _read_points() result (same order as object_ids)."""
    return {object_id: point for object_id, point in zip(object_ids, points, strict=True)}


def _target_data(scene, ids, parsed, frame, space, key=True, low=0):
    """Validate the request and resolve the data ids to write (GUI thread).

    Returns (plan, failure). Nothing is written and no AutoPosing call is made, so set_pose can
    run this before locking the frame: a request that will be refused must not leave an
    AutoPosing.Autolock undo step or a changed lock state behind. With key=false every channel
    must already hold a value at frame; with key=true the write may extend the frame instead.
    """
    model_viewer = scene.model_viewer()
    behaviour_viewer = model_viewer.behaviour_viewer()
    data_viewer = model_viewer.data_viewer()
    plan = []
    for object_id, (name, operations) in zip(ids, parsed, strict=True):
        if _transform_of(behaviour_viewer, object_id) is None and not _range_ids(
            behaviour_viewer, object_id, POSITION_DATA[space]
        ):
            return None, common.fail(
                f"{name!r} has no Transform behaviour, so its position and rotation cannot be written.",
                hint="list_controllers shows the control points; pass a main point such as left_hand.",
            )
        item = {
            "id": object_id,
            "name": name,
            "operations": operations,
            "position": None,
            "rotation": None,
            "rotation_write": None,
            "parent_rotation": None,
        }
        if "position" in operations or "offset" in operations:
            data_id, _source = _position_data(behaviour_viewer, object_id, space)
            if data_id is None:
                return None, common.fail(
                    f"{name!r} has no {POSITION_DATA[space]} data, so its position cannot be written in {space} space.",
                    hint='Pass space="local" or choose a controller that has a Transform position (list_controllers).',
                )
            if not key and _data_vector(data_viewer, data_id, frame, None)[1] is None:
                return None, common.fail(
                    f"Could not read {name!r}'s position at frame {frame}.",
                    hint=NO_VALUE_HINT.format(name=name, frame=frame, name_of_data=POSITION_DATA[space]),
                )
            item["position"] = data_id
        if "rotation" in operations:
            data_id, _source = _rotation_data(behaviour_viewer, object_id, space)
            if data_id is None:
                return None, common.fail(
                    f"{name!r} has no rotation data, so its rotation cannot be written.",
                    hint=NO_ROTATION_HINT.format(name=name),
                )
            if not key and _data_rotation(data_viewer, data_id, frame, None) is None:
                return None, common.fail(
                    f"Could not read {name!r}'s rotation at frame {frame}.",
                    hint=NO_VALUE_HINT.format(name=name, frame=frame, name_of_data=ROTATION_DATA[space]),
                )
            item["rotation"] = data_id
            item["rotation_write"] = data_id
            if space == "world":
                # A world rotation is the parent's global rotation times the object's local
                # rotation, and Cascadeur recomputes global_rotation from the local channel (a
                # global write is overwritten; measured on a box). Convert and write local_rotation
                # when both the local channel and the parent's global rotation are readable.
                transform = _transform_of(behaviour_viewer, object_id)
                local_id = _data_id(behaviour_viewer, transform, "local_rotation")
                if local_id is None:
                    local_ids = _range_ids(behaviour_viewer, object_id, "local_rotation")
                    local_id = local_ids[0] if local_ids else None
                parent_rotation = _parent_global_rotation(behaviour_viewer, data_viewer, object_id, frame, low)
                if local_id is not None and parent_rotation is not None:
                    item["rotation_write"] = local_id
                    item["parent_rotation"] = parent_rotation
        plan.append(item)
    return plan, None


def _requested_targets(scene, ids, parsed, frame, space, key=True, low=0, plan=None):
    """Resolve the values into concrete data ids and target values (GUI thread).

    Returns (targets, failure). A target holds the data ids to write, the current value, the
    requested value and the operations it came from. Rotations in local space are written as
    local_rotation; in world space they are written as local_rotation converted from the
    requested global rotation when the parent rotation is readable. With key=true the write
    creates a key at frame, so the current value may come from the closest readable frame (low)
    when the channel has no data there yet; with key=false the frame must already hold a value.
    plan is the result of _target_data (set_pose validates before locking the frame).
    """
    if plan is None:
        plan, failure = _target_data(scene, ids, parsed, frame, space, key=key, low=low)
        if failure is not None:
            return None, failure
    data_viewer = scene.model_viewer().data_viewer()
    targets = []
    for item in plan:
        name = item["name"]
        operations = item["operations"]
        target = {"id": item["id"], "name": name, "operations": operations, "writes": []}
        if item["position"] is not None:
            raw, current, used = _data_vector(data_viewer, item["position"], frame, low if key else None)
            if current is None:
                return None, common.fail(
                    f"Could not read {name!r}'s position at frame {frame}.",
                    hint=NO_VALUE_HINT.format(name=name, frame=frame, name_of_data=POSITION_DATA[space]),
                )
            if "position" in operations:
                value = list(operations["position"])
            else:
                value = [current[index] + operations["offset"][index] for index in range(3)]
            target["writes"].append(
                {"kind": "position", "data": item["position"], "raw": raw, "current": current, "value": value}
            )
            target["requested_position"] = value
            target["before_position"] = current
            target["before_frame"] = used
        if item["rotation"] is not None:
            current = _data_rotation(data_viewer, item["rotation"], frame, low if key else None)
            if current is None:
                return None, common.fail(
                    f"Could not read {name!r}'s rotation at frame {frame}.",
                    hint=NO_VALUE_HINT.format(name=name, frame=frame, name_of_data=ROTATION_DATA[space]),
                )
            write = {
                "kind": "rotation",
                "data": item["rotation_write"],
                "current": current["euler"],
                "value": list(operations["rotation"]),
            }
            if item["parent_rotation"] is not None:
                write["parent_rotation"] = item["parent_rotation"]
            target["writes"].append(write)
            target["requested_rotation"] = list(operations["rotation"])
            target["before_rotation"] = current["euler"]
            target["before_frame"] = current["frame"]
        targets.append(target)
    return targets, None


def _parent_object(behaviour_viewer, object_id):
    """The object's parent object id (Basic.parent), or None."""
    try:
        basic = behaviour_viewer.get_behaviour_by_name(object_id, "Basic")
        if basic is None or _is_null(basic):
            return None
        parent = behaviour_viewer.get_behaviour_object(basic, "parent")
    except Exception:
        return None
    return None if parent is None or _is_null(parent) else parent


def _parent_global_rotation(behaviour_viewer, data_viewer, object_id, frame, low):
    """The parent's global Rotation at frame, or None when it cannot be read."""
    parent = _parent_object(behaviour_viewer, object_id)
    if parent is None:
        return None
    transform = _transform_of(behaviour_viewer, parent)
    data_id = _data_id(behaviour_viewer, transform, "global_rotation")
    if data_id is None:
        return None
    value = _data_rotation(data_viewer, data_id, frame, low)
    if value is None:
        return None
    try:
        return data_viewer.get_data_value(data_id, value["frame"])
    except Exception:
        return None


def _rotation_value(degrees):
    """A csc.math.Rotation from Euler XYZ degrees, through Cascadeur's own Euler/quaternion pair.

    from_euler() and to_euler_angles_x_y_z() are not inverse conventions in 2026.2.3, so a
    two-axis rotation written with from_euler did not read back with the same angles. Writing via
    euler_angles_to_quaternion_x_y_z + Rotation.from_quaternion and reading via
    quaternion_to_euler_angles_x_y_z keeps the two paths consistent.
    """
    import csc
    import numpy as np

    angles = np.radians(np.asarray([float(value) for value in degrees], dtype=float))
    return csc.math.Rotation.from_quaternion(csc.math.euler_angles_to_quaternion_x_y_z(angles))


def _vector_value(raw, numbers):
    """The written vector: the current data value with its three numbers replaced."""
    if raw is None:
        return [float(value) for value in numbers]
    try:
        value = raw.copy()
    except Exception:
        value = list(raw)
    for index, number in enumerate(numbers):
        value[index] = float(number)
    return value


def _needs_extension(targets, frame) -> bool:
    """Whether a write target's data ends before frame (Cascadeur then refuses the write)."""
    for target in targets:
        before_frame = target.get("before_frame")
        if before_frame is not None and int(before_frame) != int(frame):
            return True
    return False


def _locked_layers(layer_viewer, layer_ids) -> list[str]:
    """Ids of the layers among layer_ids that are locked (fails closed only for readable layers)."""
    locked = []
    for layer_id in layer_ids:
        try:
            layer = layer_viewer.layer(layer_id)
            if layer.is_locked():
                locked.append(str(layer_id))
        except Exception:
            continue
    return locked


def _extend_data_range(view_scene, scene, model_editor, scene_updater, frame, layer_ids):
    """Extend the target layers past frame so this edit can write there (inside the modify).

    Only the layers that hold the written controllers are touched, so other characters and
    layers keep their sections; the caller checks those layers for locks before the edit starts.
    Cascadeur refuses set_data_value() for a frame past a data channel's range ("last frame in
    animation is 0"), and keying the frame only extends the range when the edit commits. This is
    the recipe Cascadeur's own BVH importer uses to make the frames it is about to write
    reachable in one edit: put an empty section past the last frame, fit the animation size to
    the layers and regenerate the update. Verified on Cascy 2026-10-24: an empty section at
    frame 101 made frames 10 and 11 readable and writable inside the same modify and left the
    animation range at 0-100.
    """
    import csc

    from .. import timeline

    info = timeline.boundary_info(view_scene)
    target = max(int(frame) + 1, int(info["last_frame"]) + 1)
    section = csc.layers.layer.Section()
    layers_editor = model_editor.layers_editor()
    for layer_id in layer_ids:
        if not _is_null(layer_id):
            layers_editor.set_section(section, target, layer_id)
    model_editor.fit_animation_size_by_layers()
    scene_updater.generate_update()
    return target


def _rejected(name, log):
    """The failure common.edit would return for a rejected undo step."""
    return common.fail(
        f"Cascadeur aborted the undo step 'MCP: {name}'.",
        hint=(
            "Cascadeur catches exceptions inside the modify callback and only logs them; "
            "the traceback is in 'log' (get_log returns the same lines)."
        ),
        log=log,
    )


def _edit_step(name, fn):
    """Run fn as one "MCP: <name>" undo step through scene.modify_update_with_session.

    fn(model_editor, update_editor, scene_updater, session) is the callback shape T15's
    autoposing.run_update needs to fold the AutoPosing update into the same undo step.
    """
    try:
        _app, _view_scene, scene = common.scenes()
        changed = scene.modify_update_with_session("MCP: " + name, fn)
    except Exception:
        return common.fail(
            f"Cascadeur did not start the undo step 'MCP: {name}'.",
            hint="Check that a scene is open and that the arguments are valid.",
            traceback=traceback.format_exc(),
        )
    if not changed:
        return _rejected(name, common.log_tail(lines=120))
    return common.ok(undo="MCP: " + name)


def _prepare(parsed, frame, autoposing, space="world", key=True, answer="yes"):
    """GUI thread: resolve, validate, check locks, turn AutoPosing on and lock the frame.

    Every check that can refuse the call runs before AutoPosing is touched: a refused call must
    not leave an AutoPosing.Autolock undo step, a changed lock state or AutoPosing switched on
    behind (measured 2026-10-24: the lock used to run before the target checks). The lock still
    runs before _apply writes anything, because Cascadeur's AutoPosing.Autolock must see the
    frame's original pose and locking after the write locks the chain that should follow.
    Returns {"ok": True, "ids", "frame", "plan", "autoposing": {...}} or {"ok": False, "result"}.
    """
    _app, _view_scene, scene = common.scenes()
    names = [name for name, _operations in parsed]
    try:
        ids = rig.resolve(names)
    except rig.RigError as exc:
        # Return the structured failure instead of raising: dispatch.call_on_gui would flatten
        # the exception to a string and the candidate list would be lost.
        return {"ok": False, "result": exc.result()}
    if len(ids) != len(names):
        return {
            "ok": False,
            "result": common.fail(
                f"{len(names)} names resolved to {len(ids)} controller(s): two of them are the same controller.",
                hint=(
                    "Name each controller once; left_hand and hand_MainPoint_l are the same main point. "
                    "Combine their values into one entry."
                ),
                names=common.jsonable(names),
            ),
        }
    blocked = ensure_unlocked(ids)
    if blocked is not None:
        return {"ok": False, "result": blocked}
    actual_frame = int(scene.get_current_frame(False)) if frame is None else int(frame)
    low = timeline.boundary_info(_view_scene)["first_frame"]
    plan, failure = _target_data(scene, ids, parsed, actual_frame, space, key=key, low=low)
    if failure is not None:
        return {"ok": False, "result": failure}
    info = {
        "ok": True,
        "ids": ids,
        "frame": actual_frame,
        "plan": plan,
        "autoposing": {
            "requested": bool(autoposing),
            "enabled_before": None,
            "enabled": None,
            "state_known": False,
            "toggled": False,
            "attempts": 0,
            "locked": False,
            "lock_action": None,
            "lock_undo": None,
        },
    }
    if not autoposing:
        return info
    state = autoposing_helpers.state()
    on = info["autoposing"]
    on["enabled_before"] = state.get("enabled")
    on["state_known"] = bool(state.get("known"))
    if state.get("enabled") is False:
        for attempt in (1, 2):
            switched = autoposing_helpers.set_enabled(True)
            on["toggled"] = bool(switched.get("changed"))
            on["attempts"] = attempt
            state = autoposing_helpers.state()
            if state.get("enabled") is not False:
                break
    on["enabled"] = state.get("enabled")
    if on["enabled"] is False:
        return {
            "ok": False,
            "result": common.fail(
                "AutoPosing is off and could not be turned on, so the pose was not written.",
                hint=(
                    "Call autoposing_enable(true) and answer Cascadeur's prompt, then retry set_pose; or pass "
                    "autoposing=false to write the values without AutoPosing posing the body."
                ),
                autoposing=common.jsonable(on),
                frame=actual_frame,
            ),
        }
    # No ids: Cascadeur's Autolock locks the frame's AutoPosing points itself, and passing the
    # controllers would change the selection twice, which adds two native "Selection" undo steps
    # on top of AutoPosing.Autolock (measured 2026-10-24). Without them a set_pose is exactly two
    # undo steps: MCP: set_pose, then AutoPosing.Autolock.
    lock = autoposing_helpers.lock_pose(actual_frame)
    on["locked"] = True
    on["lock_action"] = lock.get("action")
    on["lock_undo"] = lock.get("undo")
    _answer_now(answer)
    return info


def _watch_ids(controllers, ids) -> list:
    """Every main point of the scene plus the written controllers: what a write can move."""
    watch = [entry["id"] for entry in controllers if entry.get("kind") == "main_point"]
    for object_id in ids:
        if object_id not in watch:
            watch.append(object_id)
    return watch


def _write_targets(data_editor, targets, frame) -> set:
    """Write every target value at frame (inside a modify step); returns the data ids written."""
    written = set()
    for target in targets:
        for write in target["writes"]:
            if write["kind"] == "rotation":
                value = _rotation_value(write["value"])
                if write.get("parent_rotation") is not None:
                    value = write["parent_rotation"].inverse() * value
            else:
                value = _vector_value(write.get("raw"), write["value"])
            data_editor.set_data_value(write["data"], frame, value)
            written.add(write["data"])
    return written


def _report_items(scene, targets, before, after, by_id, frame, space, keyed) -> list:
    """One report item per written controller: the request, the values before and after, the errors."""
    model_viewer = scene.model_viewer()
    reported = []
    for target in targets:
        before_point = before.get(target["id"], {})
        after_point = after.get(target["id"], {})
        after_meta = _meta(by_id, model_viewer, target["id"])
        item = {
            "name": after_meta.get("name") or target["name"],
            "alias": after_meta.get("alias"),
            "kind": after_meta.get("kind"),
            "requested": {
                "position": target.get("requested_position"),
                "rotation": target.get("requested_rotation"),
            },
            "before": {
                "position": before_point.get("position"),
                "rotation": before_point.get("rotation"),
                "frame": target.get("before_frame"),
            },
            "after": {
                "position": after_point.get("position"),
                "rotation": after_point.get("rotation"),
                "quaternion": after_point.get("quaternion"),
            },
            "keyed": bool(keyed),
        }
        if target.get("requested_position") is not None and after_point.get("position") is not None:
            item["position_error_cm"] = distance(after_point["position"], target["requested_position"])
        if target.get("requested_rotation") is not None:
            actual_rotation = _rotation_object(scene, target["id"], space, frame)
            requested_rotation = _rotation_value(target["requested_rotation"])
            if actual_rotation is not None and requested_rotation is not None:
                item["rotation_error_deg"] = quaternion_angle_deg(
                    _quaternion_dict(actual_rotation.to_quaternion()),
                    _quaternion_dict(requested_rotation.to_quaternion()),
                )
        reported.append(item)
    return reported


def _adjusted(controllers, before, after, written_ids) -> list:
    """Main points that moved although they were not written, largest move first."""
    adjusted = []
    for entry in controllers:
        if entry.get("kind") != "main_point" or entry["id"] in written_ids:
            continue
        point_before = before.get(entry["id"])
        point_after = after.get(entry["id"])
        if not point_before or not point_after:
            continue
        old, new = point_before.get("position"), point_after.get("position")
        if old is None or new is None:
            continue
        moved = distance(new, old)
        if moved > MOVED_CM:
            adjusted.append({"name": entry.get("name"), "alias": entry.get("alias"), "moved_cm": round(moved, 3)})
    adjusted.sort(key=lambda item: item["moved_cm"], reverse=True)
    return adjusted


def _write_without_autoposing(scene, targets, frame, watch_ids, by_id, space) -> dict:
    """Re-apply the requested values without AutoPosing, as one undo step (GUI thread).

    The first attempt already made the frame writable and keyed it in its own undo step, so this
    step only writes the same absolute values again and runs the hierarchy update: the result of
    autoposing=false, without the AutoPosing update that had reverted them.
    """

    def mod(model_editor, update_editor, scene_updater, session):
        actuals = _write_targets(model_editor.data_editor(), targets, frame)
        scene_updater.run_update(actuals, frame)

    step = _edit_step(FALLBACK_STEP_NAME, mod)
    if not step.get("ok"):
        return step
    after = _points_by_id(watch_ids, _read_points(scene, watch_ids, frame, space, by_id))
    return {"ok": True, "undo": step.get("undo"), "after": after}


def _apply(ids, parsed, frame, space, key, autoposing, answer="yes", plan=None, tolerance_cm=DEFAULT_TOLERANCE_CM):
    """GUI thread: write, key and solve, then check every requested controller against its target.

    With autoposing=true the values, the keys and the AutoPosing update are one "MCP: set_pose"
    undo step. The read-back of each requested controller is checked against tolerance_cm (position)
    and ROTATION_TOLERANCE_DEG (rotation). A controller that misses is handled in this same call:
    the same absolute values are re-applied without AutoPosing in a second undo step, and checked
    again. Returns {"ok": True, ...} with the final read-back (fallback when it ran, missed when a
    controller is still off target), or {"ok": False, "result", "created"} when Cascadeur refused an
    undo step; created lists the undo steps that were made before that.
    """
    previous_frame = None
    try:
        _app, view_scene, scene = common.scenes()
        # AutoPosingTool.update moves the playhead to the session frame (measured 2026-10-24), and
        # set_pose promises not to move it, so put it back before returning either way.
        previous_frame = int(scene.get_current_frame(False))
        low = timeline.boundary_info(view_scene)["first_frame"]
        targets, failure = _requested_targets(scene, ids, parsed, frame, space, key=key, low=low, plan=plan)
        if failure is not None:
            return {"ok": False, "result": failure}
        controllers, by_id = _controller_entries()
        watch_ids = _watch_ids(controllers, ids)
        before = _points_by_id(watch_ids, _read_points(scene, watch_ids, frame, space, by_id, low))
        layer_ids = timeline.layer_ids_for_viewer(scene.layers_viewer(), ids)
        extend = key and _needs_extension(targets, frame)
        if extend:
            locked = _locked_layers(scene.layers_viewer(), layer_ids)
            if locked:
                return {
                    "ok": False,
                    "result": common.fail(
                        "Refusing to extend locked layer(s) to make the frame writable.",
                        hint="Unlock the layer with set_layer_locked(name=<layer>, locked=false), then retry.",
                        layers=locked,
                    ),
                }
        stats = {"keys": None, "autoposing": None, "extended_to": None}

        def mod(model_editor, update_editor, scene_updater, session):
            if extend:
                stats["extended_to"] = _extend_data_range(
                    view_scene, scene, model_editor, scene_updater, frame, layer_ids
                )
            if key:
                stats["keys"] = timeline.set_key_at(model_editor, ids, frame)
            actuals = _write_targets(model_editor.data_editor(), targets, frame)
            if key:
                model_editor.set_fixed_interpolation_if_need(actuals, frame)
            scene_updater.run_update(actuals, frame)
            if autoposing:
                stats["autoposing"] = autoposing_helpers.run_update(scene=scene, frame=frame, session=session)

        step = _edit_step(UNDO_STEP_NAME, mod)
        if not step.get("ok"):
            return {"ok": False, "result": step}
        if autoposing:
            _answer_now(answer)
        written_ids = {target["id"] for target in targets}
        after = _points_by_id(watch_ids, _read_points(scene, watch_ids, frame, space, by_id))
        reported = _report_items(scene, targets, before, after, by_id, frame, space, key)
        missed = misses(reported, tolerance_cm)
        created = [SET_POSE_UNDO]
        fallback = None
        if autoposing and missed:
            again = _write_without_autoposing(scene, targets, frame, watch_ids, by_id, space)
            if not again.get("ok"):
                again["controllers"] = reported
                return {"ok": False, "result": again, "created": created}
            fallback = {"used": True, "missed": missed, "undo": again.get("undo")}
            created.append(FALLBACK_UNDO)
            after = again["after"]
            final = _report_items(scene, targets, before, after, by_id, frame, space, key)
            for item, first in zip(final, reported, strict=True):
                for name in ("position_error_cm", "rotation_error_deg"):
                    if name in first:
                        item[name + "_before_fallback"] = first[name]
            reported = final
            missed = misses(reported, tolerance_cm)
        return {
            "ok": True,
            "frame": frame,
            "space": space,
            "keyed": bool(key),
            "keys": stats["keys"],
            "controllers": reported,
            "adjusted": _adjusted(controllers, before, after, written_ids),
            "autoposing_update": stats["autoposing"],
            "autoposing_applied": bool(autoposing) and fallback is None,
            "fallback": fallback,
            "missed": missed,
            "undo": step.get("undo"),
            "created": created,
            "extended_to": stats["extended_to"],
        }
    except Exception as exc:  # noqa: BLE001 - tool boundary: report instead of raising
        return {
            "ok": False,
            "result": common.fail(
                f"Could not write the pose: {type(exc).__name__}: {exc}",
                hint=(
                    "Check that a rigged scene is open and that no dialog is blocking Cascadeur; the traceback is "
                    "below (get_log returns the same lines)."
                ),
                traceback=traceback.format_exc(),
                log=common.log_tail(lines=80),
            ),
        }
    finally:
        if previous_frame is not None:
            try:
                _app, _view_scene, scene = common.scenes()
                if int(scene.get_current_frame(False)) != int(previous_frame):
                    scene.set_current_frame(int(previous_frame))
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def get_pose(controllers=None, frame=None, space="world") -> dict:
    """Read the pose of the requested controllers at a frame (GUI thread, read-only)."""
    try:
        wanted_space = parse_space(space)
        wanted_frame = None if frame is None else timeline.parse_frame(frame, "frame")
        wanted_names = _names(controllers)
    except ToolError as exc:
        return common.fail(str(exc), hint="Pass space='world' or 'local' and a whole frame number.")
    try:
        _app, view_scene, scene = common.scenes()
        actual_frame = int(scene.get_current_frame(False)) if wanted_frame is None else wanted_frame
        low = timeline.boundary_info(view_scene)["first_frame"]
        controllers_all, by_id = _controller_entries()
        if wanted_names:
            try:
                ids = rig.resolve(wanted_names)
            except rig.RigError as exc:
                return exc.result()
        else:
            ids = [entry["id"] for entry in controllers_all if entry.get("kind") == "main_point"]
        points = _read_points(scene, ids, actual_frame, wanted_space, by_id, low)
        data = {
            "frame": actual_frame,
            "current_frame": int(scene.get_current_frame(False)),
            "space": wanted_space,
            "count": len(points),
            "controllers": points,
        }
        if not wanted_names:
            data["selection"] = "main control points"
        result = common.ok(**common.jsonable(data))
        used = sorted({point["frame_used"] for point in points if "frame_used" in point})
        if used:
            result["hint"] = (
                f"Cascadeur has no values at frame {actual_frame} yet for some controllers, so their values come "
                f"from frame {used[-1]} (frame_used). Key the frame first (set_pose with key=true, or set_key) to "
                "have data there."
            )
        elif not points:
            result["hint"] = (
                "The scene has no main control points in this mode (Rig mode has none); "
                "pass controllers explicitly or use list_controllers to see what exists."
            )
        return result
    except ToolError as exc:
        return common.fail(str(exc), hint="Check get_scene_info and list_controllers, then retry.")
    except Exception as exc:  # noqa: BLE001 - tool boundary
        return _unexpected("read the pose", exc)


def _unexpected(action, exc, **extra):
    """The shared failure for an unexpected exception (dispatch flattens tracebacks)."""
    return common.fail(
        f"Could not {action}: {type(exc).__name__}: {exc}",
        hint=("Check that a rigged scene is open and that no dialog is blocking Cascadeur; get_log has the traceback."),
        **extra,
    )


def _clear_prompt(answer):
    """Answer a prompt that is already open before the operation, so it cannot swallow it."""
    try:
        outcome = autoposing_helpers.finish_dialogs(answer, timeout=0.0)
    except Exception:  # noqa: BLE001 - the operation reports its own prompt outcome
        return None
    return outcome if outcome.get("found") else None


def _finish_prompt(answer, timeout=0.4):
    """Wait for and answer the AutoPosing prompt on the HTTP thread (short: it pops up at once)."""
    try:
        return autoposing_helpers.finish_dialogs(answer, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - never leave a prompt unnoticed
        return {"found": False, "closed": None, "error": f"{type(exc).__name__}: {exc}"}


def _answer_now(answer):
    """Answer a prompt that is open right now (GUI thread); best effort, never fatal."""
    try:
        return autoposing_helpers.answer_now(answer)
    except Exception:  # noqa: BLE001 - finish_dialogs reports a prompt that stays open
        return None


def _undo_report(locked, created=()):
    """undo_steps (newest first), undo_count and (when there is something to undo) undo_note."""
    made = ([AUTOLOCK_UNDO] if locked else []) + list(created)
    steps = list(reversed(made))
    report = {"undo_steps": steps, "undo_count": len(steps)}
    if steps:
        report["undo_note"] = UNDO_NOTE
    return report


MISSED_HINT = (
    "The requested values were not reached, so the pose on this frame is not what was asked: get_pose shows "
    "where each controller is now. The rig can refuse a value (for example a head the rig pulls back), so "
    "pick a value it can hold, or undo the steps in undo_steps (newest first) and try another pose."
)


def set_pose(values, frame=None, autoposing=True, key=True, space="world", answer="yes", tolerance_cm=None) -> dict:
    """Write positions/rotations at a frame, key them, let AutoPosing pose the body, and verify them."""
    try:
        parsed = parse_values(values)
        wanted_space = parse_space(space)
        wanted_frame = None if frame is None else timeline.parse_frame(frame, "frame")
        wanted_autoposing = _boolean(autoposing, "autoposing")
        wanted_key = _boolean(key, "key")
        wanted_answer = autoposing_helpers.answer_argument(answer)
        wanted_tolerance = parse_tolerance(tolerance_cm)
    except ToolError as exc:
        return common.fail(
            str(exc),
            hint=(
                "Values are cm (position/offset) and degrees (rotation). Call get_pose first to see the current "
                "numbers, and list_controllers for names and aliases."
            ),
        )

    _clear_prompt(wanted_answer)
    try:
        prepared = dispatch.call_on_gui(
            _prepare,
            {
                "parsed": parsed,
                "frame": wanted_frame,
                "autoposing": wanted_autoposing,
                "space": wanted_space,
                "key": wanted_key,
                "answer": wanted_answer,
            },
            60.0,
        )
    except Exception as exc:  # noqa: BLE001 - tool boundary
        return _unexpected("prepare the pose", exc, undo_steps=[], undo_count=0)
    if not isinstance(prepared, dict) or not prepared.get("ok"):
        if isinstance(prepared, dict) and isinstance(prepared.get("result"), dict):
            result = prepared["result"]
            result.update(_undo_report(False))
            return result
        return _unexpected("prepare the pose", prepared)

    # The prompt only comes from the AutoPosing calls; a plain data write cannot open it, so
    # waiting for it would just add a timeout to every autoposing=false call.
    dialog = _finish_prompt(wanted_answer) if wanted_autoposing else None
    locked = bool(prepared["autoposing"].get("locked"))
    try:
        applied = dispatch.call_on_gui(
            _apply,
            {
                "ids": prepared["ids"],
                "parsed": parsed,
                "frame": prepared["frame"],
                "space": wanted_space,
                "key": wanted_key,
                "autoposing": wanted_autoposing,
                "answer": wanted_answer,
                "plan": prepared.get("plan"),
                "tolerance_cm": wanted_tolerance,
            },
            120.0,
        )
    except Exception as exc:  # noqa: BLE001 - tool boundary
        failure = _unexpected("write the pose", exc, **_undo_report(locked))
        failure["autoposing"] = common.jsonable(prepared["autoposing"])
        return failure
    if not isinstance(applied, dict) or not applied.get("ok"):
        result = applied.get("result") if isinstance(applied, dict) else _unexpected("write the pose", applied)
        created = applied.get("created", []) if isinstance(applied, dict) else []
        result.update(_undo_report(locked, created))
        result["autoposing"] = common.jsonable(prepared["autoposing"])
        result["dialog"] = dialog if dialog and (dialog.get("found") or dialog.get("error")) else None
        return result

    if wanted_autoposing:
        dialog = _finish_prompt(wanted_answer)
    missed = applied.get("missed") or []
    fallback = applied.get("fallback")
    data = {
        "frame": applied.get("frame"),
        "space": applied.get("space"),
        "keyed": applied.get("keyed"),
        "keys": applied.get("keys"),
        "controllers": applied.get("controllers"),
        "adjusted": applied.get("adjusted"),
        "autoposing": prepared["autoposing"],
        "autoposing_applied": applied.get("autoposing_applied"),
        "tolerance_cm": wanted_tolerance,
        "fallback": fallback,
        "autoposing_update": applied.get("autoposing_update"),
        "extended_to": applied.get("extended_to"),
        "undo": applied.get("undo"),
        "dialog": dialog if dialog and (dialog.get("found") or dialog.get("error")) else None,
    }
    data.update(_undo_report(locked, applied.get("created", [])))
    if missed:
        data["missed"] = missed
    result = common.ok(**common.jsonable(data))
    errors = []
    hints = []
    if dialog and (dialog.get("closed") is False or dialog.get("error")):
        errors.append("The pose was written, but the 'Adjust AutoPosing lock state?' prompt is still open.")
        hints.append("Answer the prompt with dialogs and ui_click, then retry; the pose is already in the scene.")
    if missed:
        names = ", ".join(str(item["name"]) for item in missed)
        if fallback:
            errors.append(
                f"AutoPosing and the fallback without it both left these controllers off target by more than "
                f"{wanted_tolerance:g} cm or 1 degree: {names}."
            )
        else:
            errors.append(
                f"These controllers are off target by more than {wanted_tolerance:g} cm or 1 degree: {names}."
            )
        hints.append(MISSED_HINT)
    if errors:
        result["ok"] = False
        result["error"] = " ".join(errors)
    if fallback:
        result["warning"] = FALLBACK_WARNING
    if applied.get("extended_to"):
        hints.append(
            f"The scene had no data at frame {applied.get('frame')}, so the layers were extended to frame "
            f"{applied['extended_to']} in the same undo step to make the write possible."
        )
    if prepared["autoposing"].get("enabled") is None and wanted_autoposing:
        hints.append(
            "Cascadeur's AutoPosing switch could not be read, so the update ran without confirming that AutoPosing "
            "is on; check get_autoposing_state."
        )
    if hints:
        result["hint"] = " ".join(hints)
    return result


TOOLS = [
    {
        "name": "get_pose",
        "description": (
            "Read the current pose as numbers: for each controller its position in cm and its rotation as Euler XYZ "
            "degrees plus a quaternion, at a frame, in world or local space. Use it before set_pose to see the "
            "current values, after set_pose to check that a change stuck, or to copy numbers into another call. "
            "controllers accepts names and aliases (list_controllers shows them) and defaults to the character's "
            "main control points; frame defaults to the current frame, and the playhead is not moved, so any frame "
            "can be read. space=world (the default) reads global_position/global_rotation, space=local reads "
            "local_position/local_rotation. Main control points have no rotation data in Cascadeur 2026.2.3 (their "
            "orientation is derived from the rig), so they report rotation null and rotation_available false; boxes "
            "(hand_Box_l) and joints have rotations. A frame the scene has no values for yet reports the closest "
            "readable frame in frame_used. Read-only: it never changes the scene, the selection or the playhead. To "
            "set a pose always use set_pose."
        ),
        "inputSchema": schema(
            {
                "controllers": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": (
                        "Controller name(s) or alias(es), for example left_hand or "
                        '["left_hand", "right_hand"]; omit for the main control points.'
                    ),
                },
                "frame": {
                    "type": ["integer", "string"],
                    "description": "Frame to read (0 or more); omit for the current frame. The playhead does not move.",
                },
                "space": {
                    "type": "string",
                    "enum": list(SPACES),
                    "description": "world (default; global_position/global_rotation) or local (local_position/local_rotation).",
                },
            }
        ),
        "requires": [
            "csc.Guid",
            "csc.app.get_application",
            "csc.domain.Scene.get_current_frame",
            "csc.domain.Scene.model_viewer",
            "csc.math.OrthogonalTransform",
            "csc.math.Quaternion",
            "csc.math.Rotation",
            "csc.math.ScaledTransform",
            "csc.math.quaternion_to_euler_angles_x_y_z",
            "csc.model.BehaviourViewer.get_behaviours",
            "csc.model.ModelViewer.get_objects",
            "csc.model.ObjectId",
        ],
        "fn": get_pose,
    },
    {
        "name": "set_pose",
        "description": (
            "Set the character's pose with numbers instead of dragging bones, then key it: write control-point "
            "positions (cm) and rotations (Euler XYZ degrees) at a frame and let AutoPosing pose the rest of the "
            "body. Always pose with set_pose; do not drag controllers with ui_drag. values maps a controller name "
            "or alias to one or more of position [x, y, z] (absolute, cm, in space), offset [dx, dy, dz] (added to "
            "the value at that frame) and rotation [rx, ry, rz] (degrees, in space); position and offset are "
            "alternatives and rotation can be combined with either. Main control points only have a position in "
            "Cascadeur 2026.2.3, so rotation works on boxes (hand_Box_l) and joints, not on left_hand. With "
            "autoposing=true (the default) the frame is first locked with Cascadeur's AutoPosing.Autolock command, "
            "then only the given controllers are written, then Cascadeur's AutoPosing update runs, so the body "
            "follows (the order matters: locking after the write stops the body from following, and writing "
            "without a lock is restored by the update). AutoPosing cannot hold the values on every frame (measured: "
            "a frame after an earlier posed frame, and a second call on the same frame), so every requested "
            "controller is checked against its target after the AutoPosing update. When one misses by more than "
            "tolerance_cm (default 1.0 cm; rotations 1.0 degree), the same call re-applies the requested values "
            "without AutoPosing, so they are exact, and returns ok true with autoposing_applied false, a warning, "
            "and the errors before and after the fallback (position_error_cm_before_fallback, "
            "rotation_error_deg_before_fallback); the rest of the body does not follow on that frame. A controller "
            "that still misses returns ok false with the errors and a hint. autoposing=false writes only the given "
            "values, leaves every other control point alone, and is checked the same way. key=true (the default) "
            "keys the written controllers at the frame; with key=false only an existing key's value changes. The "
            "write, the keys and the AutoPosing update are one undo step 'MCP: set_pose', but Cascadeur's Autolock "
            "step is native and cannot be merged into it, so fully undoing a set_pose takes two undos (three when "
            "the fallback ran); undo_steps lists them (newest first) with undo_count and undo_note. frame defaults "
            "to the current frame and the playhead is not moved. A frame the scene has no data at yet is made "
            "writable in the same undo step (an empty section past the last frame, Cascadeur's own BVH-import "
            "recipe), and extended_to reports the frame it extended to. The call refuses to write controllers whose "
            "animation layer is locked (unlock it with set_layer_locked). Limitation: moving the pelvis with a data "
            "write also drags the feet through the rig hierarchy before AutoPosing runs, so the feet follow the "
            "pelvis even when they are AutoPosing-locked; lock the feet again afterwards or move the pelvis in the "
            "interface when they must stay. Returns each written controller's actual position and rotation after "
            "the call with position_error_cm and rotation_error_deg against the request, the other main points "
            "AutoPosing adjusted (adjusted, moved_cm), the keys, the AutoPosing state and the undo steps. It answers "
            "Cascadeur's 'Adjust AutoPosing lock state?' prompt (answer yes by default) so no dialog is left open."
        ),
        "inputSchema": schema(
            {
                "values": {
                    "type": "object",
                    "description": (
                        'Pose values by controller name or alias, for example {"left_hand": {"offset": [0, 40, 0]}}; '
                        "position is absolute in cm, offset is added to the current value, rotation is Euler XYZ "
                        "degrees. position and offset are alternatives; rotation can be combined with either."
                    ),
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "position": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 3,
                                "maxItems": 3,
                                "description": "Absolute position [x, y, z] in cm, in space.",
                            },
                            "offset": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 3,
                                "maxItems": 3,
                                "description": "Offset [dx, dy, dz] in cm, added to the value at that frame.",
                            },
                            "rotation": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 3,
                                "maxItems": 3,
                                "description": "Euler XYZ rotation [rx, ry, rz] in degrees, in space.",
                            },
                        },
                    },
                },
                "frame": {
                    "type": ["integer", "string"],
                    "description": "Frame to pose (0 or more); omit for the current frame. The playhead does not move.",
                },
                "autoposing": {
                    "type": "boolean",
                    "description": (
                        "true (default) locks the frame, writes the given controllers and runs Cascadeur's AutoPosing "
                        "update so the body follows; false writes only the given values."
                    ),
                },
                "key": {
                    "type": "boolean",
                    "description": "true (default) keys the written controllers at the frame; false changes existing keys only.",
                },
                "space": {
                    "type": "string",
                    "enum": list(SPACES),
                    "description": "world (default; global_position/global_rotation) or local (local_position/local_rotation).",
                },
                "tolerance_cm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": (
                        "Largest miss in cm allowed for each requested position before the AutoPosing fallback runs "
                        "(default 1.0). Rotations are held to 1.0 degree."
                    ),
                },
                "answer": {
                    "type": "string",
                    "enum": list(autoposing_helpers.ANSWER_CHOICES),
                    "description": (
                        "Answer for Cascadeur's 'Adjust AutoPosing lock state?' prompt: yes (default; Autolock), no "
                        "(keep the current locks) or cancel (close the prompt without changing locks)."
                    ),
                },
            },
            ["values"],
        ),
        "requires": [
            "csc.Guid",
            "csc.app.ActionManager.call_action",
            "csc.app.Application.get_action_manager",
            "csc.app.Application.get_tools_manager",
            "csc.app.CascadeurTool.editor",
            "csc.app.SceneManager.scenes",
            "csc.app.ToolsManager.get_tool",
            "csc.app.get_application",
            "csc.domain.Scene.get_current_frame",
            "csc.domain.Scene.layers_viewer",
            "csc.domain.Scene.model_viewer",
            "csc.domain.Scene.set_current_frame",
            "csc.domain.SceneUpdater.run_update",
            "csc.layers.layer.Section",
            "csc.math.OrthogonalTransform",
            "csc.math.Quaternion",
            "csc.math.Rotation",
            "csc.math.Rotation.from_quaternion",
            "csc.math.ScaledTransform",
            "csc.math.euler_angles_to_quaternion_x_y_z",
            "csc.math.quaternion_to_euler_angles_x_y_z",
            "csc.model.BehaviourViewer.get_behaviours",
            "csc.model.DataEditor.set_data_value",
            "csc.model.ModelEditor.layers_editor",
            "csc.model.ModelViewer.get_objects",
            "csc.model.ObjectId",
            "csc.model.ObjectId.null",
            "action:AutoPosing.Autolock",
            "action:AutoPosingTool.AutoPosing",
            "action:AutoPosingTool.AutoUnlock",
        ],
        "handler": set_pose,
    },
]
