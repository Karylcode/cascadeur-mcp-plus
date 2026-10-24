"""Mirror, copy/paste and reset poses (T22): mirror_pose, copy_pose, paste_pose, reset_pose.

mirror_pose uses Cascadeur's own MirrorTool (csc.tools.MirrorTool core): it is set to the
character's mirror plane and mirrors the whole rig at a frame, including the rotations of boxes
and joints that the numeric pose tools cannot compute reliably. One call is one native undo step
named "Mirror" (measured 2026-10-24); Cascadeur does not let Python rename or merge that step.
Mirroring only a subset of the rig is not coherent (measured: mirroring a hand point alone moved
it into the wrong place), so the tool always mirrors the whole character and reports the
controllers the caller asked about.

copy_pose stores a frame's pose in this module (world positions in cm, local rotations in degrees)
under a generated name; paste_pose writes it back at another frame. paste_pose writes positions
in world space and rotations in local space (T16: a box's global rotation is recomputed from its
local rotation), keys the frame and runs inside one "MCP: paste_pose" undo step. Pasting a
mirrored copy mirrors the stored positions across the character's mirror plane; rotations are not
mirrored by paste_pose (mirror_pose does that on the live rig), and the result says so.

reset_pose("default") writes the rig's stored T-pose values (the TPose behaviour, the same data
Cascadeur's "Go to T-pose" command uses): main control points get their TPose world position;
boxes and joints get local transforms computed against their parent's TPose transform (the
command's recipe). reset_pose("bind") writes joint global transforms from the skinned meshes'
inverse bind matrices, like Cascadeur's "Go to Bind Pose" command. Both run in one
"MCP: reset_pose" undo step.
"""

from __future__ import annotations

import math
import time
import traceback

from .. import common, rig, timeline
from ..errors import ToolError
from . import pose
from ._schema import schema
from .layers import ensure_unlocked

KIND_DEFAULT = "default"
KIND_BIND = "bind"
KINDS = (KIND_DEFAULT, KIND_BIND)

AXES = {"x": 0, "y": 1, "z": 2}
UNIT = {"x": [1.0, 0.0, 0.0], "y": [0.0, 1.0, 0.0], "z": [0.0, 0.0, 1.0]}
# RiggingWindow.get_character_mirror_plane(): MirrorPlane XY=0, XZ=1, YZ=2, Undef=3.
RIG_PLANE_AXES = {0: "z", 1: "y", 2: "x"}
# The Mirror behaviour's symmetry_plane data uses the opposite order: YZ=0, XZ=1, XY=2.
BEHAVIOUR_PLANE_AXES = {0: "x", 1: "y", 2: "z"}

MIRROR_UNDO = "Mirror"
MIRROR_UNDO_NOTE = (
    "The mirror is Cascadeur's native MirrorTool command: it creates one undo step named "
    "'Mirror' and the Python API cannot rename or merge it into an 'MCP: ...' step. A single "
    "Ctrl+Z restores the pose."
)
PASTE_UNDO = "paste_pose"
RESET_UNDO = "reset_pose"
COPY_PREFIX = "pose_"

MOVED_CM = 0.01
MIRROR_TOLERANCE_CM = 1.0
ROTATION_TOLERANCE_DEG = 0.5

DEFAULT_CONTROLLERS = ("main_point", "box", "joint")
NO_ROTATION_NOTE = (
    "Main control points have no rotation data in Cascadeur 2026.2.3, so their rotation stays "
    "null; boxes (for example hand_Box_l) and joints do have rotations."
)

UNDO_NOTE = (
    "The whole call runs inside one undo step named 'MCP: {name}'; one Ctrl+Z restores the pose "
    "before the call, including the keys it created."
)

MIRROR_HINT = (
    "Mirroring always covers the whole character: mirroring only part of a rig is not coherent in "
    "Cascadeur (measured with the MirrorTool), so controllers only choose what the result reports."
)

_STORE: dict[str, dict] = {}
_STORE_COUNTER = 0


# ---------------------------------------------------------------------------
# Pure helpers (no Cascadeur import; unit tested)
# ---------------------------------------------------------------------------


def parse_kind(value) -> str:
    """Return "default" or "bind", raising ToolError for anything else."""
    text = KIND_DEFAULT if value is None or str(value).strip() == "" else str(value).strip().lower()
    if text in ("default", "t-pose", "tpose", "t_pose", "rest"):
        return KIND_DEFAULT
    if text in ("bind", "bind-pose", "bindpose", "bind_pose"):
        return KIND_BIND
    raise ToolError(f"kind must be 'default' or 'bind' (got {value!r}).")


def parse_boolean(value, name: str) -> bool:
    """Accept JSON booleans plus the usual true/false strings."""
    return pose._boolean(value, name)


def axis_from_rig_plane(value):
    """Map RiggingWindow's mirror plane enum to an axis name, or None."""
    try:
        return RIG_PLANE_AXES.get(int(value))
    except (TypeError, ValueError):
        return None


def axis_from_behaviour_plane(value):
    """Map the Mirror behaviour's symmetry_plane value to an axis name, or None."""
    try:
        return BEHAVIOUR_PLANE_AXES.get(int(value))
    except (TypeError, ValueError):
        return None


def plane_for_axis(axis: str, offset=0.0) -> dict:
    """A mirror plane perpendicular to axis through offset on that axis."""
    if axis not in AXES:
        raise ToolError(f"axis must be 'x', 'y' or 'z' (got {axis!r}).")
    index = AXES[axis]
    point = [0.0, 0.0, 0.0]
    point[index] = float(offset)
    return {
        "axis": axis,
        "normal": list(UNIT[axis]),
        "offset": float(offset),
        "point": point,
    }


def reflect_point(position, plane) -> list[float]:
    """Reflect a point across the plane (2 * projection - point)."""
    normal = plane["normal"]
    distance = sum(float(position[index]) * normal[index] for index in range(3)) - float(plane["offset"])
    return [float(position[index]) - 2.0 * distance * normal[index] for index in range(3)]


def reflect_quaternion(quaternion, plane) -> dict:
    """Reflect a world rotation quaternion across a coordinate plane.

    Reflecting a rotation R across the plane x=0 gives M R M with M = diag(-1, 1, 1); in
    quaternion components that is (w, x, -y, -z) for the x plane, (w, -x, y, -z) for y and
    (w, -x, -y, z) for z (the same rule Cascadeur's mirror_actions implementation uses).
    """
    w = float(quaternion.get("w", 1.0))
    x = float(quaternion.get("x", 0.0))
    y = float(quaternion.get("y", 0.0))
    z = float(quaternion.get("z", 0.0))
    axis = plane["axis"]
    if axis == "x":
        return {"w": w, "x": x, "y": -y, "z": -z}
    if axis == "y":
        return {"w": w, "x": -x, "y": y, "z": -z}
    if axis == "z":
        return {"w": w, "x": -x, "y": -y, "z": z}
    raise ToolError(f"Unknown mirror axis {axis!r}.")


