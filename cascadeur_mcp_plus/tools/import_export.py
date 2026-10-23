"""Import and export models and animation: FBX, GLB and BVH (USD has no Python API).

FBX goes through csc.fbx.FbxLoader, GLB through csc.glb.process_import/process_export, and
BVH reuses Cascadeur's own "Import BVH animation" command by swapping the file dialog it
opens. Cascadeur 2026.2.3 ships USD support as native code (usd_ms.dll plus the usd/ plugins)
only: there is no csc.usd module in the type stubs and no Python entry point, so .usd* paths
get an explicit error instead of a silent no-op.

Cascadeur's importers are native tools that record their own undo command, so these tools do not
wrap a whole import in common.edit: nesting a modify call around a loader that starts its own
command is not supported by Cascadeur's API. import_model therefore leaves one native undo step
for the loader plus one for Rig mode; its result says how many (undo_steps). The Rig-mode step
calls scene_tools.enter_rig_mode, the same code path as the enter_rig_mode tool, because a raw
Python import skips the RigInfo creation that the UI import performs and Quick Rigging crashes
without it.

Cascadeur's loaders report failures by writing to their log instead of raising, so the tools read
the log slice written during the call: import_animation returns ok:false when it finds a paste
failure (for example a target skeleton whose joint names do not match, which is retargeting, T28).
export_scene moves an existing output aside first, so a silent export failure cannot be reported
as a success built on the previous file.

The module imports no Cascadeur module at load time: the unit tests load it with plain Python
(no csc, no numpy), and every function that needs Cascadeur imports it lazily.
"""

from __future__ import annotations

import os
import time

from ..errors import ToolError
from ._schema import schema

FBX = "fbx"
GLB = "glb"
BVH = "bvh"
USD = "usd"

GLB_EXTENSIONS = (".glb", ".gltf")
USD_EXTENSIONS = (".usd", ".usda", ".usdc", ".usdz")
MODEL_EXTENSIONS = (".fbx", *GLB_EXTENSIONS, *USD_EXTENSIONS)
ANIMATION_EXTENSIONS = (".fbx", ".bvh")

# The FBX loader method for each (objects, frames) pair. The stub documents these as separate
# entry points; pycsc/general/fbx.py uses the same mapping for its export filter.
FBX_EXPORT_METHODS = {
    ("all", "all"): "export_all_objects",
    ("selected", "all"): "export_scene_selected_objects",
    ("all", "selected"): "export_scene_selected_frames",
    ("selected", "selected"): "export_scene_selected",
}
FBX_IMPORT_METHODS = {
    "all": "import_animation",
    "selected_objects": "import_animation_to_selected_objects",
    "selected_frames": "import_animation_to_selected_frames",
}

USD_IMPORT_HINT = (
    "Cascadeur 2026.2.3 has no Python USD importer: USD support is native (usd_ms.dll and the usd/ plugins) and the "
    "type stubs contain no csc.usd module. Convert the asset to FBX or GLB and call import_model again, or import the "
    "USD file once by hand with File > Import."
)
USD_EXPORT_HINT = (
    "Cascadeur 2026.2.3 has no Python USD exporter: USD support is native (usd_ms.dll and the usd/ plugins) and the "
    "type stubs contain no csc.usd module. Export FBX or GLB instead, or use File > Export by hand."
)

# Cascadeur's loaders report these through the log and return normally, so a tool has to read the
# log slice it produced. The exact strings are from Cascadeur 2026.2.3.
ANIMATION_FAILURE_MARKERS = (
    "Import animation: failed",
    "Error in pasting animation or timeline data",
    "No BVH joint names matched",
    "BVH import failed",
    "BVH import: modify_update was rejected",
    "BVH parse error",
    "BVH has no motion frames",
    "Failed to read BVH",
)
CROSS_SKELETON_HINT = (
    "The animation was not applied. Cascadeur's FBX paste only maps joints whose names exist in the target scene; "
    "applying an animation to a different skeleton is retargeting (T28's retarget_animation). Read get_log for the "
    "full error and compare get_scene_info's joint names with the file."
)


