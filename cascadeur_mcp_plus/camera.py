"""Viewport camera position, preset views and framing (T31).

The camera tools work on the active tab's main 3D viewport. Its camera is a
csc.view.SphericalCameraStruct (position, target and type) on the viewport;
set_camera_struct() writes it back. There is no roll field, so a view is fully described by
where the camera is and what it looks at.

The geometry at the top of this module is plain Python and is unit tested without Cascadeur
(tests/test_camera.py). It answers the questions the tools need:

* where is "front" for a character? It comes from the character itself, not from the world axes:
  the pelvis-to-chest line is the body's up axis and the left/right shoulder line is its side
  axis, so the face direction follows the character when it turns (the pelvis and chest are the
  primary source; hands, knees or feet take over when the shoulders are not usable);
* how far must the camera be to fit a set of points in a viewport with a given vertical field of
  view and aspect ratio?

The scene-facing helpers at the bottom import cascadeur_mcp_plus.common/rig lazily and must run
on Cascadeur's GUI thread, like every other tool function.
"""

from __future__ import annotations

import math

VIEWS = ("front", "back", "left", "right", "top", "three_quarter")
WORLD_UP = (0.0, 1.0, 0.0)
DEFAULT_FOV_DEG = 30.0
DEFAULT_ASPECT = 16.0 / 9.0
# The whole character has to be visible, not just its control points: the mesh sticks out a
# little (hands, hair, clothes) beyond the point controllers.
FRAME_MARGIN = 1.12
MIN_DISTANCE = 1.0
# frame_objects must not zoom into a single control point; below this distance (cm) it keeps the
# current zoom and only re-aims.
MINIMUM_FRAME_DISTANCE = 25.0


# ---------------------------------------------------------------------------
# Small vector helpers (pure)
# ---------------------------------------------------------------------------


def vec(value):
    """Validate a 3D point/vector argument and return it as a tuple of floats."""
    try:
        values = [float(item) for item in value]
    except (TypeError, ValueError):
        raise ValueError(f"Expected three numbers, got {value!r}.") from None
    if len(values) != 3:
        raise ValueError(f"Expected three numbers, got {value!r}.")
    if not all(math.isfinite(item) for item in values):
        raise ValueError(f"Coordinates must be finite numbers, got {value!r}.")
    return tuple(values)


def _add(first, second):
    return tuple(a + b for a, b in zip(first, second, strict=True))


def _sub(first, second):
    return tuple(a - b for a, b in zip(first, second, strict=True))


def _scale(vector, factor):
    return tuple(item * factor for item in vector)


def _dot(first, second):
    return sum(a * b for a, b in zip(first, second, strict=True))


def _cross(first, second):
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def length(vector):
    return math.sqrt(_dot(vector, vector))


def unit(vector, fallback=None):
    """The unit vector in the direction of vector, or fallback when it is (nearly) zero."""
    size = length(vector)
    if size <= 1e-9:
        if fallback is None:
            raise ValueError(f"Zero-length vector {vector!r} has no direction.")
        return tuple(fallback)
    return _scale(vector, 1.0 / size)


def _without(vector, axis):
    """The part of vector perpendicular to the unit vector axis."""
    return _sub(vector, _scale(axis, _dot(vector, axis)))


def midpoint(first, second):
    return tuple((a + b) / 2.0 for a, b in zip(first, second, strict=True))


# ---------------------------------------------------------------------------
# Camera basis and framing (pure)
# ---------------------------------------------------------------------------


def look_at_basis(forward, up_hint=WORLD_UP):
    """Return (forward, right, up) unit vectors for a camera looking along forward.

    Cascadeur's spherical camera only stores a position and a target, so its up is the world up
    projected onto the view plane (the same convention as ui.project_points). right is the
    screen-right axis and up is the screen-up axis. A vertical forward (top view) has no unique
    up; the hint keeps the maths finite.
    """
    forward = unit(vec(forward))
    hint = unit(vec(up_hint), fallback=WORLD_UP)
    right = _cross(forward, hint)
    if length(right) <= 1e-6:
        # Looking straight along the hint: pick any axis that is not parallel to forward.
        hint = (1.0, 0.0, 0.0) if abs(forward[0]) < 0.9 else (0.0, 0.0, 1.0)
        right = _cross(forward, hint)
    right = unit(right)
    up = unit(_cross(right, forward))
    return forward, right, up