def mirror_key(entry) -> tuple:
    """(kind, body-part tokens) of a controller entry, used to pair left and right."""
    name = entry.get("name") if isinstance(entry, dict) else entry
    kind = entry.get("kind") if isinstance(entry, dict) else rig.name_kind(name)
    base, _side = rig.normalize_name(name)
    return (kind, base)


def mirror_index(entries) -> dict[str, str | None]:
    """Map every controller name to its mirror counterpart.

    A left/right pair is two controllers with the same kind and body-part tokens and opposite
    sides (hand_MainPoint_l / hand_MainPoint_r, mixamorig:LeftHand / mixamorig:RightHand,
    LeftHand / RightHand). An unsided controller (pelvis, chest, head, neck, center of mass) is
    its own counterpart. A sided controller without exactly one counterpart maps to None: the
    caller reports it instead of guessing. Names that share a key and a side (duplicate
    namespaces) are left unpaired for the same reason.
    """
    buckets: dict[tuple, dict[str, list[str]]] = {}
    for entry in entries:
        name = entry.get("name") if isinstance(entry, dict) else entry
        if not name:
            continue
        _base, side = rig.normalize_name(name)
        buckets.setdefault(mirror_key(entry), {}).setdefault(side, []).append(str(name))
    index: dict[str, str | None] = {}
    for _key, sides in buckets.items():
        left = sides.get("l", [])
        right = sides.get("r", [])
        paired = len(left) == 1 and len(right) == 1
        for name in left:
            index[name] = right[0] if paired else None
        for name in right:
            index[name] = left[0] if paired else None
    for entry in entries:
        name = entry.get("name") if isinstance(entry, dict) else entry
        if not name:
            continue
        _base, side = rig.normalize_name(name)
        if side is None and name not in index:
            index[str(name)] = str(name)
    return index


def next_pose_name(store=None) -> str:
    """The first unused "pose_<n>" name."""
    store = _STORE if store is None else store
    number = 1
    while f"{COPY_PREFIX}{number}" in store:
        number += 1
    return f"{COPY_PREFIX}{number}"


def pose_summary(stored) -> dict:
    """A compact description of a stored pose, for listings and hints."""
    return {
        "name": stored.get("name"),
        "frame": stored.get("frame"),
        "controllers": len(stored.get("controllers") or []),
        "created": stored.get("created"),
        "scene": stored.get("scene"),
    }


def mirror_position_plan(controllers, index, plane) -> list[dict]:
    """Plan the mirrored positions of stored/source controllers.

    controllers is a list of {"name", "position"}; index maps names to counterparts. Returns one
    entry per source: {source, target, position (requested target position), skipped}. A sided
    source without a counterpart is skipped with a reason.
    """
    plan = []
    for item in controllers:
        name = item.get("name")
        position = item.get("position")
        target = index.get(name)
        if target is None:
            plan.append({"source": name, "target": None, "position": None, "skipped": "no mirror counterpart"})
            continue
        if position is None:
            plan.append({"source": name, "target": target, "position": None, "skipped": "no stored position"})
            continue
        plan.append(
            {
                "source": name,
                "target": target,
                "position": reflect_point(position, plane),
                "skipped": None,
            }
        )
    return plan


# ---------------------------------------------------------------------------
# Small scene helpers (Cascadeur GUI thread)
# ---------------------------------------------------------------------------


def _is_null(value) -> bool:
    try:
        return bool(value.is_null())
    except Exception:
        return value is None


def _current_frame(scene) -> int:
    try:
        return int(scene.get_current_frame(False))
    except Exception:
        return int(scene.get_current_frame())


def _low_frame(view_scene) -> int:
    return int(timeline.boundary_info(view_scene)["first_frame"])


def _static_value(behaviour_viewer, data_viewer, object_id, behaviour_name, data_name):
    """Read a static (non-animated) behaviour value such as TPose position or symmetry_plane."""
    try:
        behaviour = behaviour_viewer.get_behaviour_by_name(object_id, behaviour_name)
        if behaviour is None or _is_null(behaviour):
            return None
        data = behaviour_viewer.get_behaviour_data(behaviour, data_name)
        if data is None or _is_null(data):
            return None
        return data_viewer.get_data_value(data)
    except Exception:
        return None


def _tpose_of(behaviour_viewer, data_viewer, object_id):
    """{"position", "rotation", "rotation_quaternion"} from the object's TPose, or None."""
    position = _static_value(behaviour_viewer, data_viewer, object_id, "TPose", "position")
    rotation = _static_value(behaviour_viewer, data_viewer, object_id, "TPose", "rotation")
    if position is None and rotation is None:
        return None
    result = {"position": None, "rotation": None, "rotation_quaternion": None}
    if position is not None:
        try:
            result["position"] = [float(value) for value in position]
        except (TypeError, ValueError):
            pass
    if rotation is not None:
        result["rotation"] = rotation
        try:
            quaternion = rotation.to_quaternion()
            result["rotation_quaternion"] = {
                "w": float(quaternion.w()),
                "x": float(quaternion.x()),
                "y": float(quaternion.y()),
                "z": float(quaternion.z()),
            }
        except Exception:
            pass
    return result


def _mirror_ids(entries, behaviour_viewer) -> list:
    """The controllers that have a Mirror behaviour (the MirrorTool's natural input)."""
    found = []
    for entry in entries:
        try:
            behaviour = behaviour_viewer.get_behaviour_by_name(entry["id"], "Mirror")
        except Exception:
            continue
        if behaviour is not None and not _is_null(behaviour):
            found.append(entry["id"])
    return found


def _mirror_core():
    """Cascadeur's MirrorTool core for the current scene."""
    import csc

    application = csc.app.get_application()
    editor = application.get_tools_manager().get_tool("MirrorTool").editor(application.current_scene())
    return editor.core()


def _rig_plane_axis():
    """The character's mirror plane axis from the rigging window, or None."""
    try:
        import csc

        application = csc.app.get_application()
        editor = application.get_tools_manager().get_tool("RiggingToolWindowTool").editor(application.current_scene())
        return axis_from_rig_plane(editor.get_character_mirror_plane())
    except Exception:
        return None


def _behaviour_plane_axis(entries, behaviour_viewer, data_viewer):
    """The axis in the first Mirror behaviour's symmetry_plane value, or None."""
    for entry in entries:
        value = _static_value(behaviour_viewer, data_viewer, entry["id"], "Mirror", "symmetry_plane")
        axis = axis_from_behaviour_plane(value)
        if axis:
            return axis
    return None


