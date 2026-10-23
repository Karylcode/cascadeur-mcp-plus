# Starts Cascadeur MCP Plus inside Cascadeur (on its GUI thread), or restarts it with fresh code.
#
# Run it from Cascadeur's own Python, e.g. through the official script server's run_script tool or
# the Python Console, passing this file's path so it can find the package next to it:
#
#   p = r"C:\path\to\cascadeur-mcp-plus\start_mcp_plus.py"
#   exec(open(p, encoding="utf-8").read(), {"__file__": p})
#
# Add "MCP_PLUS_PORT": <port> to that dict to listen on another port. The stdio proxy does all of
# this automatically when a tool call finds the server down.
import importlib
import os
import sys

if "__file__" not in globals():
    raise RuntimeError('start_mcp_plus.py needs its path: exec(open(p).read(), {"__file__": p})')

_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

import cascadeur_mcp_plus  # noqa: E402, F401  (imported so it can be reloaded below)

# Reload in dependency order so a restart always runs the code currently on disk.
for _name in (
    "cascadeur_mcp_plus.version",
    "cascadeur_mcp_plus.ui",
    "cascadeur_mcp_plus.scene_tools",
    "cascadeur_mcp_plus.server",
    "cascadeur_mcp_plus",
):
    importlib.reload(sys.modules[_name])

_port = globals().get("MCP_PLUS_PORT")
_package = sys.modules["cascadeur_mcp_plus"]
print(_package.start(int(_port)) if _port else _package.start())