def character_axes(points):
    """Return the character's right/up/forward axes from its control points.

    points maps an alias (pelvis, chest, left_shoulder, ...) to a world position in cm. The up
    axis comes from the pelvis-to-chest line. The side axis comes from the shoulder line, then
    from hands, knees or feet. "front" is the direction the face looks at, i.e. the horizontal
    direction that is perpendicular to both. Nothing here assumes a world axis; when a character
    is turned 90 degrees these axes turn with it.

    Raises ValueError when the points cannot describe a body.
    """

    def position(alias):
        value = points.get(alias)
        return None if value is None else vec(value)

    pelvis = position("pelvis")
    chest = position("chest")
    if pelvis is not None and chest is not None and length(_sub(chest, pelvis)) >= 1.0:
        up = unit(_sub(chest, pelvis))
        up_source = "pelvis to chest"
    else:
        up = WORLD_UP
        up_source = "world Y (no pelvis/chest pair)"

    right = None
    right_source = None
    for right_alias, left_alias in (
        ("right_shoulder", "left_shoulder"),
        ("right_hand", "left_hand"),
        ("right_knee", "left_knee"),
        ("right_foot", "left_foot"),
        ("right_toe", "left_toe"),
    ):
        first = position(right_alias)
        second = position(left_alias)
        if first is None or second is None:
            continue
        side = _without(_sub(first, second), up)
        if length(side) >= 1.0:
            right = unit(side)
            right_source = f"{right_alias} to {left_alias}"
            break

    forward = None
    forward_source = None
    if right is None:
        # Last resort before the world axes: a standing character's toes point forward.
        for foot_alias, toe_alias in (("right_foot", "right_toe"), ("left_foot", "left_toe")):
            foot = position(foot_alias)
            toe = position(toe_alias)
            if foot is None or toe is None:
                continue
            probe = _without(_sub(toe, foot), up)
            if length(probe) >= 1.0:
                forward = unit(probe)
                forward_source = f"{foot_alias} to {toe_alias}"
                right = unit(_cross(forward, up))
                right_source = f"cross(forward, up) from {forward_source}"
                break
    if right is None:
        right = (1.0, 0.0, 0.0)
        right_source = "world X (no usable left/right landmarks)"
    if forward is None:
        forward = unit(_cross(up, right))
        forward_source = "up x right"

    # Re-orthonormalise: the side line is usually not exactly perpendicular to the up line.
    right = unit(_cross(forward, up))
    forward = unit(_cross(up, right))
    return {
        "right": list(right),
        "up": list(up),
        "forward": list(forward),
        "sources": {"up": up_source, "right": right_source, "forward": forward_source},
    }


def preset_direction(view, axes):
    """The unit vector from the target towards the camera for one preset view.

    axes is the dict returned by character_axes. front puts the camera on the side the character
    faces, so the face is visible; left/right are the character's own left/right sides; top
    looks down from above; three_quarter is in front, to the character's right and slightly
    above.
    """
    name = str(view or "").strip().lower()
    right = unit(vec(axes["right"]))
    up = unit(vec(axes["up"]))
    forward = unit(vec(axes["forward"]))
    if name == "front":
        return forward
    if name == "back":
        return _scale(forward, -1.0)
    if name == "left":
        return _scale(right, -1.0)
    if name == "right":
        return right
    if name == "top":
        return up
    if name == "three_quarter":
        return unit(_add(_add(_scale(forward, 0.9), _scale(right, 0.65)), _scale(up, 0.35)))
    raise ValueError(f"Unknown view {view!r}; use one of: {', '.join(VIEWS)}.")