def _geometry_plane_axis(entries, by_id, scene, frame, low, index):
    """Fallback: the axis where mirrored main-point pairs differ most."""
    pairs = [
        (name, counterpart)
        for name, counterpart in index.items()
        if counterpart and counterpart != name and name < counterpart
    ]
    if not pairs:
        return None
    names = [name for pair in pairs for name in pair]
    points = pose._read_points(scene, [by_id[name] for name in names], frame, "world", by_id, low)
    totals = [0.0, 0.0, 0.0]
    counted = 0
    for index_number in range(0, len(points), 2):
        left = points[index_number].get("position")
        right = points[index_number + 1].get("position")
        if left is None or right is None:
            continue
        counted += 1
        for component in range(3):
            totals[component] += abs(float(left[component]) - float(right[component]))
    if not counted or max(totals) < 1.0:
        return None
    return ("x", "y", "z")[totals.index(max(totals))]


def _root_offset(entries, by_id, scene, frame, low, axis):
    """The character root's coordinate on axis (the mirror plane passes through it)."""
    component = AXES[axis]
    candidates = []
    for entry in entries:
        name = str(entry.get("name") or "")
        lowered = name.lower()
        if entry.get("alias") == "pelvis":
            candidates.insert(0, entry["id"])
        elif "pelvis" in lowered:
            candidates.append(entry["id"])
    for object_id in candidates:
        point = pose._read_points(scene, [object_id], frame, "world", by_id, low)[0]
        position = point.get("position")
        if position is not None:
            return float(position[component])
    return 0.0


def mirror_plane(scene, view_scene, entries, by_id, frame, low) -> dict:
    """The character's mirror plane: {axis, normal, offset, point, source}."""
    behaviour_viewer = scene.model_viewer().behaviour_viewer()
    data_viewer = scene.model_viewer().data_viewer()
    axis = _rig_plane_axis()
    source = "RiggingToolWindowTool.get_character_mirror_plane"
    if axis is None:
        axis = _behaviour_plane_axis(entries, behaviour_viewer, data_viewer)
        source = "Mirror behaviour symmetry_plane"
    if axis is None:
        axis = _geometry_plane_axis(entries, by_id, scene, frame, low, mirror_index(entries))
        source = "main-point pair geometry"
    if axis is None:
        axis = "x"
        source = "default x (no mirror plane information in the scene)"
    offset = _root_offset(entries, by_id, scene, frame, low, axis)
    plane = plane_for_axis(axis, offset)
    plane["source"] = source
    return plane


def _name_of(entries_by_id, model_viewer, object_id):
    entry = entries_by_id.get(object_id)
    if entry:
        return entry.get("name")
    try:
        return model_viewer.get_object_name(object_id)
    except Exception:
        return None


def _selected_watch_ids(entries, requested_ids):
    """The objects a pose operation reports on (the requested ones, or every main point)."""
    if requested_ids:
        return list(requested_ids)
    return [entry["id"] for entry in entries if entry.get("kind") == "main_point"]


def _frame_missing(points) -> list[str]:
    """Names of controllers whose values at the requested frame came from another frame."""
    return [str(point.get("name")) for point in points if "frame_used" in point]


def _needs_extension_points(points) -> bool:
    return any("frame_used" in point for point in points)


def _merge_targets(groups):
    """Merge pose._requested_targets results (by object id), keeping the first order."""
    merged = []
    by_id = {}
    for group in groups:
        for target in group:
            existing = by_id.get(target["id"])
            if existing is None:
                by_id[target["id"]] = target
                merged.append(target)
                continue
            existing["writes"].extend(target["writes"])
            for key in (
                "requested_position",
                "requested_rotation",
                "before_position",
                "before_rotation",
                "before_frame",
            ):
                if key in target and key not in existing:
                    existing[key] = target[key]
    return merged


def _run_edit(undo_name, scene, view_scene, ids, targets, frame, key, extend, phases=None):
    """Write targets in one "MCP: <undo_name>" undo step, keying and extending if needed.

    phases optionally splits the targets into several write+update passes inside the same undo
    step. reset_pose uses it: the main-point writes make the rig recompute box orientations, so
    the boxes have to be written again after them (measured on Cascy: one pass left the finger
    boxes 33 cm away from the TPose, a second box pass put them back exactly).
    """
    stats = {"keys": None, "extended_to": None, "undo": None}
    groups = [list(group) for group in phases] if phases else [targets]

    def mod(model_editor, update_editor, scene_updater, session):
        data_editor = model_editor.data_editor()
        written = set()
        if extend:
            stats["extended_to"] = pose._extend_data_range(view_scene, scene, model_editor, scene_updater, frame)
        if key:
            stats["keys"] = timeline.set_key_at(model_editor, ids, frame)
        for group in groups:
            actuals = set()
            for target in group:
                for write in target["writes"]:
                    if write["kind"] == "rotation":
                        if write.get("value_rotation") is not None:
                            value = write["value_rotation"]
                        else:
                            value = pose._rotation_value(write["value"])
                    else:
                        value = pose._vector_value(write.get("raw"), write["value"])
                    data_editor.set_data_value(write["data"], frame, value)
                    actuals.add(write["data"])
            written.update(actuals)
            scene_updater.run_update(actuals, frame)
        if key:
            model_editor.set_fixed_interpolation_if_need(written, frame)

    step = pose._edit_step(undo_name, mod)
    stats["undo"] = step.get("undo")
    return step, stats


def _unexpected(action, exc, **extra):
    return common.fail(
        f"Could not {action}: {type(exc).__name__}: {exc}",
        hint=(
            "Check that a rigged scene is open and that no dialog is blocking Cascadeur; the "
            "traceback is below (get_log returns the same lines)."
        ),
        traceback=traceback.format_exc(),
        log=common.log_tail(lines=80),
        **extra,
    )


def _undo_report(steps, note=None):
    report = {"undo_steps": list(steps), "undo_count": len(steps)}
    if note:
        report["undo_note"] = note
    return report


# ---------------------------------------------------------------------------
# Target building for reset_pose
# ---------------------------------------------------------------------------


def _entry_by_name(entries, name):
    for entry in entries:
        if entry.get("name") == name:
            return entry
    return {}


def _entry_of(entries, object_id):
    for entry in entries:
        if entry["id"] == object_id:
            return entry
    return {}


def _quaternion_of(values):
    """csc Quaternion from a {"w","x","y","z"} dict."""
    import csc

    return csc.math.Quaternion(float(values["w"]), float(values["x"]), float(values["y"]), float(values["z"]))


