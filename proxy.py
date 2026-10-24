"""Launcher for the stdio MCP proxy; the implementation lives in cascadeur_mcp_plus_cli/proxy.py.

    python -I proxy.py

The installed wheel has no top-level "proxy" module (that name collides with the "proxy.py"
distribution), so this file only exists in a source checkout. It loads the implementation by
path, which also keeps the documented "python -I proxy.py" usage working: isolated mode does
not add the script's directory to sys.path.
"""

from __future__ import annotations

import importlib.util
import os


def main() -> None:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cascadeur_mcp_plus_cli", "proxy.py")
    if not os.path.isfile(path):
        raise SystemExit(f"proxy.py: cannot find the implementation at {path}")
    spec = importlib.util.spec_from_file_location("_cascadeur_mcp_plus_proxy", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"proxy.py: cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()


if __name__ == "__main__":
    main()
