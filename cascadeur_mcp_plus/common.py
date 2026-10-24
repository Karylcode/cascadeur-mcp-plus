"""Shared helpers for Cascadeur MCP Plus tool modules (T01).

Tool modules use these helpers so that arguments, results, undo steps and output files behave
the same way everywhere:

* scenes() returns the active application/view/domain scene.
* ok() and fail() build the result dictionaries from the README conventions.
* edit() runs one scene modification as a single "MCP: ..." undo step.
* jsonable() makes numpy and Cascadeur values JSON-safe before a tool returns them.
* find_objects() looks objects up by exact name and suggests the closest names.
* output_path() validates an output file path and temp_dir() creates scratch folders.
* log_tail() reads Cascadeur's log, where modify-callback tracebacks end up.

Everything except the file, log and path helpers needs Cascadeur's GUI thread; the server already
runs tool fn callables there.
"""

from __future__ import annotations

import difflib
import math
import os
import tempfile
import traceback

import csc
import numpy as np

from . import paths
from .errors import ToolError

# Cascadeur's log location differs per platform (T55); paths.log_path() also honours CASCADEUR_LOG.
LOG_PATH = paths.log_path()
UNDO_PREFIX = "MCP: "


# ---------------------------------------------------------------------------
# Scene and result helpers
# ---------------------------------------------------------------------------


def scenes():
    """Return (application, view_scene, domain_scene) for the scene in the active tab."""
    app = csc.app.get_application()
    view_scene = app.current_scene()
    return app, view_scene, view_scene.domain_scene()


def ok(**data) -> dict:
    """Build the success half of the tool result format: {"ok": true, ...}."""
    return {"ok": True, **data}


def fail(error, hint=None, **data) -> dict:
    """Build the failure half of the tool result format: {"ok": false, "error": ..., "hint": ...}."""
    result = {"ok": False, "error": str(error)}
    if hint:
        result["hint"] = str(hint)
    result.update(data)
    return result


def edit(undo_name: str, fn, *, update: bool = True) -> dict:
    """Run fn as one undoable Cascadeur step named "MCP: <undo_name>".

    fn(model_editor, update_editor, scene_updater) takes the same arguments as a
    scene.modify_update callback (a scene.modify callback when update=False). A tool call that
    changes the scene should go through this function once, so a single Ctrl+Z undoes the whole
    call.

    Cascadeur catches exceptions raised inside the callback and only writes them to its log; in
    that case modify()/modify_update() returns False and the result carries the new log lines as
    "log".
    """
    name = f"{UNDO_PREFIX}{undo_name}"
    since = _log_size()
    try:
        _, _, scene = scenes()
        changed = scene.modify_update(name, fn) if update else scene.modify(name, fn)
    except Exception:
        return fail(
            f"Cascadeur did not start the undo step {name!r}.",
            hint="Check that a scene is open and that the arguments are valid.",
            traceback=traceback.format_exc(),
        )
    if not changed:
        return fail(
            f"Cascadeur aborted the undo step {name!r}.",
            hint="Cascadeur catches exceptions inside the modify callback and only logs them; "
            "the traceback is in 'log' (get_log returns the same lines).",
            # 120 lines cover Cascadeur's "RuntimeError ... At: ... Backtrace" block without
            # dropping the exception line when a long native backtrace follows it.
            log=log_tail(since=since, lines=120) if since is not None else log_tail(lines=120),
        )
    return ok(undo=name)


# ---------------------------------------------------------------------------
# JSON conversion
# ---------------------------------------------------------------------------


def _round_float(value, digits: int = 6) -> float:
    # Adding 0.0 turns -0.0 into 0.0, which reads better in tool output.
    return round(float(value), digits) + 0.0


def _json_float(value: float):
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    return value