def _euler_degrees(rotation):
    try:
        return [math.degrees(float(angle)) for angle in rotation.to_euler_angles_x_y_z()]
    except Exception:
        return None


def _default_targets(entries, scene, ids):
    """Build the TPose writes for ids (Cascadeur's Go to T-pose recipe).

    Returns (targets, expected_world_positions, skipped). Every object gets its TPose global
    position; boxes and joints also get the matching local position/rotation computed against
    their parent's TPose transform, which is the command's recipe. Main control points only have a
    global position in Cascadeur 2026.2.3, so that is all they get.
    """
    import csc

    model_viewer = scene.model_viewer()
    behaviour_viewer = model_viewer.behaviour_viewer()
    data_viewer = model_viewer.data_viewer()
    infos = {}
    parents = {}
    for object_id in ids:
        if object_id not in infos:
            infos[object_id] = _tpose_of(behaviour_viewer, data_viewer, object_id)
        parent = pose._parent_object(behaviour_viewer, object_id)
        parents[object_id] = parent
        if parent is not None and parent not in infos:
            infos[parent] = _tpose_of(behaviour_viewer, data_viewer, parent)
    targets = []
    expected = {}
    skipped = []
    for object_id in ids:
        entry = _entry_of(entries, object_id)
        name = entry.get("name") or str(object_id)
        info = infos.get(object_id)
        if not info or info.get("position") is None:
            skipped.append(name)
            continue
        writes = []
        world_position, _source = pose._position_data(behaviour_viewer, object_id, "world")
        local_position, _source = pose._position_data(behaviour_viewer, object_id, "local")
        local_rotation, _source = pose._rotation_data(behaviour_viewer, object_id, "local")
        world_rotation, _source = pose._rotation_data(behaviour_viewer, object_id, "world")
        parent = parents.get(object_id)
        parent_info = infos.get(parent) if parent is not None else None
        # The box chain's parent is the "Boxes" group, which has no Transform behaviour: an object
        # whose parent has no transform effectively lives in world space, so its local transform is
        # its global one. Treating it as a normal child left the whole box chain (and the fingers)
        # in the old pose when this was measured on Cascy.
        parent_has_transform = False
        if parent is not None:
            try:
                parent_has_transform = pose._transform_of(behaviour_viewer, parent) is not None
            except Exception:
                parent_has_transform = False
        root_like = parent is None or not parent_has_transform
        # Write the global position of every object: the channel is enough for main control
        # points, and for boxes/joints the matching local transform is computed below so the
        # hierarchy reproduces the TPose.
        if world_position is not None:
            writes.append({"kind": "position", "data": world_position, "value": list(info["position"])})
        if root_like:
            if local_position is not None:
                writes.append({"kind": "position", "data": local_position, "value": list(info["position"])})
            if local_rotation is not None and info.get("rotation") is not None:
                writes.append({"kind": "rotation", "data": local_rotation, "value_rotation": info["rotation"]})
        elif (
            local_position is not None
            and parent_info
            and parent_info.get("position") is not None
            and parent_info.get("rotation_quaternion")
            and info.get("rotation_quaternion")
        ):
            parent_transform = csc.math.OrthogonalTransform(
                parent_info["position"], _quaternion_of(parent_info["rotation_quaternion"])
            )
            own_transform = csc.math.OrthogonalTransform(info["position"], _quaternion_of(info["rotation_quaternion"]))
            local = csc.math.transforms_difference(parent_transform, own_transform)
            writes.append(
                {"kind": "position", "data": local_position, "value": [float(value) for value in local.position]}
            )
            if local_rotation is not None:
                writes.append(
                    {
                        "kind": "rotation",
                        "data": local_rotation,
                        "value_rotation": csc.math.Rotation.from_quaternion(local.rotation),
                    }
                )
        elif local_rotation is not None and info.get("rotation") is not None and world_rotation is not None:
            # The parent has a Transform but no TPose rotation (main control points store no
            # rotation), so the local rotation cannot be computed; write the global one best-effort.
            writes.append({"kind": "rotation", "data": world_rotation, "value_rotation": info["rotation"]})
        if not writes:
            skipped.append(name)
            continue
        targets.append({"id": object_id, "name": name, "writes": writes, "before_frame": None})
        expected[object_id] = list(info["position"])
    return targets, expected, skipped


def _bind_matrices(scene):
    """{joint object id: csc.math.ScaledTransform} from the skinned meshes' inverse bind matrices."""
    import csc

    try:
        import numpy as np
    except Exception:  # numpy ships with Cascadeur; unit tests run without it
        return {}

    model_viewer = scene.model_viewer()
    behaviour_viewer = model_viewer.behaviour_viewer()
    assets = scene.assets_manager()
    found = {}
    for object_id in model_viewer.get_objects():
        try:
            mesh = behaviour_viewer.get_behaviour_by_name(object_id, "MeshObject")
        except Exception:
            continue
        if mesh is None or _is_null(mesh):
            continue
        try:
            dependency = behaviour_viewer.get_behaviour_asset(mesh, "mesh_dependency")
        except Exception:
            continue
        if dependency is None or _is_null(dependency):
            continue
        try:
            joints = list(behaviour_viewer.get_behaviour_objects_range(mesh, "linked_objects"))
        except Exception:
            continue
        try:
            matrices = assets.at(dependency).inverse_bind_matrices()
        except Exception:
            continue
        for index, joint in enumerate(joints):
            if index >= len(matrices) or joint in found:
                continue
            try:
                matrix = np.array(matrices[index], dtype=float)
                if matrix.size == 16:
                    matrix = matrix.reshape(4, 4)
                bind = np.linalg.inv(matrix)
                found[joint] = csc.math.decompose_matrix(bind)
            except Exception:
                continue
    return found


