"""Command line for Cascadeur MCP Plus: `serve`, `setup`, `doctor`, `uninstall`.

Like proxy.py this package runs under a normal Python outside Cascadeur, so it uses the
standard library only. Paths are always derived from where the package is installed:

    <site-packages>/cascadeur_mcp_plus_cli/__init__.py   this file
    <site-packages>/proxy.py                             the stdio proxy itself
    <site-packages>/start_mcp_plus.py                    what Cascadeur executes to start the server
    <site-packages>/cascadeur_mcp_plus/                  the in-app server package
"""

from __future__ import annotations

import os

__all__ = ["ROOT", "version"]

# The directory that holds proxy.py, start_mcp_plus.py and cascadeur_mcp_plus/. In a source
# checkout that is the repository root; in an installed wheel it is site-packages.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def version() -> str:
    """The in-app package version, read without importing it (it needs Cascadeur's Python).

    version.py is a flat "VERSION = ..." file, so exec()ing it needs nothing installed.
    """
    path = os.path.join(ROOT, "cascadeur_mcp_plus", "version.py")
    scope: dict = {}
    try:
        with open(path, encoding="utf-8") as handle:
            exec(handle.read(), scope)
    except OSError:
        return "unknown"
    return str(scope.get("VERSION", "unknown"))
