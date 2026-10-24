"""run_python: arbitrary Python code on Cascadeur's GUI thread."""

from __future__ import annotations

from .. import scene_tools, ui
from ._schema import schema

TOOLS = [
    {
        "name": "run_python",
        "description": (
            "Run Python inside Cascadeur on the GUI thread with csc, app, scene (domain scene), view_scene and ui "
            "in scope. Use it only when no dedicated tool covers the operation: call api_search first to check "
            "the API, and prefer the scene, pose and timeline tools for normal work. Returns stdout and the value "
            "of a lone expression or a variable named `result`; scene.modify*/edit return False when their "
            "callback failed, so check the result."
        ),
        "inputSchema": schema(
            {
                "code": {
                    "type": "string",
                    "description": "Python source; a lone expression is evaluated, otherwise it runs as statements and returns `result`.",
                },
                "timeout": {
                    "type": "number",
                    "description": "Seconds to wait before the GUI call times out (default 60, max 600).",
                },
            },
            ["code"],
        ),
        "requires": [
            "csc.app.get_application",
        ],
        "fn": lambda code, timeout=None: scene_tools.run_python(code, {"ui": ui}),
        "timeout_arg": True,
    },
]