def _bind_targets(entries, scene, ids):
    """Build joint global position/rotation writes from the inverse bind matrices."""
    model_viewer = scene.model_viewer()
    behaviour_viewer = model_viewer.behaviour_viewer()
    binds = _bind_matrices(scene)
    targets = []
    expected = {}
    skipped = []
    for object_id in ids:
        entry = _entry_of(entries, object_id)
        name = entry.get("name") or str(object_id)
        bind = binds.get(object_id)
        if bind is None:
            skipped.append(name)
            continue
        writes = []
        position_data, _source = pose._position_data(behaviour_viewer, object_id, "world")
        rotation_data, _source = pose._rotation_data(behaviour_viewer, object_id, "world")
        if position_data is None or rotation_data is None:
            skipped.append(name)
            continue
        writes.append({"kind": "position", "data": position_data, "value": [float(value) for value in bind.position]})
        writes.append({"kind": "rotation", "data": rotation_data, "value_rotation": bind.rotation})
        targets.append({"id": object_id, "name": name, "writes": writes, "before_frame": None})
        expected[object_id] = [float(value) for value in bind.position]
    return targets, expected, skipped


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def mirror_pose(frame=None, controllers=None) -> dict:
    """Mirror the character's pose across its mirror plane at a frame (native MirrorTool)."""
    try:
        wanted_frame = None if frame is None else timeline.parse_frame(frame, "frame")
        requested = pose._names(controllers)
    except ToolError as exc:
        return common.fail(
            str(exc),
            hint="Pass a whole frame number and controller names or aliases (list_controllers shows them).",
        )
    try:
        _app, view_scene, scene = common.scenes()
        entries, by_id = pose._controller_entries()
        model_viewer = scene.model_viewer()
        behaviour_viewer = model_viewer.behaviour_viewer()
        if requested:
            try:
                requested_ids = rig.resolve(requested)
            except rig.RigError as exc:
                return exc.result()
        else:
            requested_ids = []
        actual_frame = _current_frame(scene) if wanted_frame is None else int(wanted_frame)
        low = _low_frame(view_scene)
        mirror_ids = _mirror_ids(entries, behaviour_viewer)
        if not mirror_ids:
            return common.fail(
                "This scene has no controller with a Mirror behaviour, so it cannot be mirrored.",
                hint="Open a rigged character (Cascy.casc and the other samples have one) and retry.",
            )
        blocked = ensure_unlocked(mirror_ids)
        if blocked is not None:
            return blocked
        index = mirror_index(entries)
        watch_ids = _selected_watch_ids(entries, requested_ids)
        extra = []
        for object_id in watch_ids:
            entry = _entry_of(entries, object_id)
            counterpart = index.get(entry.get("name"))
            if counterpart and counterpart != entry.get("name"):
                counterpart_entry = next((item for item in entries if item.get("name") == counterpart), None)
                if (
                    counterpart_entry
                    and counterpart_entry["id"] not in watch_ids
                    and counterpart_entry["id"] not in extra
                ):
                    extra.append(counterpart_entry["id"])
        check_ids = set(requested_ids)
        if not check_ids:
            check_ids = {entry["id"] for entry in entries if entry.get("kind") == "main_point"}
        watch_ids = watch_ids + extra
        before_points = pose._read_points(scene, watch_ids, actual_frame, "world", by_id, low)
        missing = [
            point.get("name")
            for point, object_id in zip(before_points, watch_ids, strict=True)
            if "frame_used" in point and object_id in check_ids
        ]
        if missing:
            return common.fail(
                f"The scene has no pose data at frame {actual_frame} for {len(missing)} controller(s) "
                f"(for example {missing[0]!r}).",
                hint=(
                    "Key the frame first (set_pose with key=true, or set_key), then mirror. Mirroring a frame "
                    "with no data would silently mirror values from another frame."
                ),
                frame=actual_frame,
                controllers=missing[:10],
                undo_steps=[],
                undo_count=0,
            )
        plane = mirror_plane(scene, view_scene, entries, by_id, actual_frame, low)
        previous_frame = _current_frame(scene)
        changed_frame = previous_frame != actual_frame
        if changed_frame:
            scene.set_current_frame(actual_frame)
        try:
            core = _mirror_core()
            core.set_plane(csc_plane(plane))
            core.mirror_frame(set(mirror_ids))
        except Exception as exc:  # noqa: BLE001 - tool boundary
            return _unexpected("mirror the pose", exc, **_undo_report([], None))
        finally:
            if changed_frame:
                try:
                    scene.set_current_frame(previous_frame)
                except Exception:
                    pass

        after_points = pose._read_points(scene, watch_ids, actual_frame, "world", by_id)
        after_by_name = {point.get("name"): point for point in after_points}
        reported = []
        unpaired = []
        errors = []
        changed = 0
        for point in before_points:
            name = point.get("name")
            counterpart = index.get(name)
            entry = _entry_by_name(entries, name)
            if counterpart is None:
                unpaired.append(name)
                continue
            if counterpart not in after_by_name:
                continue
            expected = None
            error = None
            if point.get("position") is not None:
                expected = reflect_point(point["position"], plane)
                actual = after_by_name[counterpart].get("position")
                if actual is not None:
                    error = pose.distance(actual, expected)
                    errors.append(error)
            before_position = point.get("position")
            after_position = after_by_name[counterpart].get("position")
            moved = (
                pose.distance(after_position, before_position)
                if after_position is not None and before_position is not None
                else 0.0
            )
            if moved > MOVED_CM:
                changed += 1
            reported.append(
                {
                    "name": name,
                    "mirror_of": counterpart,
                    "alias": entry.get("alias"),
                    "kind": entry.get("kind"),
                    "before_position": before_position,
                    "after_position": after_position,
                    "expected_mirror_position": expected,
                    "symmetry_error_cm": None if error is None else round(error, 4),
                    "moved_cm": round(moved, 4),
                }
            )
        result = common.ok(
            frame=actual_frame,
            scope="whole character",
            mirrorable_controllers=len(mirror_ids),
            reported_controllers=len(reported),
            controllers=reported,
            unpaired=unpaired,
            plane=plane,
            max_symmetry_error_cm=None if not errors else round(max(errors), 4),
            tolerance_cm=MIRROR_TOLERANCE_CM,
            changed=changed,
            undo=MIRROR_UNDO,
            keyed=None,
            **_undo_report([MIRROR_UNDO], MIRROR_UNDO_NOTE),
        )
        if errors and max(errors) > MIRROR_TOLERANCE_CM:
            result["hint"] = (
                f"The mirrored pose ended {max(errors):.2f} cm from the exact mirror position; check the "
                "reported plane and that the rig is symmetric."
            )
        if requested:
            result["requested"] = common.jsonable(requested)
            result["hint"] = (result.get("hint") + " " if result.get("hint") else "") + MIRROR_HINT
        return common.jsonable(result)
    except ToolError as exc:
        return common.fail(str(exc), hint="Check get_scene_info and list_controllers, then retry.")
    except Exception as exc:  # noqa: BLE001 - tool boundary
        return _unexpected("mirror the pose", exc)


def csc_plane(plane):
    """csc.math.Plane for a plane dict."""
    import csc

    return csc.math.Plane(plane["normal"], plane["point"])


