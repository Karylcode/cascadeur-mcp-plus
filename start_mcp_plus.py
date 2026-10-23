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

# Stop the server that is running now, while its own modules are still importable.
if getattr(sys, "_cascadeur_mcp_plus_state", None):
    import cascadeur_mcp_plus as _running  # noqa: E402

    _running.stop()

# Drop every module of the package, so the import below comes from this checkout or worktree
# instead of a previous one still cached in sys.modules.
for _name in [name for name in sys.modules if name == "cascadeur_mcp_plus" or name.startswith("cascadeur_mcp_plus.")]:
    del sys.modules[_name]

# Move _root to the front even when it is already on sys.path somewhere else.
if _root in sys.path:
    sys.path.remove(_root)
sys.path.insert(0, _root)
importlib.invalidate_caches()

import cascadeur_mcp_plus  # noqa: E402  (imported after fixing sys.path)

_port = globals().get("MCP_PLUS_PORT")
print(cascadeur_mcp_plus.start(int(_port)) if _port else cascadeur_mcp_plus.start())