def fit_distance(
    points,
    target,
    camera_forward,
    camera_right,
    camera_up,
    fov_deg=DEFAULT_FOV_DEG,
    aspect=DEFAULT_ASPECT,
    margin=FRAME_MARGIN,
):
    """Distance from target along -camera_forward so every point fits in the view.

    The camera sits at target - camera_forward * distance. A point fits when its horizontal and
    vertical offsets stay inside the frustum (vertical half-angle fov_deg / 2, horizontal half-
    angle scaled by aspect). The largest requirement wins, then margin adds room for the mesh.
    """
    from math import radians, tan

    target = vec(target)
    forward = unit(vec(camera_forward))
    right = unit(vec(camera_right))
    up = unit(vec(camera_up))
    tan_vertical = tan(radians(float(fov_deg)) / 2.0)
    tan_horizontal = tan_vertical * max(0.01, float(aspect))
    needed = MIN_DISTANCE
    for point in points:
        relative = _sub(vec(point), target)
        depth_offset = _dot(relative, forward)
        horizontal = abs(_dot(relative, right))
        vertical = abs(_dot(relative, up))
        needed = max(
            needed,
            horizontal / tan_horizontal - depth_offset,
            vertical / tan_vertical - depth_offset,
        )
    return needed * max(1.0, float(margin))


def preset_camera(
    view,
    target,
    axes,
    points,
    distance=None,
    fov_deg=DEFAULT_FOV_DEG,
    aspect=DEFAULT_ASPECT,
    margin=FRAME_MARGIN,
):
    """Compute the camera for one preset view.

    Returns a dict with position, target, distance and the camera basis. distance overrides the
    automatic framing of points (cm from target to camera).
    """
    direction = preset_direction(view, axes)
    target = vec(target)
    name = str(view).strip().lower()
    if name == "top":
        # Looking straight down: screen-right is the character's right, so the character's front
        # points up in the picture.
        forward, right, up = look_at_basis(_scale(direction, -1.0), axes["forward"])
    else:
        forward, right, up = look_at_basis(_scale(direction, -1.0), WORLD_UP)
    if distance is None:
        distance = fit_distance(points, target, forward, right, up, fov_deg, aspect, margin)
    else:
        distance = float(distance)
        if not math.isfinite(distance) or distance <= 0:
            raise ValueError(f"distance must be a positive number of cm (got {distance!r}).")
    position = _add(target, _scale(direction, distance))
    return {
        "position": list(position),
        "target": list(target),
        "distance": distance,
        "direction": list(direction),
        "camera_forward": list(forward),
        "camera_right": list(right),
        "camera_up": list(up),
    }


def project(point, camera_position, camera_target, fov_deg=DEFAULT_FOV_DEG, aspect=DEFAULT_ASPECT):
    """Project a world point to normalized device coordinates (-1..1, +y is up).

    Returns {"x", "y", "depth", "on_screen"}; x and y are None when the point is at or behind the
    camera. This mirrors ui.project_points without needing Qt so the framing maths can be tested.
    """
    from math import radians, tan

    forward, right, up = look_at_basis(_sub(vec(camera_target), vec(camera_position)), WORLD_UP)
    relative = _sub(vec(point), vec(camera_position))
    depth = _dot(relative, forward)
    if depth <= 1e-6:
        return {"x": None, "y": None, "depth": depth, "on_screen": False}
    tan_half = tan(radians(float(fov_deg)) / 2.0)
    x = _dot(relative, right) / (depth * tan_half * max(0.01, float(aspect)))
    y = _dot(relative, up) / (depth * tan_half)
    return {"x": x, "y": y, "depth": depth, "on_screen": abs(x) <= 1.0 and abs(y) <= 1.0}


def bbox(points):
    """(minimum corner, maximum corner) of a non-empty list of points."""
    values = [vec(point) for point in points]
    if not values:
        raise ValueError("bbox needs at least one point.")
    return (
        tuple(min(point[index] for point in values) for index in range(3)),
        tuple(max(point[index] for point in values) for index in range(3)),
    )


def bbox_center(points):
    """The centre of the axis-aligned bounding box of the points."""
    low, high = bbox(points)
    return midpoint(low, high)


