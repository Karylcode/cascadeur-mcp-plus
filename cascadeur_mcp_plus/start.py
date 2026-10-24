"""Start or restart Cascadeur MCP Plus inside Cascadeur.

Loaded by file path from start_mcp_plus.py (the repository root in a checkout, or the installed
cascadeur_mcp_plus_runtime/start_mcp_plus.py) before this package is imported. That order
matters: the server that is running now is stopped and its modules are removed from
sys.modules before the new copy is imported.

Only the directory that holds this package is added to sys.path. In an installed wheel that is
cascadeur_mcp_plus_runtime/, never all of site-packages, so compiled packages built for a
different Python version cannot shadow Cascadeur's own modules.
"""

from __future__ import annotations

import importlib
import os
import sys


def package_root(script_dir: str) -> str:
    """Return the directory holding cascadeur_mcp_plus/ for a start script in script_dir."""
    script_dir = os.path.abspath(script_dir)
    candidates = (script_dir, os.path.dirname(script_dir))
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, "cascadeur_mcp_plus", "__init__.py")):
            return candidate
    raise RuntimeError(
        "start_mcp_plus.py must sit in the same directory as cascadeur_mcp_plus/ "
        f"(checked {script_dir} and {os.path.dirname(script_dir)})"
    )


def run(script_dir: str, port: int | None = None) -> None:
    """Restart MCP Plus from the package next to script_dir and print start()'s result."""
    root = package_root(script_dir)

    # Stop the server that is running now, while its own modules are still importable.
    if getattr(sys, "_cascadeur_mcp_plus_state", None):
        import cascadeur_mcp_plus as running

        running.stop()

    # Drop every module of the package, so the import below comes from this checkout, worktree
    # or installed runtime instead of a previous one still cached in sys.modules.
    for name in [
        name for name in sys.modules if name == "cascadeur_mcp_plus" or name.startswith("cascadeur_mcp_plus.")
    ]:
        del sys.modules[name]

    # Move root to the front even when it is already on sys.path somewhere else.
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    importlib.invalidate_caches()

    import cascadeur_mcp_plus

    print(cascadeur_mcp_plus.start(int(port)) if port else cascadeur_mcp_plus.start())