def jsonable(value):
    """Return value with every part converted to something json.dumps accepts.

    * numpy arrays and scalars become lists and Python numbers;
    * csc.Guid and csc.model.ObjectId become strings;
    * csc.math.Rotation becomes [x, y, z] Euler angles in degrees;
    * csc.math.Quaternion becomes {"w": ..., "x": ..., "y": ..., "z": ...};
    * orthonormal/scaled transforms become dicts of position, rotation (degrees) and scale;
    * dicts, lists and tuples become dicts and lists; sets become lists sorted by str;
    * NaN and infinities become "NaN", "Infinity", "-Infinity" (JSON has no such numbers);
    * anything else becomes str(value).
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, np.ndarray):
        return [jsonable(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return jsonable(value.item())
    if isinstance(value, float):
        return _json_float(value)
    if isinstance(value, csc.math.Rotation):
        return [_round_float(math.degrees(float(angle))) for angle in value.to_euler_angles_x_y_z()]
    if isinstance(value, csc.math.Quaternion):
        return {
            "w": _round_float(value.w()),
            "x": _round_float(value.x()),
            "y": _round_float(value.y()),
            "z": _round_float(value.z()),
        }
    if isinstance(value, csc.math.OrthogonalTransform):
        return {"position": jsonable(value.position), "rotation": jsonable(value.rotation)}
    if isinstance(value, csc.math.ScaledTransform):
        return {
            "position": jsonable(value.position),
            "rotation": jsonable(value.rotation),
            "scale": jsonable(value.scale),
        }
    if isinstance(value, (csc.Guid, csc.model.ObjectId)):
        return value.to_string()
    if isinstance(value, dict):
        return {str(jsonable(key)): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [jsonable(item) for item in sorted(value, key=str)]
    return str(value)


# ---------------------------------------------------------------------------
# Object lookup
# ---------------------------------------------------------------------------


def find_objects(names) -> list:
    """Return the csc.model.ObjectId list for exact object names, in the given order.

    names is one name or a sequence of names; duplicates are removed. When a name does not
    exist, raises ToolError whose message lists the closest object names, which is the hint an
    agent needs to correct the call. T12 adds character aliases on top of this.
    """
    if isinstance(names, str):
        names = [names]
    wanted = [str(name) for name in names]
    _, _, scene = scenes()
    model_viewer = scene.model_viewer()
    found = []
    missing = []
    for name in wanted:
        ids = list(model_viewer.get_objects(name))
        if ids:
            found.extend(ids)
        else:
            missing.append(name)
    if missing:
        existing = [model_viewer.get_object_name(object_id) for object_id in model_viewer.get_objects()]
        details = []
        for name in missing:
            closest = difflib.get_close_matches(name, existing, n=3, cutoff=0.5)
            suggestion = ", ".join(repr(candidate) for candidate in closest) if closest else "none"
            details.append(f"{name!r} (closest: {suggestion})")
        raise ToolError(
            f"Object not found: {'; '.join(details)}. Pass exact object names; get_scene_info lists the scene's objects."
        )
    unique = []
    seen = set()
    for object_id in found:
        if object_id not in seen:
            seen.add(object_id)
            unique.append(object_id)
    return unique


# ---------------------------------------------------------------------------
# Files, folders and the log
# ---------------------------------------------------------------------------


def output_path(path, *, overwrite=False, extensions=None) -> str:
    """Validate and normalize an output file path.

    The path must be absolute, its parent folder must exist, and its extension must be one of
    extensions (".mp4" or "mp4", a string or a sequence). An existing file is only accepted when
    overwrite is exactly True. Every violation raises ToolError.
    """
    if not path or not isinstance(path, str):
        raise ToolError("Output path must be a non-empty string.")
    if not os.path.isabs(path):
        raise ToolError(f"Output path must be absolute (got {path!r}); join it with a folder first.")
    normalized = os.path.normpath(path)
    parent = os.path.dirname(normalized)
    if not os.path.isdir(parent):
        raise ToolError(f"Output folder does not exist: {parent!r}. Create it first or pass an existing folder.")
    if extensions:
        allowed = [extensions] if isinstance(extensions, str) else list(extensions)
        allowed = [ext if ext.startswith(".") else f".{ext}" for ext in allowed]
        suffix = os.path.splitext(normalized)[1]
        if suffix.lower() not in {ext.lower() for ext in allowed}:
            raise ToolError(f"Output path must end with {' or '.join(allowed)} (got {normalized!r}).")
    if os.path.exists(normalized) and overwrite is not True:
        raise ToolError(f"File already exists: {normalized!r}. Pass overwrite=true to replace it.")
    return normalized


def temp_dir(*parts) -> str:
    """Create and return %TEMP%\\cascadeur-mcp-plus\\<parts...>."""
    path = os.path.join(tempfile.gettempdir(), "cascadeur-mcp-plus", *(str(part) for part in parts))
    os.makedirs(path, exist_ok=True)
    return path


def _log_size():
    try:
        return os.path.getsize(LOG_PATH)
    except OSError:
        return None


def log_tail(since=None, contains=None, lines: int = 40) -> list[str]:
    """Return the last lines of Cascadeur's log as strings.

    since is a byte offset from _log_size() (or None): only text written after it is returned,
    which is how edit() picks up the traceback of a failed callback. contains keeps only lines
    that include that text, ignoring case. A missing log returns an empty list.
    """
    if not os.path.exists(LOG_PATH):
        return []
    try:
        with open(LOG_PATH, "rb") as handle:
            offset = None if since is None else max(0, int(since))
            if offset:
                handle.seek(offset - 1)
                if handle.read(1) not in (b"\n", b"\r"):
                    # The offset is in the middle of a line; drop the partial first line.
                    handle.seek(offset)
                    split = handle.read().split(b"\n", 1)
                    data = split[1] if len(split) > 1 else b""
                else:
                    handle.seek(offset)
                    data = handle.read()
            else:
                handle.seek(0)
                data = handle.read()
    except OSError:
        return []
    tail = data.decode("utf-8", errors="replace").splitlines()
    if contains:
        needle = str(contains).lower()
        tail = [line for line in tail if needle in line.lower()]
    return tail[-max(1, int(lines)) :]