# ---------------------------------------------------------------------------
# Scene camera (GUI thread; Cascadeur is imported lazily)
# ---------------------------------------------------------------------------


def _round(values, digits=3):
    return [round(float(value), digits) + 0.0 for value in values]


def _struct_point(value):
    """A camera_struct position/target as three floats (Cascadeur may return (3,) or (3, 1))."""
    import numpy as np

    values = np.asarray(value, dtype=float).reshape(-1)
    if values.size < 3:
        raise ValueError(f"Expected three coordinates, got {value!r}.")
    return tuple(float(item) for item in values[:3])


def _type_name(camera_type):
    name = getattr(camera_type, "name", None)
    if isinstance(name, str) and name:
        return name
    text = str(camera_type)
    return text.rsplit(".", 1)[-1]


def _viewports():
    from . import common

    _app, view_scene, _scene = common.scenes()
    viewports = list(view_scene.viewports())
    if not viewports:
        raise ValueError("The active tab has no 3D viewport.")
    for viewport in viewports:
        domain = viewport.domain_viewport()
        try:
            if domain.is_main():
                return view_scene, domain
        except Exception:
            continue
    return view_scene, viewports[0].domain_viewport()


def viewport_fov():
    """The viewport's vertical field of view in degrees (Cascadeur's VIEWPORT/FOV setting).

    Falls back to ui.DEFAULT_VIEWPORT_FOV_DEG (30, verified for 2026.2.3) when the setting cannot
    be read.
    """
    try:
        from . import common

        app, _view_scene, _scene = common.scenes()
        value = float(app.get_setting_manager().get_float_value("VIEWPORT/FOV"))
        if 1.0 <= value <= 179.0:
            return value
    except Exception:
        pass
    from . import ui

    return float(ui.DEFAULT_VIEWPORT_FOV_DEG)


def viewport_aspect():
    """(aspect ratio, source) of the main 3D viewport.

    The ratio comes from the viewport item's current rectangle; when the interface cannot be
    inspected (for example with Cascadeur minimized) it falls back to 16:9 and says so.
    """
    try:
        from . import ui

        _index, window = ui.resolve_window()
        rectangle = ui._viewport_rect(window)
        if rectangle.width() > 0 and rectangle.height() > 0:
            return rectangle.width() / rectangle.height(), "viewport"
    except Exception:
        pass
    return DEFAULT_ASPECT, "16:9 fallback"


def read_camera():
    """The main viewport's camera: position, target, type, distance, direction and FOV."""
    _view_scene, domain = _viewports()
    struct = domain.camera_struct()
    position = _struct_point(struct.position)
    target = _struct_point(struct.target)
    offset = _sub(position, target)
    distance = length(offset)
    fov = viewport_fov()
    return {
        "position": _round(position),
        "target": _round(target),
        "type": _type_name(struct.type),
        "distance": round(distance, 3),
        "direction": _round(unit(offset)) if distance > 1e-6 else None,
        "fov_deg": round(fov, 3),
        # Cascadeur has no Python query for the camera-tracking mode; this is what this server
        # last did with track_object (null when it did not touch tracking).
        "tracking": _TRACKING["name"],
    }


def snapshot():
    """The raw camera values needed to put the camera back later (see restore)."""
    _view_scene, domain = _viewports()
    struct = domain.camera_struct()
    return {
        "position": list(_struct_point(struct.position)),
        "target": list(_struct_point(struct.target)),
        "type": _type_name(struct.type),
    }


def restore(saved):
    """Put the camera struct from snapshot() back and return the camera as read back."""
    return write_camera(position=saved.get("position"), target=saved.get("target"), type_name=saved.get("type"))


def _camera_type(name):
    import csc

    members = getattr(csc.view.CameraType, "__members__", None) or {}
    if name in members:
        return members[name]
    return getattr(csc.view.CameraType, name)


def write_camera(position=None, target=None, type_name=None):
    """Write the given camera fields to the main viewport and return the camera as read back."""
    import numpy as np

    _view_scene, domain = _viewports()
    struct = domain.camera_struct()
    if position is not None:
        struct.position = np.asarray(vec(position), dtype=np.float32)
    if target is not None:
        struct.target = np.asarray(vec(target), dtype=np.float32)
    if type_name is not None:
        struct.type = _camera_type(type_name)
    domain.set_camera_struct(struct)
    return read_camera()


