"""Scene inspection and Python execution helpers (GUI thread only)."""

from __future__ import annotations

import contextlib
import io
import os
import traceback
from collections import Counter

import csc

LOG_PATH = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Nekki Limited", "Cascadeur", "logs", "cascadeur_log.log")


def _scenes():
    app = csc.app.get_application()
    view_scene = app.current_scene()
    return app, view_scene, view_scene.domain_scene()


def _names(model_viewer, ids, limit):
    names = []
    for object_id in list(ids)[:limit]:
        try:
            names.append(model_viewer.get_object_name(object_id))
        except TypeError:
            # Selections can contain tool objects (e.g. AutoPosing points), which have no model name.
            names.append(f"<{type(object_id).__name__} {object_id}>")
    return names


def scene_info(max_objects: int = 80) -> dict:
    app, view_scene, scene = _scenes()
    mv = scene.model_viewer()
    bv = mv.behaviour_viewer()
    dv = mv.data_viewer()
    objects = mv.get_objects()
    types = Counter(mv.get_object_type_name(o) for o in objects)
    selection = scene.selector().selected()
    selected_ids = list(selection.ids)
    boundary = view_scene.animation_boundary()
    behaviour_counts = {}
    for name in ("RigInfo", "AnimationInfo", "QrtInfo", "Joint", "MeshObject", "AutoPosing"):
        if bv.is_valid_behaviour_type(name):  # invalid names raise and spam the log
            behaviour_counts[name] = len(bv.get_behaviours(name))
    return {
        "scene": {
            "name": view_scene.name(),
            "path": view_scene.get_path_name(),
            "open_scenes": len(app.get_scene_manager().scenes()),
            "gravity_per_frame": view_scene.gravity_per_frame(),
        },
        "frames": {
            "current": scene.get_current_frame(),
            "first": boundary.first_frame,
            "last": boundary.last_frame,
            "animation_size": dv.get_animation_size(),
        },
        "objects": {
            "count": len(objects),
            "by_type": dict(types.most_common()),
            "sample": [
                {"name": mv.get_object_name(o), "type": mv.get_object_type_name(o)} for o in objects[:max_objects]
            ],
        },
        "selection": {"count": len(selected_ids), "names": _names(mv, selected_ids, 50)},
        "behaviours": behaviour_counts,
        "license": {"pro_features": app.is_pro_features_available(), "export": app.is_export_available()},
    }


def object_info(name: str, max_properties: int = 40) -> dict:
    _, _, scene = _scenes()
    mv = scene.model_viewer()
    bv = mv.behaviour_viewer()
    ids = mv.get_objects(name)
    if not ids:
        lowered = name.lower()
        ids = [o for o in mv.get_objects() if lowered in mv.get_object_name(o).lower()][:5]
    if not ids:
        return {"error": f"No object named like {name!r}"}
    result = []
    for object_id in ids[:5]:
        behaviours = []
        for behaviour_id in bv.get_behaviours(object_id):
            entry = {"name": bv.get_behaviour_name(behaviour_id)}
            try:
                entry["properties"] = bv.get_behaviour_property_names(behaviour_id)[:max_properties]
            except Exception:
                pass
            behaviours.append(entry)
        result.append(
            {
                "name": mv.get_object_name(object_id),
                "type": mv.get_object_type_name(object_id),
                "id": str(object_id),
                "behaviours": behaviours,
            }
        )
    return {"objects": result, "matches": len(ids)}


def enter_rig_mode(color=(0.0, 0.5, 0.0)) -> dict:
    """Enter Rig mode through Cascadeur's own rig_mode.on entry points.

    - Already in Rig mode (one AnimationInfo): nothing to do.
    - A RigInfo exists: rig_mode.on.run_raw, as Cascadeur's samples do.
    - Freshly imported model (no RigInfo): rig_mode.on.run_after_import, the step a UI import
      performs automatically but a Python import_model skips. It creates the RigInfo for the
      imported joints, enters Rig mode and shows the "Rig mode helper" dialog offering Quick Rigging.
    """
    import rig_mode.on as rig_mode_on

    _, _, scene = _scenes()
    mv = scene.model_viewer()
    bv = mv.behaviour_viewer()
    before = len(bv.get_behaviours("AnimationInfo"))
    if before == 1:
        return {"rig_mode": True, "changed": False, "animation_info": 1}
    if before > 1:
        return {"error": f"Scene has {before} AnimationInfo; resolve that manually before rigging."}
    rgb = [float(c) for c in color]
    if bv.get_behaviours("RigInfo"):
        path = "run_raw"
        rig_mode_on.run_raw(scene, rgb)
    else:
        path = "run_after_import"
        rig_mode_on.run_after_import(scene, mv.get_objects(), rgb)
    after = len(bv.get_behaviours("AnimationInfo"))
    result = {"rig_mode": after == 1, "changed": True, "path": path, "animation_info": after}
    if path == "run_after_import":
        result["next"] = (
            "A 'Rig mode helper' dialog asks to launch the Quick Rigging tool; press its Yes button via dialogs/ui_click."
        )
    return result


class _Tee(io.TextIOBase):
    def __init__(self, *streams):
        self.streams = [s for s in streams if s is not None]

    def write(self, text):
        for stream in self.streams:
            try:
                stream.write(text)
            except Exception:
                pass
        return len(text)

    def flush(self):
        for stream in self.streams:
            try:
                stream.flush()
            except Exception:
                pass


def run_python(code: str, extra_namespace: dict | None = None) -> dict:
    """Run code with csc/app/scene in scope. Returns the value of a lone expression or of `result`."""
    import sys

    app, view_scene, scene = _scenes()
    namespace = {"csc": csc, "app": app, "scene": scene, "view_scene": view_scene, **(extra_namespace or {})}
    captured = io.StringIO()
    try:
        compiled = compile(code, "<mcp-plus>", "eval")
        is_expression = True
    except SyntaxError:
        is_expression = False
    try:
        with contextlib.redirect_stdout(_Tee(captured, sys.stdout)):
            if is_expression:
                value = eval(compiled, namespace, namespace)
                has_value = True
            else:
                exec(compile(code, "<mcp-plus>", "exec"), namespace, namespace)
                value = namespace.get("result")
                has_value = "result" in namespace
        out = {"ok": True, "stdout": captured.getvalue()}
        if has_value:
            out["value"] = repr(value)
        return out
    except Exception:
        return {"ok": False, "stdout": captured.getvalue(), "error": traceback.format_exc()}


def get_log(lines: int = 60, contains: str | None = None) -> dict:
    if not os.path.exists(LOG_PATH):
        return {"error": f"Log not found: {LOG_PATH}"}
    with open(LOG_PATH, encoding="utf-8", errors="replace") as handle:
        content = handle.readlines()
    if contains:
        content = [line for line in content if contains.lower() in line.lower()]
    return {"path": LOG_PATH, "lines": [line.rstrip("\n") for line in content[-max(1, int(lines)) :]]}