class _ArgumentError(ToolError):
    """A bad or unsupported argument; hint carries the next step for the agent."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


# ---------------------------------------------------------------------------
# Small helpers shared by the tools (pure Python, unit-testable)
# ---------------------------------------------------------------------------


def _common():
    """Import the Cascadeur-side helpers lazily so plain Python can load this module."""
    from .. import common

    return common


def _fail(common, exc: ToolError) -> dict:
    return common.fail(str(exc), hint=getattr(exc, "hint", None))


def _suffix(path) -> str:
    return os.path.splitext(str(path))[1].lower()


def _choice(value, name: str, allowed) -> str:
    """Return value as one of allowed (case-insensitive), or raise _ArgumentError."""
    options = list(allowed)
    if not isinstance(value, str):
        raise _ArgumentError(
            f"{name} must be one of {', '.join(options)} (got {value!r}).",
            hint=f"Use one of: {', '.join(options)}.",
        )
    key = value.strip().lower()
    if key not in options:
        raise _ArgumentError(f"Unknown {name} {value!r}.", hint=f"Use one of: {', '.join(options)}.")
    return key


def _existing_file(path, kind: str) -> str:
    """Validate an import source and return it as an absolute native path."""
    if not path or not isinstance(path, str):
        raise _ArgumentError(f"The {kind} path must be a non-empty string.", hint="Pass an absolute file path.")
    if not os.path.isabs(path):
        raise _ArgumentError(
            f"The {kind} path must be absolute (got {path!r}).",
            hint="Join the file name with its folder first, for example C:/Users/me/asset.fbx.",
        )
    normalized = os.path.normpath(path)
    if not os.path.isfile(normalized):
        raise _ArgumentError(
            f"{kind} file not found: {normalized!r}.",
            hint="Check the path; output files land in %TEMP%\\cascadeur-mcp-plus-tests\\<ticket>\\ during tests.",
        )
    return normalized


def cascadeur_path(path: str) -> str:
    """Cascadeur's own scripts pass forward-slash paths to the loaders; mirror that."""
    return os.path.abspath(path).replace("\\", "/")


def model_format(path) -> tuple[str, str]:
    """Return (absolute source path, format) for a model file, or raise _ArgumentError."""
    source = _existing_file(path, "model")
    suffix = _suffix(source)
    if suffix == ".fbx":
        return source, FBX
    if suffix in GLB_EXTENSIONS:
        return source, GLB
    if suffix in USD_EXTENSIONS:
        raise _ArgumentError(f"USD import is not available ({suffix} file).", hint=USD_IMPORT_HINT)
    raise _ArgumentError(
        f"Unsupported model file {source!r}.",
        hint=f"import_model accepts {', '.join(MODEL_EXTENSIONS)}; USD files are not supported by the Python API.",
    )


def animation_format(path) -> tuple[str, str]:
    """Return (absolute source path, format) for an animation file, or raise _ArgumentError."""
    source = _existing_file(path, "animation")
    suffix = _suffix(source)
    if suffix == ".fbx":
        return source, FBX
    if suffix == ".bvh":
        return source, BVH
    if suffix in USD_EXTENSIONS:
        raise _ArgumentError(f"USD animation import is not available ({suffix} file).", hint=USD_IMPORT_HINT)
    raise _ArgumentError(
        f"Unsupported animation file {source!r}.",
        hint=f"import_animation accepts {', '.join(ANIMATION_EXTENSIONS)} files.",
    )


def export_format(path, format=None) -> str:
    """Return fbx/glb for an export target; format overrides the extension and must match it."""
    suffix = _suffix(path)
    if format is None:
        if suffix == ".fbx":
            return FBX
        if suffix in GLB_EXTENSIONS:
            return GLB
        if suffix in USD_EXTENSIONS:
            raise _ArgumentError("USD export is not available.", hint=USD_EXPORT_HINT)
        raise _ArgumentError(
            f"Cannot tell the export format from {str(path)!r}.",
            hint="End the path with .fbx or .glb, or pass format='fbx' or format='glb'.",
        )
    fmt = _choice(format, "format", ("fbx", "glb", "usd"))
    if fmt == USD:
        raise _ArgumentError("USD export is not available.", hint=USD_EXPORT_HINT)
    expected = ".fbx" if fmt == FBX else GLB_EXTENSIONS
    allowed = (expected,) if isinstance(expected, str) else expected
    if suffix not in allowed:
        raise _ArgumentError(
            f"format={fmt!r} does not match the path extension {suffix or '(none)'!r}.",
            hint=f"Give the path a {' or '.join(allowed)} extension, or drop format and let it come from the path.",
        )
    return fmt