def set_camera(position=None, target=None):
    """Move the viewport camera; returns {"previous", "camera"} as written and read back."""
    if position is None and target is None:
        raise ValueError("Pass position and/or target.")
    if position is not None:
        vec(position)
    if target is not None:
        vec(target)
    previous = read_camera()
    position = previous["position"] if position is None else position
    target = previous["target"] if target is None else target
    if length(_sub(vec(position), vec(target))) < 0.1:
        raise ValueError("position and target are (nearly) the same point; the camera would have no direction.")
    camera = write_camera(position=position, target=target)
    return {"previous": previous, "camera": camera}


# ---------------------------------------------------------------------------
# Characters and object targets (GUI thread)
# ---------------------------------------------------------------------------


def _global_position(object_id, frame=None):
    """World position of an object's Transform at frame (cm), or None without a Transform."""
    import numpy as np

    from . import common

    _app, _view_scene, scene = common.scenes()
    model_viewer = scene.model_viewer()
    behaviour_viewer = model_viewer.behaviour_viewer()
    data_viewer = model_viewer.data_viewer()
    if frame is None:
        frame = scene.get_current_frame()
    behaviour = behaviour_viewer.get_behaviour_by_name(object_id, "Transform")
    if behaviour.is_null():
        return None
    data_id = behaviour_viewer.get_behaviour_data(behaviour, "global_position")
    values = np.asarray(data_viewer.get_data_value(data_id, frame), dtype=float).reshape(-1)
    return [float(value) for value in values[:3]]


def _points_by_alias(entries):
    return {entry["alias"]: entry["position"] for entry in entries if entry.get("alias") and entry.get("position")}


def _character_entries(name):
    from . import rig

    entries = rig.controllers(character=name)
    return [entry for entry in entries if entry.get("position")]


def _character_target(character):
    entries = _character_entries(character["name"])
    if not entries:
        raise ValueError(
            f"Character {character['name']!r} has no control points with a world position; open a rigged scene."
        )
    points = [entry["position"] for entry in entries]
    return {
        "kind": "character",
        "name": character["name"],
        "character": character["name"],
        "root_joint": character.get("root_joint"),
        "point": bbox_center(points),
        "points": points,
        "entries": entries,
    }


def resolve_target(target="character"):
    """Resolve a camera target argument to an aim point and the points to frame.

    target may be the literal "character" (the scene's only character), a character name from
    list_characters (root joint and mesh names work too) or a control point name/alias from
    list_controllers. Returns a dict with kind, name, character, point (aim point, cm), points
    (the points that must stay in frame) and entries (control-point records of the character).
    """
    from . import rig

    characters = rig.characters()
    wanted = "character" if target in (None, "") else str(target)
    if wanted.lower() == "character":
        if not characters:
            raise ValueError(
                "The scene has no character (no RigInfo or AnimationInfo); pass a control point or object name instead."
            )
        if len(characters) > 1:
            raise rig.AmbiguousCharacter(
                f"The scene has {len(characters)} characters; camera_preset's default target needs exactly one.",
                hint="Pass target=<character name> from list_characters.",
                candidates=[
                    {"name": character["name"], "root_joint": character.get("root_joint")} for character in characters
                ],
            )
        return _character_target(characters[0])

    # Control points first: a character's root joint may share its name with an alias ("pelvis"),
    # and an alias such as "head" is much more likely to mean the control point than a character.
    all_entries = [entry for entry in rig.controllers() if entry.get("position")]
    matches = rig.match_controllers(wanted, all_entries)
    if len(matches) > 1:
        raise rig.AmbiguousName(
            f"{wanted!r} matches {len(matches)} control points; pick one by its exact name.",
            hint="Use one of the candidate names, or pass character= to narrow the lookup.",
            candidates=[rig.candidate(item) for item in matches[:20]],
            matched=len(matches),
        )
    if len(matches) == 1:
        entry = matches[0]
        character = entry.get("character")
        siblings = [item for item in all_entries if character and item.get("character") == character] or [entry]
        points = [item["position"] for item in siblings]
        return {
            "kind": "point",
            "name": entry["name"],
            "alias": entry.get("alias"),
            "character": character,
            "point": entry["position"],
            "points": points,
            "entries": siblings,
        }

    try:
        match = rig._match_characters(wanted, characters)[0]
    except rig.UnknownCharacter:
        match = None
    if match is not None:
        return _character_target(match)

    ids = rig.resolve([wanted])
    object_id = ids[0]
    position = _global_position(object_id)
    if position is None:
        raise ValueError(f"Object {wanted!r} has no Transform behaviour, so it has no position to frame.")
    from . import common

    _app, _view_scene, scene = common.scenes()
    name = scene.model_viewer().get_object_name(object_id)
    return {
        "kind": "object",
        "name": name,
        "character": None,
        "point": position,
        "points": [position],
        "entries": [],
    }