def copy_pose(frame, controllers=None) -> dict:
    """Store a frame's pose in this server under a generated name and return that name."""
    try:
        wanted_frame = timeline.parse_frame(frame, "frame")
        names = pose._names(controllers)
    except ToolError as exc:
        return common.fail(
            str(exc),
            hint="Pass a whole frame number (0 or more); controllers accepts names or aliases and defaults "
            "to the main control points.",
        )
    try:
        _app, view_scene, scene = common.scenes()
        entries, by_id = pose._controller_entries()
        if names:
            try:
                ids = rig.resolve(names)
            except rig.RigError as exc:
                return exc.result()
        else:
            ids = [entry["id"] for entry in entries if entry.get("kind") == "main_point"]
        low = _low_frame(view_scene)
        world = pose._read_points(scene, ids, wanted_frame, "world", by_id, low)
        missing = _frame_missing(world)
        if missing:
            return common.fail(
                f"The scene has no pose data at frame {wanted_frame} for {len(missing)} controller(s) "
                f"(for example {missing[0]!r}).",
                hint=(
                    "Key that frame first (set_key or set_pose) or copy a frame that already has keys; "
                    "copying would otherwise take values from another frame."
                ),
                frame=wanted_frame,
                controllers=missing[:10],
            )
        local = pose._read_points(scene, ids, wanted_frame, "local", by_id)
        stored_controllers = []
        for world_point, local_point in zip(world, local, strict=True):
            stored_controllers.append(
                {
                    "name": world_point.get("name"),
                    "alias": world_point.get("alias"),
                    "kind": world_point.get("kind"),
                    "position": world_point.get("position"),
                    "rotation": local_point.get("rotation"),
                    "quaternion": world_point.get("quaternion"),
                }
            )
        name = next_pose_name()
        try:
            scene_name = str(view_scene.name())
        except Exception:
            scene_name = None
        stored = {
            "name": name,
            "frame": wanted_frame,
            "controllers": stored_controllers,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "scene": scene_name,
        }
        _STORE[name] = stored
        result = common.ok(
            name=name,
            frame=wanted_frame,
            count=len(stored_controllers),
            controllers=stored_controllers,
            stored=len(_STORE),
            note=(
                "The pose is kept in MCP Plus's memory (world positions in cm, local rotations in "
                "degrees); paste it with paste_pose(frame, name=...). It is not a file and is lost when "
                "MCP Plus restarts."
            ),
        )
        if any(item.get("rotation") is None for item in stored_controllers):
            result["hint"] = NO_ROTATION_NOTE
        return common.jsonable(result)
    except ToolError as exc:
        return common.fail(str(exc), hint="Check get_scene_info and list_controllers, then retry.")
    except Exception as exc:  # noqa: BLE001 - tool boundary
        return _unexpected("copy the pose", exc)


def _find_pose(name):
    """(stored pose, failure) for a paste call."""
    if not _STORE:
        return None, common.fail(
            "No pose has been copied in this MCP Plus session yet.",
            hint="Call copy_pose(frame=<frame>) first; it returns the name to pass to paste_pose.",
        )
    if name is None or str(name).strip() == "":
        latest = next(reversed(_STORE))
        return _STORE[latest], None
    key = str(name).strip()
    stored = _STORE.get(key)
    if stored is None:
        return None, common.fail(
            f"No copied pose named {key!r}.",
            hint="Pass one of the stored names or leave name out to paste the most recent copy.",
            available=[pose_summary(item) for item in _STORE.values()],
        )
    return stored, None


def paste_pose(frame, name=None, mirrored=False, key=True) -> dict:
    """Paste a stored pose at a frame, optionally mirrored, as one undo step."""
    try:
        wanted_frame = timeline.parse_frame(frame, "frame")
        wanted_mirrored = parse_boolean(mirrored, "mirrored")
        wanted_key = parse_boolean(key, "key")
    except ToolError as exc:
        return common.fail(
            str(exc),
            hint="Pass a whole frame number, mirrored true/false and key true/false.",
        )
    stored, failure = _find_pose(name)
    if failure is not None:
        return failure
    try:
        _app, view_scene, scene = common.scenes()
        entries, by_id = pose._controller_entries()
        index = mirror_index(entries)
        stored_controllers = stored.get("controllers") or []
        source_names = [item.get("name") for item in stored_controllers]
        try:
            source_ids = rig.resolve(source_names)
        except rig.RigError as exc:
            result = exc.result()
            result["hint"] = (
                f"The stored pose was copied from {stored.get('scene') or 'another scene'} at frame "
                f"{stored.get('frame')}; the current scene does not have all of its controllers. "
                + (result.get("hint") or "")
            )
            return result
        paste_plane = (
            mirror_plane(scene, view_scene, entries, by_id, wanted_frame, _low_frame(view_scene))
            if wanted_mirrored
            else None
        )
        plan = []
        skipped = []
        rotations_skipped = 0
        target_names = []
        target_positions = []
        target_rotations = []
        for item, _source_id in zip(stored_controllers, source_ids, strict=True):
            source_name = item.get("name")
            target_name = index.get(source_name) if wanted_mirrored else source_name
            position = item.get("position")
            if target_name is None:
                skipped.append({"name": source_name, "reason": "no mirror counterpart"})
                continue
            if position is None:
                skipped.append({"name": source_name, "reason": "no stored position"})
                continue
            if wanted_mirrored:
                position = reflect_point(position, paste_plane)
            if target_name in target_names:
                return common.fail(
                    f"Two copied controllers would map to {target_name!r} when mirrored.",
                    hint="Copy a controller set where every left/right pair appears once, then retry.",
                )
            target_names.append(target_name)
            target_positions.append((target_name, {"position": list(position)}))
            rotation = item.get("rotation")
            if rotation is not None:
                if wanted_mirrored:
                    rotations_skipped += 1
                else:
                    target_rotations.append((target_name, {"rotation": list(rotation)}))
            plan.append(
                {
                    "source": source_name,
                    "target": target_name,
                    "requested_position": list(position),
                    "requested_rotation": None if wanted_mirrored else rotation,
                }
            )
        if not target_names:
            return common.fail(
                "None of the copied controllers could be pasted into this scene.",
                hint="Check that the current scene is the one the pose was copied from.",
                skipped=skipped,
            )
        target_ids = rig.resolve(target_names)
        blocked = ensure_unlocked(target_ids)
        if blocked is not None:
            return blocked
        low = _low_frame(view_scene)
        current = pose._read_points(scene, target_ids, wanted_frame, "world", by_id, low)
        extend = _needs_extension_points(current)
        targets, failure = pose._requested_targets(
            scene, target_ids, target_positions, wanted_frame, "world", key=wanted_key, low=low
        )
        if failure is not None:
            return failure
        groups = [targets]
        if target_rotations:
            rotation_targets, failure = pose._requested_targets(
                scene, target_ids, target_rotations, wanted_frame, "local", key=wanted_key, low=low
            )
            if failure is not None:
                return failure
            groups.append(rotation_targets)
        merged = _merge_targets(groups)
        extend = extend or pose._needs_extension(merged, wanted_frame)
        step, stats = _run_edit(PASTE_UNDO, scene, view_scene, target_ids, merged, wanted_frame, wanted_key, extend)
        if not step.get("ok"):
            result = step.get("result") if isinstance(step.get("result"), dict) else step
            result.update(_undo_report([], None))
            return result
        after = pose._read_points(scene, target_ids, wanted_frame, "world", by_id)
        after_by_name = {point.get("name"): point for point in after}
        after_local = pose._read_points(scene, target_ids, wanted_frame, "local", by_id)
        local_by_name = {point.get("name"): point for point in after_local}
        reported = []
        errors = []
        for item in plan:
            target = after_by_name.get(item["target"])
            local = local_by_name.get(item["target"])
            entry = {"name": item["target"], "source": item["source"]}
            actual_position = None if target is None else target.get("position")
            if actual_position is not None:
                error = pose.distance(actual_position, item["requested_position"])
                errors.append(error)
                entry["after_position"] = actual_position
                entry["position_error_cm"] = round(error, 4)
            entry["requested_position"] = item["requested_position"]
            entry["requested_rotation"] = item["requested_rotation"]
            if item["requested_rotation"] is not None and local is not None and local.get("rotation") is not None:
                actual_rotation = local.get("rotation")
                entry["after_rotation"] = actual_rotation
                differences = [
                    abs(pose.angle_difference(actual, requested))
                    for actual, requested in zip(actual_rotation, item["requested_rotation"], strict=True)
                ]
                entry["rotation_error_deg"] = round(max(differences), 4)
            reported.append(entry)
        result = common.ok(
            name=stored.get("name"),
            source_frame=stored.get("frame"),
            frame=wanted_frame,
            mirrored=wanted_mirrored,
            plane=paste_plane,
            keyed=wanted_key,
            controllers=reported,
            count=len(reported),
            skipped=skipped,
            rotations_skipped=rotations_skipped,
            extended_to=stats.get("extended_to"),
            keys=stats.get("keys"),
            undo=step.get("undo"),
            **_undo_report([step.get("undo") or "MCP: " + PASTE_UNDO], UNDO_NOTE.format(name=PASTE_UNDO)),
        )
        hints = []
        if errors and max(errors) > MIRROR_TOLERANCE_CM:
            hints.append(
                f"Some controllers ended up to {max(errors):.2f} cm from the copied position; the rig may have "
                "pulled them back (check the keys and the layer locks)."
            )
        if skipped:
            hints.append(
                f"{len(skipped)} controller(s) were skipped because they have no mirror counterpart or no "
                "stored position (see skipped)."
            )
        if rotations_skipped:
            hints.append(
                f"mirrored=true pasted positions only: {rotations_skipped} stored rotation(s) were not "
                "mirrored. Use mirror_pose for a full mirrored pose including box/joint rotations."
            )
        if stored.get("scene") and stored["scene"] != _scene_name(view_scene):
            hints.append(
                f"The pose was copied from {stored['scene']!r} (frame {stored.get('frame')}); check that the "
                "two characters use the same controller names."
            )
        if hints:
            result["hint"] = " ".join(hints)
        return common.jsonable(result)
    except ToolError as exc:
        return common.fail(str(exc), hint="Check get_scene_info and list_controllers, then retry.")
    except Exception as exc:  # noqa: BLE001 - tool boundary
        return _unexpected("paste the pose", exc)


