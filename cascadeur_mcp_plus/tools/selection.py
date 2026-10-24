"""Selection tools: choose objects by name or alias (T13).

select_objects resolves names through rig.resolve (T12), so an alias such as ``left_hand``, an
exact name such as ``hand_MainPoint_l``, or a partial name all work the same way as in
list_controllers. get_selection reports what is selected now, including tool objects that have no
model name (AutoPosing points); clear_selection empties the selection.

Selection is Cascadeur's domain state, not animation data, so these tools deliberately create no
"MCP: " undo step: Ctrl+Z keeps undoing pose edits, not selections. Later tickets run their
actions (AutoPosing lock, interpolation, motion cleanup) against whatever these tools select.
"""

from __future__ import annotations

import traceback

import csc

from .. import common, rig
from ._schema import schema

MODES = ("replace", "add", "remove")


def _unexpected(exc):
    return common.fail(
        f"Could not read or change the selection: {type(exc).__name__}: {exc}",
        hint="Check that a scene is open in Cascadeur and that a modal dialog is not blocking it; get_log has the traceback.",
        traceback=traceback.format_exc(),
    )


# ---------------------------------------------------------------------------
# Arguments and selection arithmetic (pure Python, unit tested outside Cascadeur)
# ---------------------------------------------------------------------------


def _names_argument(names):
    """Normalize the names argument to a deduplicated list of non-empty strings."""
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, (list, tuple)):
        raise ValueError("names must be a list of object names, aliases or partial names.")
    cleaned = []
    seen = set()
    for raw in names:
        text = str(raw).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    if not cleaned:
        raise ValueError("names must contain at least one object name, alias or partial name.")
    return cleaned


def _mode_argument(mode):
    """Validate and normalize the mode argument."""
    text = str(mode if mode is not None else "replace").strip().lower()
    if text not in MODES:
        raise ValueError(f"mode must be one of: {', '.join(MODES)} (got {mode!r}).")
    return text


def _apply_mode(current, ids, mode):
    """The selection after applying mode to the current selection, preserving order."""
    if mode == "replace":
        return list(ids)
    result = list(current)
    if mode == "add":
        present = set(result)
        for object_id in ids:
            if object_id not in present:
                result.append(object_id)
                present.add(object_id)
        return result
    removed = set(ids)
    return [object_id for object_id in result if object_id not in removed]


# ---------------------------------------------------------------------------
# Scene helpers (Cascadeur GUI thread)
# ---------------------------------------------------------------------------


def _selected_ids(selector):
    """The current selection in interface order (ordered_ids when the version has it)."""
    selection = selector.selected()
    try:
        ordered = list(selection.ordered_ids)
    except Exception:
        ordered = []
    return ordered or list(selection.ids)


def _select_ids(selector, ids):
    """Select ids as one new selection and return the ids that were asked for."""
    pivot = ids[0] if ids else csc.model.ObjectId.null()
    selector.select(set(ids), pivot)
    return ids


def _controllers_by_id():
    """Map ObjectId -> rig.controllers() entry, for aliases and kinds."""
    return {entry["id"]: entry for entry in rig.controllers()}


def _describe(model_viewer, object_id, controllers):
    """One entry of get_selection: name, alias, kind, type, character and id."""
    info = controllers.get(object_id)
    if info is not None:
        return {
            "name": info.get("name"),
            "alias": info.get("alias"),
            "kind": info.get("kind"),
            "type": info.get("type"),
            "character": info.get("character"),
            "id": str(object_id),
        }
    try:
        name = model_viewer.get_object_name(object_id)
        type_name = model_viewer.get_object_type_name(object_id)
    except TypeError:
        # Tool objects such as AutoPosing points have no model name. Keep them visible with the
        # same "<Type id>" shape scene_tools._names uses instead of dropping them.
        return {
            "name": f"<{type(object_id).__name__} {object_id}>",
            "alias": None,
            "kind": None,
            "type": type(object_id).__name__,
            "character": None,
            "id": str(object_id),
            "tool_object": True,
        }
    return {
        "name": name,
        "alias": None,
        "kind": None,
        "type": type_name,
        "character": None,
        "id": str(object_id),
    }