def apply_preset(view, target="character", distance=None, aspect=None):
    """Apply a preset view and return the written camera plus what it framed."""
    name = str(view or "").strip().lower()
    if name not in VIEWS:
        raise ValueError(f"Unknown view {view!r}; use one of: {', '.join(VIEWS)}.")
    resolved = resolve_target(target)
    axes = character_axes(_points_by_alias(resolved["entries"]))
    ratio = viewport_aspect()[0] if aspect is None else float(aspect)
    fov = viewport_fov()
    camera = preset_camera(
        name,
        resolved["point"],
        axes,
        resolved["points"],
        distance=distance,
        fov_deg=fov,
        aspect=ratio,
    )
    written = write_camera(position=camera["position"], target=camera["target"], type_name="PERSPECTIVE")
    result = dict(camera)
    result["camera"] = written
    result["preset"] = name
    result["fov_deg"] = round(fov, 3)
    result["aspect"] = round(ratio, 4)
    result["target"] = {
        "kind": resolved["kind"],
        "name": resolved["name"],
        "character": resolved["character"],
        "point": _round(resolved["point"]),
    }
    result["axes"] = axes
    return result


def frame_objects(names):
    """Move the camera (keeping its direction) so the named objects are all in view."""
    from . import rig

    if isinstance(names, str):
        names = [names]
    wanted = [str(name) for name in names or []]
    if not wanted:
        raise ValueError("Pass at least one object name.")
    object_ids = rig.resolve(wanted)
    from . import common

    _app, _view_scene, scene = common.scenes()
    model_viewer = scene.model_viewer()
    positions = []
    described = []
    for object_id in object_ids:
        name = model_viewer.get_object_name(object_id)
        position = _global_position(object_id)
        if position is None:
            raise ValueError(f"Object {name!r} has no Transform behaviour, so it has no position to frame.")
        positions.append(position)
        described.append({"name": name, "position": _round(position)})
    camera = read_camera()
    offset = _sub(vec(camera["position"]), vec(camera["target"]))
    direction = unit(offset) if length(offset) > 1e-6 else (0.0, 0.0, 1.0)
    forward, camera_right, camera_up = look_at_basis(_scale(direction, -1.0), WORLD_UP)
    low, high = bbox(positions)
    extent = length(_sub(high, low))
    aim = bbox_center(positions)
    ratio = viewport_aspect()[0]
    fov = viewport_fov()
    if extent < 5.0:
        # A single object (or a few coincident ones) has no extent to fit; keep the current zoom
        # and only re-aim, instead of diving into the control point.
        distance = max(float(camera["distance"]), MINIMUM_FRAME_DISTANCE)
    else:
        distance = max(
            fit_distance(positions, aim, forward, camera_right, camera_up, fov, ratio), MINIMUM_FRAME_DISTANCE
        )
    written = write_camera(position=_add(aim, _scale(direction, distance)), target=aim)
    projected = {}
    for item in described:
        value = project(item["position"], written["position"], written["target"], fov, ratio)
        projected[item["name"]] = {
            "ndc": [round(value["x"], 4), round(value["y"], 4)] if value["x"] is not None else None,
            "on_screen": value["on_screen"],
        }
    return {
        "objects": described,
        "target": _round(aim),
        "distance": round(distance, 3),
        "fov_deg": round(fov, 3),
        "aspect": round(ratio, 4),
        "camera": written,
        "projected": projected,
    }