def export_target(common, path, format, objects, frames, overwrite) -> tuple[str, str, str, str, str]:
    """Validate an export call; returns (path, format, objects, frames, fbx method name)."""
    fmt = export_format(path, format)
    objects = _choice(objects, "objects", ("all", "selected"))
    frames = _choice(frames, "frames", ("all", "selected"))
    extension = ".fbx" if fmt == FBX else ".glb"
    target = common.output_path(path, overwrite=overwrite, extensions=[extension])
    return target, fmt, objects, frames, FBX_EXPORT_METHODS[(objects, frames)]


def take_index(takes, take) -> int:
    """Map a take name (case-insensitive) or 0-based index to FbxLoader's num_take (-1 = default)."""
    if take is None:
        return -1
    if isinstance(take, bool):
        raise _ArgumentError(f"take must be a name or an index (got {take!r}).", hint=f"Available takes: {takes}.")
    if isinstance(take, int):
        if 0 <= take < len(takes):
            return take
        raise _ArgumentError(
            f"take index {take} is out of range for this FBX file ({len(takes)} takes).",
            hint=f"Available takes: {takes or 'none'}.",
        )
    name = str(take).strip()
    for index, candidate in enumerate(takes):
        if str(candidate).strip().lower() == name.lower():
            return index
    raise _ArgumentError(
        f"Take {str(take)!r} was not found in this FBX file.",
        hint=f"Available takes: {takes or 'none'}. Pass a name from list_fbx_takes or a 0-based index.",
    )


def _fbx_loader(app, view_scene):
    """The FbxLoader bound to view_scene (csc.fbx.FbxSceneLoader.get_fbx_loader)."""
    return app.get_tools_manager().get_tool("FbxSceneLoader").get_fbx_loader(view_scene)


def _takes(loader, source: str) -> list[str]:
    try:
        takes = loader.get_takes(cascadeur_path(source))
    except Exception as exc:  # the loader reports bad files through pybind exceptions
        raise _ArgumentError(
            f"Could not read the FBX takes from {source!r}: {exc}",
            hint="Check get_log; the file may not be a valid FBX.",
        ) from exc
    return [str(name) for name in (takes or [])]


def _last_key(scene):
    """Highest key position across the scene's animation layers, or None when there are none."""
    try:
        layers_viewer = scene.layers_viewer()
        positions = []
        for layer_id in layers_viewer.all_layer_ids():
            if layer_id.is_null():
                continue
            try:
                positions.append(int(layers_viewer.layer(layer_id).last_key_pos()))
            except Exception:
                continue
    except Exception:
        return None
    return max(positions) if positions else None


def _pose_sample(scene, frame: int = 1) -> list[str]:
    """Stringify a few joint transforms at one frame; comparing two samples tells whether an import
    rewrote the animation, which a timeline boundary alone cannot (a rigged scene already spans
    0..100)."""
    try:
        model_viewer = scene.model_viewer()
        behaviour_viewer = model_viewer.behaviour_viewer()
        data_viewer = model_viewer.data_viewer()
        joints = list(behaviour_viewer.get_behaviours("Joint"))[:6]
    except Exception:
        return []
    sample = []
    for joint in joints:
        try:
            owner = behaviour_viewer.get_behaviour_owner(joint)
            transform = behaviour_viewer.get_behaviour_by_name(owner, "Transform")
            if transform.is_null():
                continue
        except Exception:
            continue
        for name in ("local_rotation", "local_position"):
            try:
                data_id = behaviour_viewer.get_behaviour_data(transform, name)
                if data_id.is_null():
                    continue
                sample.append(f"{name}={data_viewer.union_get_data_value(data_id, frame)}")
            except Exception:
                continue
    return sample


