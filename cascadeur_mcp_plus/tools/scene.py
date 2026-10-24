"""Scene inspection, rig mode and log reading."""

from __future__ import annotations

from .. import scene_tools
from ._schema import schema

TOOLS = [
    {
        "name": "get_scene_info",
        "description": (
            "Summarize the current scene: name/path, current and animation frames, object counts by type, "
            "selection, rig/animation behaviours and license flags. Use it first to learn what is open, then "
            "list_controllers for a character's control points or get_timeline for the range. Returns scene, "
            "frames, objects (count, by_type and a sample list), selection, behaviours and license; max_objects "
            "limits the sample."
        ),
        "inputSchema": schema(
            {
                "max_objects": {
                    "type": "integer",
                    "description": "Maximum objects listed in objects.sample (default 80).",
                }
            }
        ),
        "fn": scene_tools.scene_info,
    },
    {
        "name": "get_object_info",
        "description": (
            "Describe objects by exact or partial name: type, id and behaviours with their property names. Use it "
            "when list_controllers does not cover an object (mesh, camera or helper) or to inspect behaviours "
            "before run_python. Returns objects (up to five matches) with their behaviours/properties and the "
            "number of matches; max_properties limits the property names per behaviour."
        ),
        "inputSchema": schema(
            {
                "name": {
                    "type": "string",
                    "description": "Exact or partial object name, e.g. 'pelvis' or 'Cascy_mesh'.",
                },
                "max_properties": {
                    "type": "integer",
                    "description": "Maximum property names listed per behaviour (default 40).",
                },
            },
            ["name"],
        ),
        "fn": scene_tools.object_info,
    },
    {
        "name": "enter_rig_mode",
        "description": (
            "Enter Cascadeur's Rig mode using its own rig_mode.on entry points (run_after_import for a freshly "
            "imported model). Use it after import_model or before Quick Rigging: without Rig mode Cascadeur "
            "crashes when rig elements are generated. Runs on the scene idle event like the official script server. "
            "Returns rig_mode, changed, the entry point used and the AnimationInfo count; when a Rig mode helper "
            "appears, next explains the dialog to confirm."
        ),
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
        "description": (
            "Read the tail of Cascadeur's log file, where errors and Python tracebacks land, optionally filtered. "
            "Use it after a tool returns an unexpected error or traceback, or while debugging run_python. Returns "
            "the log path and the selected lines; lines limits the tail and contains keeps only lines containing "
            "the text."
        ),
        "inputSchema": schema(
            {
                "lines": {"type": "integer", "description": "Number of trailing log lines to read (default 60)."},
                "contains": {"type": "string", "description": "Only return lines containing this text."},
            }
        ),
        "fn": scene_tools.get_log,
        "gui": False,
    },
]