# ---------------------------------------------------------------------------
# Camera tracking (GUI thread)
# ---------------------------------------------------------------------------

TRACK_ACTION = "ObjectWatchingTool.Track selection with camera"

# Cascadeur's ObjectWatchingTool is a toggle with no queryable state in the Python API. Remember
# what this server session did, so track_object(None) can turn tracking off again.
_TRACKING = {"name": None, "ids": [], "previous": None}


def _selection_ids(scene):
    try:
        return [item for item in scene.selector().selected().ids]
    except Exception:
        return []


def _select(scene, ids):
    """Replace the scene selection with ids (first one is the pivot)."""
    if not ids:
        return False
    try:
        scene.selector().select(set(ids), ids[0])
    except TypeError:
        scene.selector().select(set(ids))
    return True


def _restore_selection(scene, previous):
    if previous:
        return _select(scene, previous)
    try:
        scene.selector().select(set())
        return True
    except Exception:
        return False


def _track_ids(resolved):
    """The object ids that camera tracking should follow for a resolved target."""
    from . import rig

    if resolved["kind"] == "character":
        entries = resolved["entries"]
        root = next((entry for entry in entries if entry.get("alias") == "pelvis"), None) or entries[0]
        return [root["id"]], root["name"]
    if resolved["kind"] == "point":
        entry = next((entry for entry in resolved["entries"] if entry["name"] == resolved["name"]), None)
        if entry is None:
            raise ValueError(f"Could not find the control point {resolved['name']!r} again.")
        return [entry["id"]], entry["name"]
    return rig.resolve([resolved["name"]]), resolved["name"]


def track_object(name=None):
    """Turn camera tracking on for one object/character, or off with name=None."""
    import csc

    from . import common

    _app, _view_scene, scene = common.scenes()
    action_manager = csc.app.get_application().get_action_manager()
    if name in (None, ""):
        if _TRACKING["name"] is None:
            return {
                "tracking": False,
                "changed": False,
                "note": "Camera tracking was not turned on by this server session (Cascadeur does not expose its state), so nothing was toggled.",
            }
        action_manager.call_action(TRACK_ACTION)
        previous = _TRACKING.get("previous") or []
        _TRACKING.update({"name": None, "ids": [], "previous": None})
        try:
            restored = _restore_selection(scene, previous)
        except Exception:
            restored = False
        return {
            "tracking": False,
            "changed": True,
            "selection_restored": restored,
            "note": "Cascadeur only exposes the tracking action as a toggle; it was on because this session turned it on.",
        }

    resolved = resolve_target(name)
    ids, tracked_name = _track_ids(resolved)
    if _TRACKING["name"] == tracked_name and _TRACKING["ids"] == ids:
        return {"tracking": True, "changed": False, "target": tracked_name, "ids": [str(item) for item in ids]}
    if _TRACKING["name"] is not None:
        # Retarget: switch tracking off, select the new object, switch it on again.
        action_manager.call_action(TRACK_ACTION)
        _TRACKING.update({"name": None, "ids": [], "previous": None})
    previous = _selection_ids(scene)
    if not _select(scene, ids):
        raise ValueError(f"Could not select {tracked_name!r}, so the camera cannot track it.")
    action_manager.call_action(TRACK_ACTION)
    _TRACKING.update({"name": tracked_name, "ids": ids, "previous": previous})
    return {
        "tracking": True,
        "changed": True,
        "target": tracked_name,
        "kind": resolved["kind"],
        "ids": [str(item) for item in ids],
        "selected": [str(item) for item in ids],
        "previous_selection": [str(item) for item in previous],
        "note": "Cascadeur's tracking action is a toggle without a Python state query; track_object(None) turns it off and restores the previous selection.",
    }
