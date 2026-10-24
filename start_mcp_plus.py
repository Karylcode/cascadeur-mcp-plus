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
#
# This file is a loader only; the real logic is in cascadeur_mcp_plus/start.py. The installed
# wheel ships the same loader as cascadeur_mcp_plus_runtime/start_mcp_plus.py, so each layout
# adds exactly its own directory (the one that holds cascadeur_mcp_plus/) to sys.path and never
# all of site-packages.

import importlib.util
import os

if "__file__" not in globals():
    raise RuntimeError('start_mcp_plus.py needs its path: exec(open(p).read(), {"__file__": p})')

_here = os.path.dirname(os.path.abspath(__file__))
_loader = os.path.join(_here, "cascadeur_mcp_plus", "start.py")
_spec = importlib.util.spec_from_file_location("_cascadeur_mcp_plus_start", _loader)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot load {_loader}")
_start = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_start)
_start.run(_here, globals().get("MCP_PLUS_PORT"))