def _scene_name(view_scene):
    try:
        return str(view_scene.name())
    except Exception:
        return None


def reset_pose(kind="default", controllers=None, frame=None) -> dict:
    """Reset controllers to the rig's default (T-pose) or bind pose at a frame."""
    try:
        wanted_kind = parse_kind(kind)
        wanted_frame = None if frame is None else timeline.parse_frame(frame, "frame")
        names = pose._names(controllers)
    except ToolError as exc:
        return common.fail(
            str(exc),
            hint="kind is 'default' (the rig's T-pose) or 'bind' (the skinned meshes' bind pose); frame is a "
            "whole frame number and controllers accepts names or aliases.",
        )
    try:
        _app, view_scene, scene = common.scenes()
        entries, by_id = pose._controller_entries()
        if names:
            try:
                ids = rig.resolve(names)
            except rig.RigError as exc:
                return exc.result()
        else:
            if wanted_kind == KIND_BIND:
                ids = [entry["id"] for entry in entries if entry.get("kind") == "joint"]
            else:
                ids = [entry["id"] for entry in entries if entry.get("kind") in DEFAULT_CONTROLLERS]
        if not ids:
            return common.fail(
                f"This scene has no controllers to reset to the {wanted_kind} pose.",
                hint="Open a rigged character and pass controllers (list_controllers shows them).",
            )
        blocked = ensure_unlocked(ids)
        if blocked is not None:
            return blocked
        actual_frame = _current_frame(scene) if wanted_frame is None else int(wanted_frame)
        low = _low_frame(view_scene)
        current = pose._read_points(scene, ids, actual_frame, "world", by_id, low)
        extend = _needs_extension_points(current)
        if wanted_kind == KIND_BIND:
            targets, expected, skipped = _bind_targets(entries, scene, ids)
        else:
            targets, expected, skipped = _default_targets(entries, scene, ids)
        if not targets:
            return common.fail(
                f"No {wanted_kind} pose data was found for the requested controllers.",
                hint=(
                    "The character may not store TPose data; check the scene and list_controllers."
                    if wanted_kind == KIND_DEFAULT
                    else "Bind pose needs a skinned mesh with inverse bind matrices and joints."
                ),
                skipped=skipped[:20],
            )
        written_ids = [target["id"] for target in targets]
        # A second pass over the boxes/joints: writing the main control points makes the rig
        # recompute box orientations, so the box transforms have to be applied again afterwards.
        second_pass = [target for target in targets if _entry_of(entries, target["id"]).get("kind") != "main_point"]
        phases = [targets] + ([second_pass] if second_pass else [])
        step, stats = _run_edit(
            RESET_UNDO, scene, view_scene, written_ids, targets, actual_frame, True, extend, phases=phases
        )
        if not step.get("ok"):
            result = step.get("result") if isinstance(step.get("result"), dict) else step
            result.update(_undo_report([], None))
            return result
        after = pose._read_points(scene, written_ids, actual_frame, "world", by_id)
        after_by_name = {point.get("name"): point for point in after}
        reported = []
        errors = []
        for target in targets:
            entry = _entry_of(entries, target["id"])
            name = entry.get("name") or target["name"]
            point = after_by_name.get(name)
            actual = None if point is None else point.get("position")
            item = {"name": name, "kind": entry.get("kind")}
            wanted_position = expected.get(target["id"])
            if actual is not None and wanted_position is not None:
                error = pose.distance(actual, wanted_position)
                errors.append(error)
                item["after_position"] = actual
                item["expected_position"] = wanted_position
                item["position_error_cm"] = round(error, 4)
            reported.append(item)
        result = common.ok(
            kind=wanted_kind,
            frame=actual_frame,
            controllers=reported,
            count=len(reported),
            skipped=skipped[:50],
            extended_to=stats.get("extended_to"),
            keys=stats.get("keys"),
            undo=step.get("undo"),
            **_undo_report([step.get("undo") or "MCP: " + RESET_UNDO], UNDO_NOTE.format(name=RESET_UNDO)),
        )
        if skipped:
            result["hint"] = (
                f"{len(skipped)} controller(s) had no {wanted_kind} pose data and were left unchanged "
                "(see skipped). Note: TPose resets use the stored T-pose, which is usually not the pose the "
                "scene was saved in."
            )
        if errors and max(errors) > 5.0:
            result["hint"] = (
                (result.get("hint") + " " if result.get("hint") else "")
                + f"Some controllers ended up to {max(errors):.2f} cm from the stored {wanted_kind} pose; the rig "
                "may have pulled them back (check the keys and the layer locks)."
            )
        return common.jsonable(result)
    except ToolError as exc:
        return common.fail(str(exc), hint="Check get_scene_info and list_controllers, then retry.")
    except Exception as exc:  # noqa: BLE001 - tool boundary
        return _unexpected("reset the pose", exc)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

