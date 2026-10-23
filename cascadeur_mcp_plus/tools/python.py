"""run_python: arbitrary Python code on Cascadeur's GUI thread."""

from __future__ import annotations

from .. import scene_tools, ui
from ._schema import schema

TOOLS = [
    {
        "name": "run_python",
        "description": "Run Python inside Cascadeur on the GUI thread with csc, app, scene (domain scene), view_scene and ui in scope. "
        "Returns stdout and the value of a lone expression or of a variable named `result`. "
        "scene.modify*/edit return False when their callback failed; check it.",
        "inputSchema": schema(
            {
                "code": {"type": "string"},
                "timeout": {"type": "number", "description": "Seconds to wait (default 60, max 600)."},
            },
            ["code"],
        ),
        "fn": lambda code, timeout=None: scene_tools.run_python(code, {"ui": ui}),
        "timeout_arg": True,
    },
]
