"""MCP resource registry (T40).

Resources let an MCP client read editor state (scene summary, log, timeline, control points)
without calling a tool. The server exposes them through resources/list and resources/read.

Adding a resource works like adding a tool: a module exports a RESOURCES list and the registry
loads it automatically. This module is the registry and also exports the built-in resources;
any sibling module named resources_<topic>.py that exports RESOURCES is loaded too, in
file-name order. A duplicate URI is an error rather than a silent override.

Resource dict fields
--------------------
uri : str
    Unique URI, for example "cascadeur://scene".
name : str
    Short display name for MCP clients.
description : str
    English. What the resource contains and when to read it.
mimeType : str, optional (default "text/plain")
    "application/json" for JSON documents, "text/plain" for logs.
fn : callable
    Called with no arguments. Return a str (sent as-is) or a JSON-serializable value
    (serialized with json.dumps). Runs on the GUI thread by default.
gui : bool, optional (default True)
    Run fn on the GUI thread. Set False for functions that touch no Qt or scene state.
"""

from __future__ import annotations

import importlib
import os
import pkgutil


def _scene() -> dict:
    from . import scene_tools

    return scene_tools.scene_info()


def _log() -> str:
    from . import common

    lines = common.log_tail(lines=200)
    if not lines:
        return f"No log lines found at {common.LOG_PATH}."
    return "\n".join(lines)


def _timeline() -> dict:
    from . import scene_tools

    return scene_tools.scene_info()["frames"]


_KINDS = (
    ("_MainPoint", "main_point"),
    ("_DirectionPoint", "direction"),
    ("_Box", "box"),
)


def _controller_kind(name: str) -> str:
    for marker, kind in _KINDS:
        if marker in name:
            return kind
    return "other"


def _controllers() -> dict:
    """List the control points: every object that owns a Point behaviour."""
    from . import common

    _, view_scene, scene = common.scenes()
    model_viewer = scene.model_viewer()
    behaviour_viewer = model_viewer.behaviour_viewer()
    controllers = []
    if behaviour_viewer.is_valid_behaviour_type("Point"):
        owners = {behaviour_viewer.get_behaviour_owner(b) for b in behaviour_viewer.get_behaviours("Point")}
        for object_id in owners:
            name = model_viewer.get_object_name(object_id)
            controllers.append(
                {
                    "name": name,
                    "kind": _controller_kind(name),
                    "type": model_viewer.get_object_type_name(object_id),
                }
            )
    controllers.sort(key=lambda item: item["name"])
    result = {
        "scene": view_scene.name(),
        "count": len(controllers),
        "controllers": controllers[:500],
        "note": "Kinds are inferred from Cascadeur's point names; aliases and exact kinds arrive with T12.",
    }
    if len(controllers) > 500:
        result["truncated"] = True
    return result


RESOURCES = [
    {
        "uri": "cascadeur://scene",
        "name": "Scene",
        "description": "Current scene summary: name, path, frame range, object counts and sample, selection, "
        "behaviours and license flags. Same data as the get_scene_info tool.",
        "mimeType": "application/json",
        "fn": _scene,
    },
    {
        "uri": "cascadeur://log",
        "name": "Log",
        "description": "The last 200 lines of Cascadeur's log file, where Python tracebacks and errors from "
        "modify callbacks appear. Read it after an operation that may have failed silently.",
        "mimeType": "text/plain",
        "fn": _log,
        "gui": False,
    },
    {
        "uri": "cascadeur://timeline",
        "name": "Timeline",
        "description": "Current frame, first and last frame, and animation size of the active scene. "
        "Keyframe summaries are added when the timeline tools land (T14).",
        "mimeType": "application/json",
        "fn": _timeline,
    },
    {
        "uri": "cascadeur://controllers",
        "name": "Controllers",
        "description": "Control points of the active scene: object names with kind (main_point, direction, box or "
        "other) and object type. Character aliases and joints are added when the rig tools land (T12).",
        "mimeType": "application/json",
        "fn": _controllers,
    },
]


def load_resources() -> list[dict]:
    """Return this module's resources plus every sibling resources_<topic>.py module's.

    A resource module must define RESOURCES as a list of resource dicts. Modules whose name
    starts with an underscore are helpers. A duplicate URI is an error rather than a silent
    override, like duplicate tool names in tools/__init__.py.
    """
    groups: list[tuple[str, object]] = [(__name__, RESOURCES)]
    package = __package__
    if package:
        package_dir = os.path.dirname(os.path.abspath(__file__))
        for info in sorted(pkgutil.iter_modules([package_dir]), key=lambda module: module.name):
            if not info.name.startswith("resources_"):
                continue
            module = importlib.import_module(f"{package}.{info.name}")
            groups.append((module.__name__, getattr(module, "RESOURCES", None)))

    resources: list[dict] = []
    origin: dict[str, str] = {}
    for module_name, module_resources in groups:
        if not isinstance(module_resources, list):
            raise RuntimeError(f"{module_name} must define RESOURCES as a list of resource dicts")
        for resource in module_resources:
            for key in ("uri", "name", "description", "fn"):
                if key not in resource:
                    raise RuntimeError(f"{module_name} has a resource without {key!r}: {resource!r}")
            uri = resource["uri"]
            if uri in origin:
                raise RuntimeError(f"Duplicate resource {uri!r} in {module_name} and {origin[uri]}")
            origin[uri] = module_name
            resources.append(resource)
    return resources
