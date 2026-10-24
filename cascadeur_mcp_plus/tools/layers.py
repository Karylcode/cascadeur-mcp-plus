"""Animation layers: the scene's timeline layer/folder tree (T23).

Cascadeur's timeline is built from layers (one per animated object track) grouped into
folders. This module lists that tree and edits it: create layers and folders, delete them,
hide/lock a layer, rename an item and move control points to another layer.

Layer operations are pure timeline edits, so they run through scene.modify (no data update)
inside common.edit: every tool call becomes one "MCP: ..." undo step, exactly like the other
tools. The tools verify their own postcondition by reading the tree again, because the layer
editor reports no error when an edit is refused.

Name lookup accepts an exact name, a "folder/child" path or the item's id (all
case-insensitive). Several items can share a name in different folders, so an ambiguous name
returns an error with the matching paths as candidates instead of picking one.

ensure_unlocked(object_ids) is the shared guard for tools that change control-point data:
the user's layer lock is not enforced by Cascadeur's Python editor API, so those tools must
call it before writing and return its failure. move_to_layer calls it too.

Cascadeur 2026.2.3 crashes (heap corruption in ntdll.dll) when Viewer.item(),
Folder.children_ordered() and ItemVariant.header() are used together, so the tree is rebuilt
from the flat layers_map()/folders_map() dictionaries and each item's header. That is also how
Cascadeur's own bundled scripts (pycsc, the QRT rigging prototype) read the layer tree.

All layer operations run on Cascadeur's GUI thread. The module imports only the standard
library at import time, so tests/test_layers.py can exercise the pure tree helpers without
Cascadeur; csc and cascadeur_mcp_plus.common are imported lazily.
"""

from __future__ import annotations

import difflib
import traceback

LAYER = "layer"
FOLDER = "folder"
KINDS = (LAYER, FOLDER)
PATH_SEPARATOR = "/"
CANDIDATE_LIMIT = 20


# ---------------------------------------------------------------------------
# Errors and arguments
# ---------------------------------------------------------------------------


class LayerError(Exception):
    """A layer lookup or argument failed in a way the caller can act on."""

    def __init__(self, message, hint=None, candidates=None, **extra):
        super().__init__(message)
        self.hint = hint
        self.candidates = list(candidates or [])
        self.extra = extra

    def result(self):
        """The {"ok": false, ...} tool result for this error."""
        data = {"ok": False, "error": str(self)}
        if self.hint:
            data["hint"] = self.hint
        if self.candidates:
            data["candidates"] = self.candidates
        data.update(self.extra)
        return data


def validate_name(name, what="name"):
    """Return the stripped item name, or raise LayerError when it cannot be used."""
    if not isinstance(name, str):
        raise LayerError(f"{what} must be a string (got {type(name).__name__}).")
    cleaned = name.strip()
    if not cleaned:
        raise LayerError(f"{what} must not be empty.")
    if PATH_SEPARATOR in cleaned:
        raise LayerError(
            f"{what} must not contain {PATH_SEPARATOR!r}: it separates folders in layer paths.",
            hint="Use a single name without slashes.",
        )
    return cleaned


def bool_arg(value, what):
    """Return a real bool, or raise LayerError (JSON strings are not accepted)."""
    if isinstance(value, bool):
        return value
    raise LayerError(f"{what} must be true or false (got {value!r}).")


# ---------------------------------------------------------------------------
# Pure helpers: entries, paths, tree and lookup (no Cascadeur import)
# ---------------------------------------------------------------------------