def _animation_state(view_scene, scene) -> dict:
    """Animation length plus a pose sample, for the before/after comparison in import_animation."""
    try:
        boundary = view_scene.animation_boundary()
        first, last = int(boundary.first_frame), int(boundary.last_frame)
    except Exception:
        first = last = None
    try:
        size = int(scene.model_viewer().data_viewer().get_animation_size())
    except Exception:
        size = None
    return {
        "first_frame": first,
        "last_frame": last,
        "size": size,
        "last_key": _last_key(scene),
        "pose": _pose_sample(scene),
    }


def _public_state(state: dict) -> dict:
    return {key: value for key, value in state.items() if key != "pose"}


def _behaviour_counts(scene) -> dict:
    bv = scene.model_viewer().behaviour_viewer()
    counts = {}
    for name in ("RigInfo", "AnimationInfo", "Joint", "MeshObject"):
        if bv.is_valid_behaviour_type(name):
            counts[name] = len(bv.get_behaviours(name))
    return counts


def _log_hint(common, lines: int = 40) -> dict:
    return {"log": common.log_tail(lines=lines)}


def _log_position(common):
    """Byte offset in Cascadeur's log before an operation (None when the log cannot be read)."""
    try:
        return common._log_size()
    except Exception:
        return None


def _log_since(common, position, lines: int = 200) -> list[str]:
    """Log lines written after position (the plain tail when no position is available)."""
    try:
        if position is None:
            return list(common.log_tail(lines=lines))
        return list(common.log_tail(since=position, lines=lines))
    except Exception:
        return []


def find_animation_failure(lines) -> str | None:
    """Return the Cascadeur log marker that says an animation import failed, or None."""
    text = "\n".join(str(line) for line in lines).lower()
    for marker in ANIMATION_FAILURE_MARKERS:
        if marker.lower() in text:
            return marker
    return None


def _move_aside(target: str):
    """Move an existing output file aside before overwriting it; returns the backup path or None.

    Raises _ArgumentError when the file cannot be moved. With the old file out of the way, the
    tool can tell a real export (the path appears again) from a silent failure (it does not), and
    it can put the original back when the exporter fails.
    """
    if not os.path.isfile(target):
        return None
    backup = f"{target}.mcp-backup-{os.getpid()}-{time.time_ns()}"
    try:
        os.replace(target, backup)
    except OSError as exc:
        raise _ArgumentError(
            f"Could not move the existing file {target!r} aside before overwriting it: {exc}",
            hint="Close the program that has the file open, or export to another path.",
        ) from exc
    return backup


def _restore_moved_aside(target: str, backup) -> str | None:
    """Put a backup back after a failed export; returns an error message when that fails."""
    if backup is None:
        return None
    try:
        if os.path.isfile(target):
            os.remove(target)  # a partial file from the failed export
        os.replace(backup, target)
    except OSError as exc:
        return f"could not restore {backup!r} to {target!r}: {exc}"
    return None


def _discard_moved_aside(backup) -> None:
    if backup is None:
        return
    try:
        os.remove(backup)
    except OSError:
        pass


def _export_failure(common, message: str, hint: str, target: str, backup) -> dict:
    """Build an export failure result and restore the file that overwrite moved aside."""
    restore_error = _restore_moved_aside(target, backup)
    result = common.fail(message, hint=hint, **_log_hint(common))
    if restore_error:
        result["restore_error"] = restore_error
    return result