NAME_OR_ALIAS = {
    "type": ["string", "array"],
    "items": {"type": "string"},
    "description": (
        "Controller name(s) or alias(es), for example left_hand or "
        '["left_hand", "right_hand"]; omit for the main control points.'
    ),
}
FRAME_OPTIONAL = {
    "type": ["integer", "string"],
    "description": "Frame to use (0 or more); omit for the current frame. The playhead is not moved.",
}

TOOLS = [
    {
        "name": "mirror_pose",
        "description": (
            "Mirror the character's pose left-to-right at a frame (raise the left arm and mirror_pose gives the "
            "same pose on the right). Use it for symmetric poses, walk cycles and copying one side of a pose to "
            "the other. It uses Cascadeur's own MirrorTool with the character's mirror plane, so boxes and joints "
            "rotate correctly, and it mirrors the whole character: controllers only choose which mirrored "
            "controllers the result reports, because mirroring part of a rig is not coherent in Cascadeur. "
            "positions are cm and the reported plane is the world-space plane the mirror used. The frame must "
            "already have data (key it with set_pose or set_key first). Returns the plane, each reported "
            "controller's before/after position, the expected mirror position and symmetry_error_cm, the "
            "unpaired controllers, and the undo step. The undo step is Cascadeur's native 'Mirror' step (one "
            "Ctrl+Z restores the pose); Python cannot rename it to 'MCP: ...'."
        ),
        "inputSchema": schema(
            {
                "frame": FRAME_OPTIONAL,
                "controllers": NAME_OR_ALIAS,
            }
        ),
        "fn": mirror_pose,
    },
    {
        "name": "copy_pose",
        "description": (
            "Copy the character's pose at a frame into MCP Plus's own pose store, so it can be pasted later "
            "with paste_pose. positions are stored in cm (world) and rotations in degrees (local); main control "
            "points have no rotation, so their rotation is null. controllers accepts names and aliases and "
            "defaults to the main control points. The frame must already have data (key it first). Each call "
            "stores a new named copy (pose_1, pose_2, ...) and returns the name plus the stored values. "
            "Read-only: it never changes the pose, the keys or the playhead; the copies live in MCP Plus's "
            "memory and are lost when the server restarts."
        ),
        "inputSchema": schema(
            {
                "frame": {"type": ["integer", "string"], "description": "Frame to copy (0 or more)."},
                "controllers": NAME_OR_ALIAS,
            },
            ["frame"],
        ),
        "fn": copy_pose,
    },
    {
        "name": "paste_pose",
        "description": (
            "Paste a pose copied with copy_pose at another frame, as one undo step. positions are written in cm "
            "(world) and rotations in degrees (local); key=true (the default) keys the controllers at the frame, "
            "key=false changes only existing keys. mirrored=true mirrors the stored positions across the "
            "character's mirror plane (for the default main-point copy this is exactly a mirrored pose; stored "
            "box/joint rotations are not mirrored by paste_pose, use mirror_pose on the live rig for those). "
            "name is the value copy_pose returned; omit it to paste the most recent copy. The frame is made "
            "writable when the scene has no data there yet (extended_to reports the frame the layers were "
            "extended to) and the whole call is the single undo step 'MCP: paste_pose'. Returns each "
            "controller's requested and actual position with position_error_cm, the keys, skipped controllers "
            "and the undo step."
        ),
        "inputSchema": schema(
            {
                "frame": {"type": ["integer", "string"], "description": "Frame to paste at (0 or more)."},
                "name": {
                    "type": "string",
                    "description": "Name returned by copy_pose (for example pose_1); omit for the most recent copy.",
                },
                "mirrored": {
                    "type": "boolean",
                    "description": "true mirrors the stored positions across the character's mirror plane.",
                },
                "key": {
                    "type": "boolean",
                    "description": "true (default) keys the pasted controllers at the frame; false changes existing keys only.",
                },
            },
            ["frame"],
        ),
        "fn": paste_pose,
    },
    {
        "name": "reset_pose",
        "description": (
            "Reset controllers to the character's default pose or bind pose at a frame. kind='default' writes "
            "the rig's stored T-pose (the TPose behaviour: main control points get their TPose world position, "
            "boxes and joints their local transforms against the parent's TPose, the same recipe as Cascadeur's "
            "Go to T-pose command). kind='bind' writes joint global transforms from the skinned meshes' inverse "
            "bind matrices, like Cascadeur's Go to Bind Pose command. controllers accepts names and aliases; "
            "default resets every main point, box and joint, and bind resets every joint. The frame is keyed and "
            "made writable when needed, and the whole call is the single undo step 'MCP: reset_pose'. positions "
            "are cm; returns each controller's actual position, expected_position and position_error_cm, the "
            "skipped controllers (no TPose/bind data) and the undo step. The default T-pose is usually not the "
            "pose the scene was saved in; use set_pose with a copied pose to return to a specific animation pose."
        ),
        "inputSchema": schema(
            {
                "kind": {
                    "type": "string",
                    "enum": list(KINDS),
                    "description": "default (the rig's T-pose) or bind (the meshes' bind pose).",
                },
                "controllers": NAME_OR_ALIAS,
                "frame": FRAME_OPTIONAL,
            }
        ),
        "fn": reset_pose,
    },
]
