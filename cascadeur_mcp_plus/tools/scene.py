"""Scene inspection, rig mode and log reading."""

from __future__ import annotations

from .. import scene_tools
from ._schema import schema

TOOLS = [
    {
        "name": "get_scene_info",
        "description": "Summarize the current scene: name/path, frames, object counts by type, selection, rig/animation behaviours, license flags.",
        "inputSchema": schema({"max_objects": {"type": "integer"}}),
        "fn": scene_tools.scene_info,
    },
    {
        "name": "get_object_info",
        "description": "Describe objects by exact or partial name: type, id and behaviours with their property names.",
        "inputSchema": schema({"name": {"type": "string"}, "max_properties": {"type": "integer"}}, ["name"]),
        "fn": scene_tools.object_info,
    },
    {
        "name": "enter_rig_mode",
        "description": "Enter Cascadeur's Rig mode using its own rig_mode.on entry points (run_after_import for a freshly imported model). "
        "Required before Quick Rigging's Add rig elements / Generate rig: without it Cascadeur crashes. "
        "Runs on the scene idle event like the official script server.",
        "inputSchema": schema(
            {
                "color": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Rig color RGB 0..1 (default [0, 0.5, 0]).",
                }
            }
        ),
        "fn": scene_tools.enter_rig_mode,
        "idle": True,
    },
    {
        "name": "get_log",
        "description": "Read the tail of Cascadeur's log file (errors and Python tracebacks land here), optionally filtered.",
        "inputSchema": schema({"lines": {"type": "integer"}, "contains": {"type": "string"}}),
        "fn": scene_tools.get_log,
        "gui": False,
    },
]