def _post_import_failure(common, source: str, fmt: str, stage: str, exc) -> dict:
    """A failure after the native import already ran: say clearly that the model is in the scene."""
    return common.fail(
        f"Cascadeur imported {source!r} ({fmt.upper()}), but {stage} afterwards failed: {exc}",
        hint="The model from that file IS in the scene now: do not import it again. Check get_log, then run "
        "enter_rig_mode (or fix the scene by hand) before Quick Rigging.",
        imported=True,
        path=source,
        format=fmt,
        **_log_hint(common),
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def import_model(path: str, enter_rig_mode: bool = True, timeout: float | None = None) -> dict:
    """Import an FBX or GLB model and enter Rig mode when the scene has no RigInfo yet."""
    common = _common()
    try:
        source, fmt = model_format(path)
    except ToolError as exc:
        return _fail(common, exc)

    import csc  # only available inside Cascadeur

    try:
        app, view_scene, scene = common.scenes()
        before = len(scene.model_viewer().get_objects())
    except Exception as exc:
        return common.fail(
            f"Could not read the current scene before importing {source!r}: {exc}",
            hint="Check that a scene is open and that MCP Plus is healthy (get_scene_info).",
            **_log_hint(common),
        )
    try:
        if fmt == FBX:
            _fbx_loader(app, view_scene).import_model(cascadeur_path(source))
        else:
            options = csc.glb.ImportOptions()
            options.include_animation = True
            options.include_objects = True
            options.throw_exception = True
            csc.glb.process_import(scene, cascadeur_path(source), options)
    except Exception as exc:  # native loaders raise pybind exceptions for bad files
        return common.fail(
            f"Cascadeur's {fmt.upper()} import failed: {exc}",
            hint="Check get_log; the file may be corrupt or exported with unsupported features.",
            **_log_hint(common),
        )

    # From here on the native import has already happened: every failure must say so, because
    # re-running import_model would add a second copy of the model.
    result = {"path": source, "format": fmt}
    try:
        _, view_scene, scene = common.scenes()
        counts = _behaviour_counts(scene)
        total = len(scene.model_viewer().get_objects())
    except Exception as exc:
        return _post_import_failure(common, source, fmt, "reading the scene", exc)
    if total == before:
        return common.fail(
            f"Cascadeur imported no objects from {source!r}.",
            hint="Check get_log; the file may contain only animation. Use import_animation for that.",
            **_log_hint(common),
        )
    result.update(
        objects=total,
        objects_added=total - before,
        joints=counts.get("Joint", 0),
        meshes=counts.get("MeshObject", 0),
    )

    # Cascadeur's loader records its own "Import FBX Model"/"Load GLB" undo command.
    undo_steps = 1
    rig_error = None
    if enter_rig_mode and counts.get("RigInfo", 0) == 0:
        # The same entry point the enter_rig_mode tool uses: a raw Python import skips the
        # RigInfo creation the UI performs, and Quick Rigging crashes without it.
        try:
            from .. import scene_tools

            rig = scene_tools.enter_rig_mode()
        except Exception as exc:
            return _post_import_failure(common, source, fmt, "entering Rig mode", exc)
        if "error" in rig:
            rig_error = str(rig["error"])
        else:
            result["rig_path"] = rig.get("path")
            if rig.get("next"):
                result["next"] = rig["next"]
            if rig.get("changed", True):
                undo_steps += 1  # "Rig mode on"
        try:
            _, view_scene, scene = common.scenes()  # Rig mode reloads the scene
            counts = _behaviour_counts(scene)
        except Exception as exc:
            return _post_import_failure(common, source, fmt, "reading the scene after Rig mode", exc)

    result["animation_info"] = counts.get("AnimationInfo", 0)
    result["rig_info"] = counts.get("RigInfo", 0)
    result["rig_mode"] = counts.get("AnimationInfo", 0) == 1
    result["undo_steps"] = undo_steps
    if rig_error:
        result["rig_error"] = rig_error
    if enter_rig_mode and counts.get("RigInfo", 0) > 0 and not result["rig_mode"]:
        result["hint"] = "Rig mode is not active; run enter_rig_mode or check get_log."
    return common.ok(**result)


def import_animation(
    path: str,
    take=None,
    target: str = "all",
    timeout: float | None = None,
) -> dict:
    """Apply an FBX or BVH animation to the current scene."""
    common = _common()
    try:
        source, fmt = animation_format(path)
        target_key = _choice(target, "target", tuple(FBX_IMPORT_METHODS))
        if fmt == BVH and target_key != "all":
            raise _ArgumentError(
                "A BVH file can only be applied to all joints (Cascadeur's BVH command has no selection modes).",
                hint="Use target='all', or import an FBX when you need selected_objects/selected_frames.",
            )
    except ToolError as exc:
        return _fail(common, exc)

    try:
        app, view_scene, scene = common.scenes()
        before = _animation_state(view_scene, scene)
    except Exception as exc:
        return common.fail(
            f"Could not read the current scene before importing {source!r}: {exc}",
            hint="Check that a scene is open and that MCP Plus is healthy (get_scene_info).",
            **_log_hint(common),
        )
    log_position = _log_position(common)
    try:
        if fmt == FBX:
            loader = _fbx_loader(app, view_scene)
            # get_takes() is only worth a native call when the caller names a take.
            takes = _takes(loader, source) if take is not None else []
            index = take_index(takes, take)
            getattr(loader, FBX_IMPORT_METHODS[target_key])(cascadeur_path(source), index)
            take_name = None if index < 0 else takes[index]
        else:
            take_name = None
            _run_bvh_import(common, scene, source)
    except ToolError as exc:
        return _fail(common, exc)
    except Exception as exc:
        return common.fail(
            f"Cascadeur's {fmt.upper()} animation import failed: {exc}",
            hint="Check get_log and that the file contains animation for joints with matching names.",
            **_log_hint(common),
        )

    # The FBX loader and the BVH command report paste failures by writing to the log and
    # returning normally, so only the log slice written during this call can tell them apart.
    log_lines = _log_since(common, log_position, lines=200)
    failure = find_animation_failure(log_lines)
    if failure:
        return common.fail(
            f"Cascadeur logged a failure while importing {source!r}: {failure}",
            hint=CROSS_SKELETON_HINT,
            format=fmt,
            target=target_key,
            marker=failure,
            log=log_lines[-60:],
        )

    try:
        _, view_scene, scene = common.scenes()
        after = _animation_state(view_scene, scene)
    except Exception as exc:
        return common.fail(
            f"Cascadeur ran the {fmt.upper()} animation import for {source!r}, but reading the scene afterwards "
            f"failed: {exc}",
            hint="The animation may already be applied: check the timeline and get_log before importing it again.",
            format=fmt,
            target=target_key,
            **_log_hint(common),
        )
    changed = after["pose"] != before["pose"] or after["last_key"] != before["last_key"]
    result = {
        "path": source,
        "format": fmt,
        "target": target_key,
        "take": take_name,
        "before": _public_state(before),
        "after": _public_state(after),
        "changed": changed,
        # One native undo command: "Import FBX Animation" or the BVH command's modify_update.
        "undo_steps": 1,
    }
    if not changed:
        result["hint"] = (
            "Cascadeur logged no failure but the sampled poses and keys did not change. Check that the file's joint "
            "names match the scene's joints (get_scene_info lists them) and read get_log."
        )
    return common.ok(**result)


def list_fbx_takes(path: str, timeout: float | None = None) -> dict:
    """List the animation take names stored in an FBX file."""
    common = _common()
    try:
        source = _existing_file(path, "FBX")
        if _suffix(source) != ".fbx":
            raise _ArgumentError(
                f"list_fbx_takes reads .fbx files only (got {source!r}).",
                hint="Takes only exist in FBX files; GLB stores a single animation.",
            )
    except ToolError as exc:
        return _fail(common, exc)

    app, view_scene, _ = common.scenes()
    try:
        takes = _takes(_fbx_loader(app, view_scene), source)
    except ToolError as exc:
        return _fail(common, exc)
    return common.ok(path=source, takes=takes, count=len(takes))


def export_scene(
    path: str,
    format: str | None = None,
    objects: str = "all",
    frames: str = "all",
    overwrite: bool = False,
    timeout: float | None = None,
) -> dict:
    """Export the current scene to FBX or GLB."""
    common = _common()
    try:
        target, fmt, objects, frames, method = export_target(common, path, format, objects, frames, overwrite)
    except ToolError as exc:
        return _fail(common, exc)

    import csc  # only available inside Cascadeur

    app, view_scene, scene = common.scenes()
    try:
        export_available = bool(app.is_export_available())
    except Exception as exc:
        return common.fail(f"Could not check Cascadeur's export license: {exc}", **_log_hint(common))
    if not export_available:
        return common.fail(
            "Cascadeur's export is not available in this installation (license check failed).",
            hint="Cascadeur needs an export-capable license to write FBX/GLB files; "
            "check the license in Cascadeur's window title or get_scene_info's license field.",
        )
    # An existing file is moved aside first: if Cascadeur only logs an error and writes nothing, the
    # old file must not make the export look like a success (and it gets put back on failure).
    try:
        backup = _move_aside(target) if overwrite else None
    except ToolError as exc:
        return _fail(common, exc)
    try:
        if fmt == FBX:
            getattr(_fbx_loader(app, view_scene), method)(cascadeur_path(target))
        else:
            options = csc.glb.ExportOptions()
            options.include_animation = True
            options.for_selected_objects = objects == "selected"
            options.for_selected_interval = frames == "selected"
            options.throw_exception = True
            csc.glb.process_export(scene, cascadeur_path(target), options)
    except Exception as exc:
        return _export_failure(
            common,
            f"Cascadeur's {fmt.upper()} export failed: {exc}",
            "Check get_log; exporting needs the mesh/joint data to be valid.",
            target,
            backup,
        )
    if not os.path.isfile(target):
        return _export_failure(
            common,
            f"Cascadeur did not write {target!r}: the exporter only logged the failure.",
            "Check get_log; a license or file permission problem can make the exporter stop early.",
            target,
            backup,
        )
    size = os.path.getsize(target)
    if size == 0:
        return _export_failure(
            common,
            f"Cascadeur wrote an empty file to {target!r}.",
            "Check get_log; the export was rejected rather than written.",
            target,
            backup,
        )
    _discard_moved_aside(backup)
    return common.ok(
        path=target,
        format=fmt,
        bytes=size,
        objects=objects,
        frames=frames,
        method=method if fmt == FBX else "process_export",
    )


# ---------------------------------------------------------------------------
# BVH: reuse Cascadeur's own "Import BVH animation" command
# ---------------------------------------------------------------------------


def _run_bvh_import(common, scene, source: str) -> None:
    """Run Cascadeur's official BVH command without its file dialog.

    commands/import_bvh_animation.run(scene) only opens a file dialog and passes apply_bvh to
    it as the callback, so the module is executed with csc.app.get_application swapped for a
    stub that hands the callback straight back. The callback then does the real work with the
    same code path (and the same single undo step) as the command in Cascadeur's menu.
    """
    import csc

    try:
        import commands.import_bvh_animation as bvh_import
    except Exception as exc:
        raise _ArgumentError(
            f"Cascadeur's BVH command is not importable: {exc}",
            hint="Use Scripts > Import BVH animation in Cascadeur's menu, or convert the file to FBX.",
        ) from exc

    captured: dict = {}

    class _DialogManager:
        def show_open_file_dialog(self, title, folder, filters, callback):
            captured["callback"] = callback

    class _Application:
        def get_file_dialog_manager(self):
            return _DialogManager()

    original = csc.app.get_application
    try:
        csc.app.get_application = lambda: _Application()
    except Exception as exc:  # the extension module refuses the patch on some builds
        raise _ArgumentError(
            f"Cascadeur's BVH command cannot be driven headlessly on this build: {exc}",
            hint="Use Scripts > Import BVH animation in Cascadeur's menu, or convert the file to FBX.",
        ) from exc
    try:
        bvh_import.run(scene)
    finally:
        csc.app.get_application = original

    callback = captured.get("callback")
    if callback is None:
        raise _ArgumentError(
            "Cascadeur's BVH command did not request a file.",
            hint="Use Scripts > Import BVH animation in Cascadeur's menu, or convert the file to FBX.",
        )
    callback(cascadeur_path(source))


TOOLS = [
    {
        "name": "import_model",
        "description": "Import an FBX or GLB model file into the current scene and enter Rig mode the way Cascadeur's "
        "own File > Import does. Use it to load a character before Quick Rigging: a bare Python import_model skips the "
        "RigInfo step, and Quick Rigging crashes without it. Returns the objects added, the Joint/MeshObject counts, "
        "the AnimationInfo/RigInfo counts and whether Rig mode is active. This is NOT one MCP undo step: Cascadeur's "
        "loader records its own undo command (Import FBX Model / Load GLB) and entering Rig mode records another, so "
        "the result's undo_steps (1 or 2) is how many undos revert the call completely. Only .fbx and .glb files; "
        "Cascadeur's Python API has no USD importer. A failure after the import says the model is already in the scene "
        "(imported=true in the result), and rig_error carries a Rig-mode problem without failing the whole call.",
        "inputSchema": schema(
            {
                "path": {"type": "string", "description": "Absolute path to a .fbx or .glb file."},
                "enter_rig_mode": {
                    "type": "boolean",
                    "description": "Enter Rig mode after the import when the scene has no RigInfo (default true).",
                },
                "timeout": {
                    "type": "number",
                    "description": "Seconds to wait for the import and the Rig-mode step (default 60, max 600).",
                },
            },
            ["path"],
        ),
        "fn": import_model,
        "idle": True,
        "timeout_arg": True,
    },
    {
        "name": "import_animation",
        "description": "Apply the animation stored in an FBX (or BVH) file to the current scene's character. "
        "target='all' retargets every joint, 'selected_objects' applies it to the selected objects only, and "
        "'selected_frames' writes it into the selected timeline frames; take picks one take from list_fbx_takes. "
        "Returns the animation length (frame range, last key) before and after plus whether the sampled joint poses "
        "changed, so you can tell whether the animation landed. Cascadeur reports a refused paste only in its log, so "
        "a logged failure such as 'Import animation: failed' or 'No BVH joint names matched' comes back as "
        "ok:false with the marker and log lines; a target skeleton with different joint names needs retargeting (T28). "
        "The call records one native undo step (undo_steps). "
        "Cascadeur's BVH command replaces the keyed animation on the layers it writes; use FBX for partial imports.",
        "inputSchema": schema(
            {
                "path": {"type": "string", "description": "Absolute path to a .fbx or .bvh file."},
                "take": {
                    "type": ["string", "integer"],
                    "description": "Take name or 0-based index from list_fbx_takes (default: the file's own default).",
                },
                "target": {
                    "type": "string",
                    "enum": ["all", "selected_objects", "selected_frames"],
                    "description": "Where to apply the animation (default all; BVH supports all only).",
                },
                "timeout": {
                    "type": "number",
                    "description": "Seconds to wait for the import (default 60, max 600).",
                },
            },
            ["path"],
        ),
        "fn": import_animation,
        "idle": True,
        "timeout_arg": True,
    },
    {
        "name": "list_fbx_takes",
        "description": "List the animation take names inside an FBX file. Use it before import_animation to pass an "
        "exact take name instead of guessing. Returns the take names in file order and their count; takes only exist "
        "in FBX files (GLB holds a single animation).",
        "inputSchema": schema(
            {
                "path": {"type": "string", "description": "Absolute path to a .fbx file."},
                "timeout": {"type": "number", "description": "Seconds to wait (default 60, max 600)."},
            },
            ["path"],
        ),
        "fn": list_fbx_takes,
        "timeout_arg": True,
    },
    {
        "name": "export_scene",
        "description": "Export the current scene to an FBX or GLB file. format comes from the file extension (or pass "
        "format explicitly; it must match the extension); objects='all' or 'selected' and frames='all' or 'selected' "
        "choose the content, and an existing file is only replaced when overwrite=true. An existing target is moved "
        "aside before the export, so a failed export can never be reported as success with the old file's size, and "
        "the original file is restored when the export fails. Returns the written path, its byte size, the options "
        "used and whether Cascadeur's export license is available. USD export has no Python API in Cascadeur "
        "2026.2.3, so .usd* paths return an error that says so.",
        "inputSchema": schema(
            {
                "path": {"type": "string", "description": "Absolute output path ending in .fbx or .glb."},
                "format": {
                    "type": "string",
                    "enum": ["fbx", "glb", "usd"],
                    "description": "Optional explicit format; must match the path's extension.",
                },
                "objects": {
                    "type": "string",
                    "enum": ["all", "selected"],
                    "description": "Export every object or only the selected ones (default all).",
                },
                "frames": {
                    "type": "string",
                    "enum": ["all", "selected"],
                    "description": "Export the whole animation or only the selected timeline frames (default all).",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Replace an existing file (default false, which returns an error).",
                },
                "timeout": {"type": "number", "description": "Seconds to wait for the export (default 60, max 600)."},
            },
            ["path"],
        ),
        "fn": export_scene,
        "timeout_arg": True,
    },
]