def _selection_entries(scene):
    model_viewer = scene.model_viewer()
    controllers = _controllers_by_id()
    return [_describe(model_viewer, object_id, controllers) for object_id in _selected_ids(scene.selector())]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def select_objects(names, mode="replace") -> dict:
    """Select objects by name/alias with replace, add or remove semantics (GUI thread)."""
    try:
        wanted = _names_argument(names)
        mode = _mode_argument(mode)
    except ValueError as exc:
        return common.fail(
            str(exc),
            hint=f'Pass names as a list such as ["left_hand", "right_hand"] and mode as one of: {", ".join(MODES)}.',
        )
    try:
        ids = rig.resolve(wanted)
    except rig.RigError as exc:
        return exc.result()
    except Exception as exc:
        return _unexpected(exc)

    try:
        _app, _view_scene, scene = common.scenes()
        selector = scene.selector()
        target = _apply_mode(_selected_ids(selector), ids, mode)
        _select_ids(selector, target)
        entries = _selection_entries(scene)
    except Exception as exc:
        return _unexpected(exc)

    result = common.ok(
        mode=mode,
        requested=wanted,
        requested_count=len(ids),
        count=len(entries),
        names=[entry["name"] for entry in entries],
        selection=common.jsonable(entries),
    )
    if len(entries) != len(target):
        result["hint"] = (
            "Cascadeur's selection does not match the requested set; some objects may not be selectable "
            "in the current mode. get_scene_info lists the scene's objects."
        )
    return result


def get_selection() -> dict:
    """List the selected objects with alias, kind, type and character (GUI thread)."""
    try:
        _app, _view_scene, scene = common.scenes()
        entries = _selection_entries(scene)
    except Exception as exc:
        return _unexpected(exc)
    return common.ok(
        count=len(entries),
        names=[entry["name"] for entry in entries],
        selection=common.jsonable(entries),
    )


def clear_selection() -> dict:
    """Deselect every object in the active scene (GUI thread)."""
    try:
        _app, _view_scene, scene = common.scenes()
        _select_ids(scene.selector(), [])
        entries = _selection_entries(scene)
    except Exception as exc:
        return _unexpected(exc)
    result = common.ok(
        count=len(entries),
        names=[entry["name"] for entry in entries],
        selection=common.jsonable(entries),
    )
    if entries:
        result["hint"] = "Cascadeur kept part of the selection; check that no modal dialog is blocking it."
    return result


TOOLS = [
    {
        "name": "select_objects",
        "description": "Select scene objects (control points, joints, boxes, meshes, ...) by name or alias, as clicking them "
        "in the viewport does. Pass aliases such as left_hand or exact names such as hand_MainPoint_l; rig.resolve matches "
        "both and reports candidates when a name is ambiguous. mode replace (default) replaces the selection, add keeps it "
        "and adds the objects, remove deselects the named objects. Use it for tools that act on the object selection, such "
        "as AutoPosing locking or camera framing; timeline actions get their range from select_frames, and most tools "
        "select the controllers they need from their own arguments. Returns the names and details of what is actually "
        "selected afterwards. Selection changes do not create undo steps.",
        "inputSchema": schema(
            {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": 'Object names, aliases or partial names, for example ["left_hand", "right_hand"].',
                },
                "mode": {
                    "type": "string",
                    "enum": list(MODES),
                    "description": "replace (default) replaces the selection, add adds to it, remove deselects the named objects.",
                },
            },
            ["names"],
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
            "csc.model.BehaviourViewer.get_behaviours",
            "csc.model.ModelViewer.get_objects",
            "csc.model.ObjectId",
            "csc.model.ObjectId.null",
        ],
        "fn": select_objects,
    },
    {
        "name": "get_selection",
        "description": "List the current selection with each object's name, alias, kind (main_point, direction, box, joint, "
        "other), Cascadeur type, character and id. Use it to check what an action will act on, or to see the result of "
        "select_objects; tool objects such as AutoPosing points are listed too (they have no model name and are marked "
        "tool_object). Read-only.",
        "inputSchema": schema(),
        "requires": [
            "csc.Guid",
            "csc.app.get_application",
            "csc.domain.Scene.get_current_frame",
            "csc.domain.Scene.model_viewer",
            "csc.math.OrthogonalTransform",
            "csc.math.Quaternion",
            "csc.math.Rotation",
            "csc.math.ScaledTransform",
            "csc.model.BehaviourViewer.get_behaviours",
            "csc.model.ModelViewer.get_objects",
            "csc.model.ObjectId",
        ],
        "fn": get_selection,
    },
    {
        "name": "clear_selection",
        "description": "Deselect every object in the active scene, as clicking empty space in the viewport does. Use it before "
        "building a new selection, or to stop an action from acting on an old one. Returns the remaining selection (0 on "
        "success). Selection changes do not create undo steps.",
        "inputSchema": schema(),
        "requires": [
            "csc.Guid",
            "csc.app.get_application",
            "csc.domain.Scene.get_current_frame",
            "csc.domain.Scene.model_viewer",
            "csc.math.OrthogonalTransform",
            "csc.math.Quaternion",
            "csc.math.Rotation",
            "csc.math.ScaledTransform",
            "csc.model.BehaviourViewer.get_behaviours",
            "csc.model.ModelViewer.get_objects",
            "csc.model.ObjectId",
            "csc.model.ObjectId.null",
        ],
        "fn": clear_selection,
    },
]