def make_entry(kind, entry_id, name, parent=None, order=0, objects=0, visible=None, locked=None):
    """Build the plain entry shape used by the tree helpers.

    kind is "layer" or "folder"; entry_id and parent are string ids (csc.Guid text), so the
    helpers can be unit tested without Cascadeur. A folder has visible/locked None: Cascadeur
    only stores those flags on layers.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown item kind: {kind!r}")
    return {
        "kind": kind,
        "id": str(entry_id),
        "name": str(name),
        "parent": None if parent is None else str(parent),
        "order": int(order),
        "objects": int(objects),
        "visible": None if visible is None else bool(visible),
        "locked": None if locked is None else bool(locked),
    }


def _children_map(entries):
    children = {}
    for entry in entries:
        children.setdefault(entry.get("parent"), []).append(entry)
    for items in children.values():
        items.sort(key=lambda entry: (entry.get("order", 0), entry.get("name", "")))
    return children


def entry_paths(entries, root_id=None):
    """Return {entry id: "folder/child/name"} for every entry, stopping at the root id."""
    by_id = {entry["id"]: entry for entry in entries}
    root = None if root_id is None else str(root_id)
    cache = {}

    def path_of(entry_id):
        if entry_id in cache:
            return cache[entry_id]
        names = []
        seen = set()
        current = by_id.get(entry_id)
        while current is not None and current["id"] not in seen:
            seen.add(current["id"])
            names.append(current["name"])
            parent = current.get("parent")
            if parent is None or parent == root:
                current = None
            else:
                current = by_id.get(parent)
        value = PATH_SEPARATOR.join(reversed(names))
        cache[entry_id] = value
        return value

    for entry in entries:
        path_of(entry["id"])
    return cache


def build_tree(entries, root_id=None):
    """Nest entries into the folder tree the viewer shows, ordered like their parents.

    Each node carries kind, name, id, path and objects; layers also carry visible and locked,
    folders carry children and the total object count of their subtree. Entries whose parent
    is unknown are kept at the top level instead of being dropped.
    """
    root = None if root_id is None else str(root_id)
    paths = entry_paths(entries, root)
    children = _children_map(entries)
    emitted = set()

    def node(entry):
        entry_id = entry["id"]
        result = {
            "kind": entry["kind"],
            "name": entry["name"],
            "id": entry_id,
            "path": paths.get(entry_id, entry["name"]),
        }
        if entry["kind"] == LAYER:
            result["visible"] = entry.get("visible")
            result["locked"] = entry.get("locked")
            result["objects"] = int(entry.get("objects", 0))
            return result
        kids = []
        for child in children.get(entry_id, []):
            if child["id"] in emitted:
                continue
            emitted.add(child["id"])
            kids.append(node(child))
        result["children"] = kids
        result["objects"] = sum(kid["objects"] for kid in kids)
        result["visible"] = None
        result["locked"] = None
        return result

    tree = []
    for entry in children.get(root, []):
        if entry["id"] in emitted:
            continue
        emitted.add(entry["id"])
        tree.append(node(entry))
    for entry in entries:
        if entry["id"] in emitted:
            continue
        emitted.add(entry["id"])
        tree.append(node(entry))
    return tree


def candidate(entry, paths):
    """The public candidate shape used in ambiguity errors."""
    return {
        "name": entry["name"],
        "path": paths.get(entry["id"], entry["name"]),
        "id": entry["id"],
        "kind": entry["kind"],
    }


def find_entry(entries, query, kind=None, partial=True):
    """Return the entry a name, path or id refers to (raises LayerError).

    Matching order: id, full path (only when the query contains "/"), exact name, then a
    unique partial name; every comparison is case-insensitive. A plain name is never treated
    as a path, because a top-level item's path equals its name and that must not hide a
    same-named item in a folder. Several matches at the same stage raise LayerError with
    candidates instead of picking one. kind limits the search to "layer" or "folder".

    partial=False accepts only an id, a full path or the exact name. Destructive calls use it
    so "Hand" can never resolve to "LeftHandFingers".
    """
    text = str(query or "").strip()
    if not text:
        raise LayerError("Layer name or path must not be empty.")
    allowed = [entry for entry in entries if kind is None or entry["kind"] == kind]
    if not allowed:
        wanted = "layer or folder" if kind is None else kind
        raise LayerError(
            f"The scene has no {wanted} to match {text!r}.",
            hint="Call list_layers to see the scene's layers and folders.",
        )
    paths = entry_paths(entries)
    folded = text.casefold()

    def unique(matches, label):
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise LayerError(
                f"{text!r} matches {len(matches)} items by {label}; pass the full path or the id.",
                hint="Paths look like 'parent/child'; list_layers shows every item's path and id.",
                candidates=[candidate(entry, paths) for entry in matches[:CANDIDATE_LIMIT]],
                matched=len(matches),
            )
        return None

    found = unique([entry for entry in allowed if entry["id"].casefold() == folded], "id")
    if found is not None:
        return found
    if PATH_SEPARATOR in text:
        # "Rig/hand_l" is unambiguous even when several items are called hand_l.
        found = unique(
            [entry for entry in allowed if paths.get(entry["id"], entry["name"]).casefold() == folded],
            "path",
        )
        if found is not None:
            return found
    found = unique([entry for entry in allowed if entry["name"].casefold() == folded], "name")
    if found is not None:
        return found
    if partial:
        found = unique([entry for entry in allowed if folded in entry["name"].casefold()], "partial name")
        if found is not None:
            return found
    known = [paths.get(entry["id"], entry["name"]) for entry in allowed]
    closest = difflib.get_close_matches(text, known, n=3, cutoff=0.5)
    suggestion = ", ".join(repr(item) for item in closest) if closest else "none"
    raise LayerError(
        f"Layer or folder not found: {text!r} (closest: {suggestion}).",
        hint="Call list_layers to see the scene's layer tree, or pass an id from it.",
        closest=closest,
    )


def name_taken(entries, parent_id, name, exclude_id=None):
    """Return the sibling entry that already uses name (case-insensitive), or None."""
    parent = None if parent_id is None else str(parent_id)
    folded = str(name).casefold()
    for entry in entries:
        if exclude_id is not None and entry["id"] == exclude_id:
            continue
        if entry.get("parent") == parent and entry["name"].casefold() == folded:
            return entry
    return None


def descendant_object_count(entries, entry_id):
    """The number of layer objects in an item's subtree (a layer's own count for a layer)."""
    children = _children_map(entries)
    target = next((entry for entry in entries if entry["id"] == str(entry_id)), None)
    total = int(target.get("objects", 0)) if target is not None and target["kind"] == LAYER else 0
    seen = set()
    stack = [str(entry_id)]
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            if child["id"] in seen:
                continue
            seen.add(child["id"])
            if child["kind"] == LAYER:
                total += int(child.get("objects", 0))
            stack.append(child["id"])
    return total


def locked_groups(entries, object_layers, object_names=None):
    """Group objects by the locked layer they belong to (pure).

    object_layers maps an object key to its layer id (None when it has no layer) and
    object_names maps that key to the name shown in an error. Returns
    [(layer entry, [object names])] in first-seen order; empty when no object is on a locked
    layer. ensure_unlocked() turns this into the tool failure.
    """
    by_id = {entry["id"]: entry for entry in entries}
    names = dict(object_names or {})
    groups = {}
    for key, layer_id in object_layers.items():
        entry = by_id.get(None if layer_id is None else str(layer_id))
        if entry is None or not entry.get("locked"):
            continue
        group = groups.get(entry["id"])
        if group is None:
            group = groups[entry["id"]] = (entry, [])
        group[1].append(str(names.get(key, key)))
    return list(groups.values())


def rescue_layer(entries, doomed_ids, default_id=None):
    """Choose the layer that force-deleted objects move to (None when there is no other layer).

    Cascadeur refuses to delete a layer that still holds objects ("deleting non-empty layer is
    forbidden"), so force=true rescues the objects into the scene's default layer first, or into
    any other layer outside the doomed subtree. doomed_ids is the set of layer/folder ids the
    deletion removes.
    """
    if default_id is not None and default_id not in doomed_ids:
        return default_id
    for entry in entries:
        if entry["kind"] == LAYER and entry["id"] not in doomed_ids:
            return entry["id"]
    return None


def delete_plan(entries, entry):
    """Return [(kind, id), ...] to delete an item bottom-up (layers before their folders)."""
    children = _children_map(entries)
    plan = []

    def visit(folder_id):
        for child in children.get(folder_id, []):
            if child["kind"] == FOLDER:
                visit(child["id"])
            else:
                plan.append((LAYER, child["id"]))
        plan.append((FOLDER, folder_id))

    if entry["kind"] == LAYER:
        return [(LAYER, entry["id"])]
    visit(entry["id"])
    return plan


# ---------------------------------------------------------------------------
# Scene helpers (Cascadeur GUI thread; csc is imported lazily)
# ---------------------------------------------------------------------------


def _common():
    from .. import common

    return common


def _unexpected(exc, action="read the layer tree"):
    return _common().fail(
        f"Could not {action}: {type(exc).__name__}: {exc}",
        hint="Check that a scene with an animation is open, and call get_log for the traceback.",
        traceback=traceback.format_exc(),
    )


def _guid_text(guid):
    try:
        return guid.to_string()
    except Exception:
        return str(guid)


def _is_null(guid):
    try:
        return guid is None or guid.is_null()
    except Exception:
        return guid is None


def _scene():
    _app, _view_scene, scene = _common().scenes()
    return scene


def _item_id(header, fallback_guid, strict=False):
    try:
        header_id = header.id
    except Exception:
        if strict:
            raise
        header_id = None
    if header_id is None or _is_null(header_id):
        header_id = fallback_guid
    return _guid_text(header_id)


def _parent_id(header, root_id, strict=False):
    try:
        parent = header.parent
    except Exception:
        if strict:
            raise
        parent = None
    if parent is None or _is_null(parent):
        return root_id
    return _guid_text(parent)


def read_entries(scene=None, strict=False):
    """Return (entries, guids, root_id, root_name) for the scene's layer tree (GUI thread).

    Reads the flat layers_map()/folders_map() dictionaries and every item's header, the same
    route Cascadeur's own bundled scripts use. It must not call Viewer.item(),
    Folder.children_ordered(), Folder.children_ids() or ItemVariant.header(): on Cascadeur
    2026.2.3 those calls corrupt the heap (0xc0000374 in ntdll.dll) and crash the application,
    as measured on 2026-10-24. Each header's parent field gives the tree; siblings are sorted
    by name because pos_in_parent() adds no information and stays untested.

    strict=True raises instead of leaving unknown values behind: a missing layers_map(), an
    unreadable header or an unreadable is_locked() must not look like "not locked". Callers
    that make safety decisions (ensure_unlocked, delete_layer) use it and fail closed.
    """
    if scene is None:
        scene = _scene()
    viewer = scene.layers_viewer()
    root = viewer.root_id()
    if _is_null(root):
        raise LayerError(
            "The scene has no layer tree root.",
            hint="Open a scene with an animation (for example Cascadeur's Cascy.casc sample).",
        )
    root_id = _guid_text(root)
    try:
        root_name = str(viewer.header(root).name)
    except Exception:
        if strict:
            raise
        root_name = "Layers"
    guids = {root_id: root}
    entries = []

    def add(kind, guid, header, objects=0, visible=None, locked=None):
        item_id = _item_id(header, guid, strict=strict)
        guids[item_id] = guid
        entries.append(
            make_entry(
                kind,
                item_id,
                str(header.name),
                _parent_id(header, root_id, strict=strict),
                0,
                objects,
                visible,
                locked,
            )
        )

    try:
        folders = list(viewer.folders_map().keys())
    except Exception:
        if strict:
            raise
        folders = []
    for guid in folders:
        if _guid_text(guid) == root_id:
            continue
        add(FOLDER, guid, viewer.header(guid))
    try:
        layers = dict(viewer.layers_map())
    except Exception:
        if strict:
            raise
        layers = {}
    for guid, layer in layers.items():
        objects = 0
        visible = None
        locked = None
        try:
            objects = len(layer.obj_ids)
        except Exception:
            if strict:
                raise
        try:
            visible = bool(layer.is_visible)
            locked = bool(layer.is_locked)
        except Exception:
            if strict:
                raise
        add(LAYER, guid, viewer.header(guid), objects, visible, locked)
    return entries, guids, root_id, root_name


def _target(entries, guids, query, kind=None, partial=True):
    entry = find_entry(entries, query, kind=kind, partial=partial)
    return entry, guids[entry["id"]]


def _entry_by_id(entries, entry_id):
    for entry in entries:
        if entry["id"] == entry_id:
            return entry
    return None


def _entry_by_name(entries, name, parent_id):
    for entry in entries:
        if entry.get("parent") == parent_id and entry["name"].casefold() == str(name).casefold():
            return entry
    return None


def _public(entries, entry):
    """An entry plus its path, ready for a tool result (None stays None)."""
    if entry is None:
        return None
    data = dict(entry)
    data["path"] = entry_paths(entries).get(entry["id"], entry["name"])
    return _common().jsonable(data)


def _layer_of(viewer, object_id):
    """The layer id text of an object, or None when it has no layer (GUI thread)."""
    try:
        guid = viewer.layer_id_by_obj_id(object_id)
    except Exception:
        try:
            guid = viewer.layer_id_by_obj_id_or_null(object_id)
        except Exception:
            return None
    return None if _is_null(guid) else _guid_text(guid)


def _layer_of_strict(viewer, object_id):
    """The layer id text of an object (None for a null layer) or an exception (GUI thread).

    ensure_unlocked uses this rather than _layer_of(): a failed lookup must not look like
    "this object has no layer", which would silently pass a locked object.
    """
    try:
        guid = viewer.layer_id_by_obj_id(object_id)
    except Exception:
        guid = viewer.layer_id_by_obj_id_or_null(object_id)
    return None if _is_null(guid) else _guid_text(guid)


def _object_name(model_viewer, object_id):
    try:
        return str(model_viewer.get_object_name(object_id))
    except Exception:
        return str(object_id)


def _object_key(object_id):
    try:
        return object_id.to_string()
    except Exception:
        return str(object_id)


def ensure_unlocked(object_ids):
    """Return a common.fail(...) result when any object is on a locked layer, else None.

    Tools that change control-point data (set_pose in T16, interpolation, mirroring, ...) call
    this first. The user locked the layer on purpose, and Cascadeur 2026.2.3's Python editor
    API does not enforce the lock itself (measured: a global_position write and a
    move_objects_to_layer both succeed on a locked layer), so MCP Plus has to enforce it.

    object_ids is one csc.model.ObjectId or a sequence of them. The failure lists the objects
    and their layers and says how to unlock them; tools return it as their own failure result.
    Objects without a layer cannot be locked and are ignored. The check fails closed: when the
    layer tree, a layer's lock flag or an object's layer cannot be read, it returns a failure
    instead of assuming the object is unlocked.
    """
    common = _common()
    if object_ids is None:
        return None
    if isinstance(object_ids, (str, bytes)) or not hasattr(object_ids, "__iter__"):
        object_ids = [object_ids]
    object_ids = list(object_ids)
    if not object_ids:
        return None
    try:
        scene = _scene()
        viewer = scene.layers_viewer()
        model_viewer = scene.model_viewer()
        entries, _guids, _root_id, _root_name = read_entries(scene, strict=True)
        object_layers = {}
        names = {}
        for object_id in object_ids:
            key = _object_key(object_id)
            object_layers[key] = _layer_of_strict(viewer, object_id)
            names[key] = _object_name(model_viewer, object_id)
        groups = locked_groups(entries, object_layers, names)
    except LayerError as exc:
        return exc.result()
    except Exception as exc:
        return common.fail(
            f"Could not confirm whether the objects are on a locked layer: {type(exc).__name__}: {exc}",
            hint="The layer tree or a layer's lock state could not be read; check get_log, then retry.",
            traceback=traceback.format_exc(),
        )
    if not groups:
        return None
    paths = entry_paths(entries)
    locked_objects = [name for _entry, group_names in groups for name in group_names]
    layers = []
    for entry, group_names in groups:
        item = candidate(entry, paths)
        item["objects"] = group_names
        item["visible"] = entry.get("visible")
        item["locked"] = entry.get("locked")
        layers.append(item)
    layer_text = ", ".join(repr(paths.get(entry["id"], entry["name"])) for entry, _names in groups)
    return common.fail(
        f"Refusing to change objects on locked layer(s): {layer_text} holds {', '.join(locked_objects)}.",
        hint=(
            "Unlock the layer first with set_layer_locked(name=<layer>, locked=false); "
            "the lock is there to keep these objects unchanged."
        ),
        objects=common.jsonable(locked_objects),
        layers=common.jsonable(layers),
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def list_layers() -> dict:
    """List the scene's layer and folder tree with names, ids, flags and object counts."""
    common = _common()
    try:
        entries, _guids, root_id, root_name = read_entries()
        tree = build_tree(entries, root_id)
    except LayerError as exc:
        return exc.result()
    except Exception as exc:
        return _unexpected(exc)
    result = common.ok(
        root={"id": root_id, "name": root_name},
        counts={
            "layers": sum(1 for entry in entries if entry["kind"] == LAYER),
            "folders": sum(1 for entry in entries if entry["kind"] == FOLDER),
        },
        tree=common.jsonable(tree),
    )
    if not entries:
        result["hint"] = "The scene's layer tree is empty; create_layer adds a layer under the root."
    return result


def create_layer(name: str, parent: str | None = None) -> dict:
    """Create an empty layer under a folder (the root by default)."""
    common = _common()
    try:
        layer_name = validate_name(name, "name")
        scene = _scene()
        entries, guids, root_id, _root_name = read_entries(scene)
        parent_entry = None if parent is None else find_entry(entries, parent, kind=FOLDER)
    except LayerError as exc:
        return exc.result()
    except Exception as exc:
        return _unexpected(exc, "read the layer tree")
    parent_id = root_id if parent_entry is None else parent_entry["id"]
    existing = name_taken(entries, parent_id, layer_name)
    if existing is not None:
        return common.fail(
            f"A {existing['kind']} named {existing['name']!r} already exists in this parent.",
            hint="Pass another name, or use the existing item's path with the other tools.",
            existing=common.jsonable(candidate(existing, entry_paths(entries))),
        )
    parent_guid = guids[parent_id]
    created = {}

    def change(model_editor, _update_editor, _scene):
        created["id"] = _guid_text(model_editor.layers_editor().create_layer(layer_name, parent_guid))

    result = common.edit(f"create layer {layer_name}", change, update=False)
    if not result["ok"]:
        return result
    try:
        entries, _guids, _root_id, _root_name = read_entries(scene)
        entry = _entry_by_id(entries, created.get("id")) or _entry_by_name(entries, layer_name, parent_id)
    except Exception:
        entry = None
    if entry is None:
        return common.fail(
            f"Cascadeur did not create layer {layer_name!r}.",
            hint="Check get_log; the layer tree still looks the same.",
        )
    return common.ok(layer=_public(entries, entry), undo=result["undo"])


def create_layer_folder(name: str, parent: str | None = None) -> dict:
    """Create a folder with one empty default layer (Cascadeur rejects an empty folder)."""
    common = _common()
    try:
        folder_name = validate_name(name, "name")
        scene = _scene()
        entries, guids, root_id, _root_name = read_entries(scene)
        parent_entry = None if parent is None else find_entry(entries, parent, kind=FOLDER)
    except LayerError as exc:
        return exc.result()
    except Exception as exc:
        return _unexpected(exc, "read the layer tree")
    parent_id = root_id if parent_entry is None else parent_entry["id"]
    existing = name_taken(entries, parent_id, folder_name)
    if existing is not None:
        return common.fail(
            f"A {existing['kind']} named {existing['name']!r} already exists in this parent.",
            hint="Pass another name, or use the existing item's path with the other tools.",
            existing=common.jsonable(candidate(existing, entry_paths(entries))),
        )
    parent_guid = guids[parent_id]
    created = {}

    def change(model_editor, _update_editor, _scene):
        layers_editor = model_editor.layers_editor()
        # Cascadeur 2026.2.3 raises "Invariant check failed: empty dir" when a new folder is
        # left without layers, so ask for its default layer (named Track0 by Cascadeur).
        created["id"] = _guid_text(layers_editor.create_folder(folder_name, parent_guid, True))

    result = common.edit(f"create layer folder {folder_name}", change, update=False)
    if not result["ok"]:
        return result
    try:
        entries, _guids, _root_id, _root_name = read_entries(scene)
        entry = _entry_by_id(entries, created.get("id")) or _entry_by_name(entries, folder_name, parent_id)
        default_entry = None
        if entry is not None:
            default_entry = next((item for item in entries if item.get("parent") == entry["id"]), None)
    except Exception:
        entry = None
        default_entry = None
    if entry is None:
        return common.fail(
            f"Cascadeur did not create folder {folder_name!r}.",
            hint="Check get_log; the layer tree still looks the same.",
        )
    return common.ok(folder=_public(entries, entry), default_layer=_public(entries, default_entry), undo=result["undo"])


def delete_layer(name: str, force=False) -> dict:
    """Delete a layer, or a folder with everything in it (folders and non-empty layers need force)."""
    common = _common()
    try:
        must_force = bool_arg(force, "force")
        scene = _scene()
        entries, guids, _root_id, _root_name = read_entries(scene, strict=True)
        # Destructive: a partial name ("Hand") must never resolve to "LeftHandFingers".
        entry, _guid = _target(entries, guids, name, partial=False)
    except LayerError as exc:
        return exc.result()
    except Exception as exc:
        return common.fail(
            f"Could not read the layer tree, so nothing was deleted: {type(exc).__name__}: {exc}",
            hint="The layer tree or a layer's lock state could not be read; check get_log, then retry.",
            traceback=traceback.format_exc(),
        )
    plan = delete_plan(entries, entry)
    label = "layer folder" if entry["kind"] == FOLDER else "layer"
    paths = entry_paths(entries)
    path = paths.get(entry["id"], entry["name"])
    doomed = {entry_id: guids[entry_id] for _kind, entry_id in plan}
    doomed_ids = set(doomed)
    doomed_layers = {entry_id for kind, entry_id in plan if kind == LAYER}
    locked_layers = []
    for entry_id in doomed_layers:
        item = _entry_by_id(entries, entry_id)
        if item is not None and item.get("locked"):
            locked_layers.append(item)
    if locked_layers:
        return common.fail(
            f"Cannot delete {label} {path!r}: {len(locked_layers)} layer(s) in it are locked.",
            hint=(
                "Unlock them first with set_layer_locked(name=<layer>, locked=false); "
                "a locked layer protects its objects from changes."
            ),
            layers=common.jsonable([candidate(item, paths) for item in locked_layers]),
        )
    objects = descendant_object_count(entries, entry["id"])
    if not must_force:
        if entry["kind"] == LAYER and objects:
            return common.fail(
                f"Layer {entry['name']!r} holds {objects} object(s); refusing to delete it without force=true.",
                hint="Move the objects to another layer with move_to_layer, or pass force=true to delete the layer and its objects' layer.",
                layer=common.jsonable(candidate(entry, paths)),
            )
        if entry["kind"] == FOLDER and any(child["parent"] == entry["id"] for child in entries):
            return common.fail(
                f"Folder {entry['name']!r} is not empty; refusing to delete it without force=true.",
                hint="Delete its children first, or pass force=true to delete the folder and everything under it.",
                folder=common.jsonable(candidate(entry, paths)),
            )
    viewer = scene.layers_viewer()
    try:
        layers_now = dict(viewer.layers_map())
    except Exception:
        return common.fail(
            f"Could not read the objects of {label} {path!r}, so it was not deleted.",
            hint="The layer tree could not be read; check get_log and try again.",
            traceback=traceback.format_exc(),
        )
    rescued = []
    for entry_id in doomed_layers:
        layer = layers_now.get(guids[entry_id])
        if layer is None:
            return common.fail(
                f"Could not read the objects of a layer in {path!r}, so it was not deleted.",
                hint="The layer tree changed while deleting; call list_layers and retry.",
            )
        try:
            rescued.extend(list(layer.obj_ids))
        except Exception:
            return common.fail(
                f"Could not read the objects of a layer in {path!r}, so it was not deleted.",
                hint="Check get_log and try again.",
                traceback=traceback.format_exc(),
            )
    try:
        default_guid = viewer.default_layer_id()
    except Exception:
        default_guid = None
    default_id = None if _is_null(default_guid) else _guid_text(default_guid)
    if default_id is not None and default_id not in guids:
        default_id = None
    rescue_id = rescue_layer(entries, doomed_ids, default_id) if rescued else None
    if rescued and rescue_id is None:
        return common.fail(
            f"{label.capitalize()} {path!r} holds {len(rescued)} object(s) and the scene has no other layer to move them to.",
            hint="Create a layer with create_layer first, then delete this one with force=true.",
            layer=common.jsonable(candidate(entry, paths)),
        )
    rescue_entry = _entry_by_id(entries, rescue_id) if rescue_id is not None else None
    if rescued and rescue_entry is not None and rescue_entry.get("locked"):
        return common.fail(
            f"Cannot delete {label} {path!r}: its {len(rescued)} object(s) would move to locked layer "
            f"{paths.get(rescue_entry['id'], rescue_entry['name'])!r}.",
            hint=(
                "Unlock the destination layer first with set_layer_locked(name=<layer>, locked=false), "
                "or move the objects somewhere else with move_to_layer."
            ),
            layer=common.jsonable(candidate(rescue_entry, paths)),
        )

    def change(model_editor, _update_editor, _scene):
        layers_editor = model_editor.layers_editor()
        if rescued and rescue_id is not None:
            # Cascadeur forbids deleting a non-empty layer, so rescue its objects first.
            model_editor.move_objects_to_layer(list(rescued), guids[rescue_id])
        for kind, entry_id in plan:
            if kind == FOLDER:
                layers_editor.delete_folder(doomed[entry_id])
            else:
                layers_editor.delete_layer(doomed[entry_id])

    result = common.edit(f"delete {label} {path}", change, update=False)
    if not result["ok"]:
        return result
    try:
        after, _guids, _root_id, _root_name = read_entries(scene)
    except Exception as exc:
        return _unexpected(exc, "confirm the deletion")
    remaining = [item for item in after if item["id"] in doomed]
    if remaining:
        return common.fail(
            f"Cascadeur did not delete {label} {path!r}.",
            hint="The layer may be locked or still referenced; unlock it and check get_log.",
            remaining=common.jsonable([candidate(item, entry_paths(after)) for item in remaining]),
        )
    payload = common.ok(
        deleted=common.jsonable(candidate(entry, entry_paths(entries))), objects=objects, undo=result["undo"]
    )
    if rescue_entry is not None:
        payload["objects_moved_to"] = _public(entries, rescue_entry)
    return payload


def set_layer_visible(name: str, visible) -> dict:
    """Show or hide a layer."""
    common = _common()
    try:
        wanted = bool_arg(visible, "visible")
        scene = _scene()
        entries, guids, _root_id, _root_name = read_entries(scene)
        entry, guid = _target(entries, guids, name, kind=LAYER)
    except LayerError as exc:
        return exc.result()
    except Exception as exc:
        return _unexpected(exc, "read the layer tree")
    layer = entry_paths(entries).get(entry["id"], entry["name"])
    if entry.get("visible") == wanted:
        return common.ok(layer=_public(entries, entry), visible=wanted, changed=False)

    def change(model_editor, _update_editor, _scene):
        model_editor.layers_editor().set_visible_for_layer(wanted, guid)

    result = common.edit(f"{'show' if wanted else 'hide'} layer {layer}", change, update=False)
    if not result["ok"]:
        return result
    try:
        after, _guids, _root_id, _root_name = read_entries(scene)
        entry_after = _entry_by_id(after, entry["id"])
    except Exception as exc:
        return _unexpected(exc, "confirm the visibility change")
    if entry_after is None or entry_after.get("visible") != wanted:
        return common.fail(
            f"Cascadeur did not change the visibility of layer {layer!r}.",
            hint="Check get_log; unlock the layer if it is locked and try again.",
        )
    return common.ok(layer=_public(after, entry_after), visible=wanted, undo=result["undo"])


def set_layer_locked(name: str, locked) -> dict:
    """Lock or unlock a layer."""
    common = _common()
    try:
        wanted = bool_arg(locked, "locked")
        scene = _scene()
        entries, guids, _root_id, _root_name = read_entries(scene)
        entry, guid = _target(entries, guids, name, kind=LAYER)
    except LayerError as exc:
        return exc.result()
    except Exception as exc:
        return _unexpected(exc, "read the layer tree")
    layer = entry_paths(entries).get(entry["id"], entry["name"])
    if entry.get("locked") == wanted:
        return common.ok(layer=_public(entries, entry), locked=wanted, changed=False)

    def change(model_editor, _update_editor, _scene):
        model_editor.layers_editor().set_locked_for_layer(wanted, guid)

    result = common.edit(f"{'lock' if wanted else 'unlock'} layer {layer}", change, update=False)
    if not result["ok"]:
        return result
    try:
        after, _guids, _root_id, _root_name = read_entries(scene)
        entry_after = _entry_by_id(after, entry["id"])
    except Exception as exc:
        return _unexpected(exc, "confirm the lock change")
    if entry_after is None or entry_after.get("locked") != wanted:
        return common.fail(
            f"Cascadeur did not change the lock state of layer {layer!r}.",
            hint="Check get_log and try again.",
        )
    return common.ok(layer=_public(after, entry_after), locked=wanted, undo=result["undo"])


def rename_layer(name: str, new_name: str) -> dict:
    """Rename a layer or folder."""
    common = _common()
    try:
        wanted = validate_name(new_name, "new_name")
        scene = _scene()
        entries, guids, _root_id, _root_name = read_entries(scene)
        entry, guid = _target(entries, guids, name)
    except LayerError as exc:
        return exc.result()
    except Exception as exc:
        return _unexpected(exc, "read the layer tree")
    label = "layer folder" if entry["kind"] == FOLDER else "layer"
    old_path = entry_paths(entries).get(entry["id"], entry["name"])
    if entry["name"] == wanted:
        return common.ok(item=_public(entries, entry), name=wanted, changed=False)
    existing = name_taken(entries, entry.get("parent"), wanted, exclude_id=entry["id"])
    if existing is not None:
        return common.fail(
            f"A {existing['kind']} named {existing['name']!r} already exists in this parent.",
            hint="Pick a name that is unique among its siblings.",
            existing=common.jsonable(candidate(existing, entry_paths(entries))),
        )

    def change(model_editor, _update_editor, _scene):
        model_editor.layers_editor().set_name(wanted, guid)

    result = common.edit(f"rename {label} {old_path} to {wanted}", change, update=False)
    if not result["ok"]:
        return result
    try:
        after, _guids, _root_id, _root_name = read_entries(scene)
        entry_after = _entry_by_id(after, entry["id"])
    except Exception as exc:
        return _unexpected(exc, "confirm the rename")
    if entry_after is None or entry_after["name"] != wanted:
        return common.fail(
            f"Cascadeur did not rename {label} {old_path!r}.",
            hint="Check get_log and try again.",
        )
    return common.ok(item=_public(after, entry_after), undo=result["undo"])


def move_to_layer(controllers, layer: str) -> dict:
    """Move control points (names or aliases) to a layer."""
    common = _common()
    from .. import rig

    if not isinstance(controllers, (str, list, tuple)) or not controllers:
        return common.fail(
            "controllers must be a control point name, alias or a non-empty list of them.",
            hint="Pass names or aliases such as 'left_hand' or ['left_hand', 'right_hand'].",
        )
    if isinstance(controllers, (list, tuple)) and not all(isinstance(item, str) for item in controllers):
        return common.fail(
            "controllers must contain only strings.",
            hint="Pass names or aliases such as ['left_hand', 'right_hand'].",
        )
    try:
        scene = _scene()
        entries, guids, _root_id, _root_name = read_entries(scene)
        entry, guid = _target(entries, guids, layer, kind=LAYER)
    except LayerError as exc:
        return exc.result()
    except Exception as exc:
        return _unexpected(exc, "read the layer tree")
    try:
        object_ids = rig.resolve(controllers)
    except rig.RigError as exc:
        return exc.result()
    except Exception as exc:
        return _unexpected(exc, "resolve the control points")
    blocked = ensure_unlocked(object_ids)
    if blocked is not None:
        return blocked
    if entry.get("locked"):
        return common.fail(
            f"Layer {entry_paths(entries).get(entry['id'], entry['name'])!r} is locked.",
            hint="Unlock it with set_layer_locked(name=<layer>, locked=false) before moving objects into it.",
            layer=_public(entries, entry),
        )

    viewer = scene.layers_viewer()
    model_viewer = scene.model_viewer()
    already = [object_id for object_id in object_ids if _layer_of(viewer, object_id) == entry["id"]]
    moving = [object_id for object_id in object_ids if object_id not in already]
    names = [_object_name(model_viewer, object_id) for object_id in object_ids]
    if not moving:
        return common.ok(
            layer=_public(entries, entry),
            moved=[],
            already_in_layer=names,
            changed=False,
        )

    def change(model_editor, _update_editor, _scene):
        model_editor.move_objects_to_layer(list(moving), guid)

    result = common.edit(
        f"move objects to layer {entry_paths(entries).get(entry['id'], entry['name'])}", change, update=False
    )
    if not result["ok"]:
        return result
    try:
        viewer = scene.layers_viewer()
        missing = [object_id for object_id in moving if _layer_of(viewer, object_id) != entry["id"]]
    except Exception as exc:
        return _unexpected(exc, "confirm the move")
    if missing:
        return common.fail(
            f"Cascadeur did not move {len(missing)} of {len(moving)} object(s) to layer {entry['name']!r}.",
            hint="The source layer may be locked; unlock it and try again, or check get_log.",
            not_moved=common.jsonable([_object_name(model_viewer, object_id) for object_id in missing]),
        )
    moved_names = [_object_name(model_viewer, object_id) for object_id in moving]
    try:
        after, _guids, _root_id, _root_name = read_entries(scene)
        entry_after = _entry_by_id(after, entry["id"]) or entry
    except Exception:
        entry_after = entry
    return common.ok(
        layer=_public(after, entry_after),
        moved=moved_names,
        already_in_layer=[_object_name(model_viewer, object_id) for object_id in already],
        undo=result["undo"],
    )


TOOLS = [
    {
        "name": "list_layers",
        "description": "List the scene's animation layer tree: every folder and layer with its name, id, path, "
        "whether it is visible and locked, and how many objects it holds. Folders also list their children "
        "and the total object count of their subtree. Use it before moving control points to a layer or "
        "changing a layer, because layer names are not unique (pass the returned path or id when two items "
        "share a name). Read-only.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "fn": list_layers,
    },
    {
        "name": "create_layer",
        "description": "Create an empty animation layer under a folder and return it with its id and path. "
        "Use it to build a separate track before calling move_to_layer, so a group of control points can be "
        "hidden, locked or edited together. parent is a folder name, folder path or id from list_layers; omit "
        "it for the root folder. Names must be unique among their siblings; the call is one undo step.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the new layer (no '/')."},
                "parent": {
                    "type": "string",
                    "description": "Folder name, path ('parent/child') or id to put the layer in; omit for the root.",
                },
            },
            "required": ["name"],
        },
        "fn": create_layer,
    },
    {
        "name": "create_layer_folder",
        "description": "Create a folder in the animation layer tree and return it with its id, path and the "
        "default layer Cascadeur puts inside it (Cascadeur 2026.2.3 rejects a folder with no layers, so the "
        "folder opens with one empty Track0 layer). Use it to group related layers; parent is a folder name, "
        "path or id from list_layers, omitted for the root. Names must be unique among their siblings; the "
        "call is one undo step.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the new folder (no '/')."},
                "parent": {
                    "type": "string",
                    "description": "Folder name, path ('parent/child') or id to put the folder in; omit for the root.",
                },
            },
            "required": ["name"],
        },
        "fn": create_layer_folder,
    },
    {
        "name": "delete_layer",
        "description": "Delete a layer, or a folder with everything under it, and return what was removed plus "
        "how many objects its layers held. A layer that still holds objects and a folder that is not empty are "
        "refused unless force=true. Cascadeur itself refuses to delete a non-empty layer, so force moves the "
        "objects to the scene's default layer (or the first other layer) before deleting; objects_moved_to names "
        "it. Forcing a folder deletes its layers first and then the sub-folders. The whole call is one undo step.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Layer or folder name, path ('parent/child') or id."},
                "force": {
                    "type": "boolean",
                    "description": "Delete a layer that holds objects, or a folder with children (default false).",
                },
            },
            "required": ["name"],
        },
        "fn": delete_layer,
    },
    {
        "name": "set_layer_visible",
        "description": "Show or hide an animation layer. Hiding a layer hides its objects' animation in the "
        "viewport; the stored animation is untouched. visible must be true or false. Returns the layer with its "
        "new state, and changed=false when it already had that state. One undo step.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Layer name, path ('parent/child') or id."},
                "visible": {"type": "boolean", "description": "true to show the layer, false to hide it."},
            },
            "required": ["name", "visible"],
        },
        "fn": set_layer_visible,
    },
    {
        "name": "set_layer_locked",
        "description": "Lock or unlock an animation layer. A locked layer is protected from MCP write tools: they "
        "refuse to edit objects on it and return an error naming the layer (Cascadeur's Python API itself does not "
        "enforce the lock), so unlock with set_layer_locked(locked=false) before posing or keying. locked must be "
        "true or false. Returns the layer with its new state, and changed=false when it already had that state. "
        "One undo step.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Layer name, path ('parent/child') or id."},
                "locked": {"type": "boolean", "description": "true to lock the layer, false to unlock it."},
            },
            "required": ["name", "locked"],
        },
        "fn": set_layer_locked,
    },
    {
        "name": "rename_layer",
        "description": "Rename an animation layer or folder and return it with its new name and path. The new "
        "name must be unique among its siblings; use the old name, path or id to identify the item. One undo step.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Current layer or folder name, path or id."},
                "new_name": {"type": "string", "description": "New name (no '/')."},
            },
            "required": ["name", "new_name"],
        },
        "fn": rename_layer,
    },
    {
        "name": "move_to_layer",
        "description": "Move control points to an animation layer and return the layer, the objects that moved "
        "and any that were already there. controllers accepts a control point name, an alias such as left_hand "
        "(see list_controllers) or a list of them; layer is a layer name, path or id from list_layers (a folder "
        "is not a layer). Use it to group a character's controls so the layer can be hidden or locked as a unit. "
        "One undo step.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "controllers": {
                    "type": ["string", "array"],
                    "items": {"type": "string"},
                    "description": "Control point name, alias, partial name, or a list of them.",
                },
                "layer": {"type": "string", "description": "Target layer name, path ('parent/child') or id."},
            },
            "required": ["controllers", "layer"],
        },
        "fn": move_to_layer,
    },
]
