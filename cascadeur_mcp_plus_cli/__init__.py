"""Command line for Cascadeur MCP Plus: serve, setup, doctor and uninstall.

Standard library only. The installed layout is:

    <site-packages>/cascadeur_mcp_plus_cli/          this package (proxy.py and the CLI)
    <site-packages>/cascadeur_mcp_plus_runtime/      what Cascadeur executes
        start_mcp_plus.py
        cascadeur_mcp_plus/                          the in-app server package

Only the runtime directory is ever added to Cascadeur's sys.path, so the host Python's
site-packages (compiled packages built for another Python version) cannot shadow Cascadeur's
own modules. A source checkout keeps proxy.py, start_mcp_plus.py and cascadeur_mcp_plus/ at
the repository root instead.
"""

from __future__ import annotations

import os

__all__ = ["PACKAGE_ROOT", "ROOT", "runtime_dir", "version"]

# Parent of this package: site-packages in an installed wheel, the repository root in a
# source checkout. ROOT is kept as an alias for the code that only needs "where the package
# lives"; runtime_dir() is the directory that holds the files Cascadeur executes.
PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = PACKAGE_ROOT


def runtime_dir() -> str | None:
    """Return the directory holding start_mcp_plus.py and cascadeur_mcp_plus/, or None.

    Installed wheel: <site-packages>/cascadeur_mcp_plus_runtime. Source checkout: the
    repository root. The directory must contain both, so a partially installed package is
    reported instead of silently starting from the wrong place.
    """
    for candidate in (os.path.join(PACKAGE_ROOT, "cascadeur_mcp_plus_runtime"), PACKAGE_ROOT):
        if os.path.isfile(os.path.join(candidate, "start_mcp_plus.py")) and os.path.isfile(
            os.path.join(candidate, "cascadeur_mcp_plus", "__init__.py")
        ):
            return candidate
    return None


def version() -> str:
    """The in-app package version, read without importing it (it needs Cascadeur's Python).

    version.py is a flat "VERSION = ..." file, so exec()ing it needs nothing installed. A
    broken checkout falls back to the installed distribution's metadata.
    """
    runtime = runtime_dir()
    scope: dict = {}
    if runtime:
        path = os.path.join(runtime, "cascadeur_mcp_plus", "version.py")
        try:
            with open(path, encoding="utf-8-sig") as handle:
                exec(handle.read(), scope)
        except OSError:
            scope = {}
    if "VERSION" in scope:
        return str(scope["VERSION"])
    try:
        from importlib.metadata import PackageNotFoundError, version as installed_version
    except ImportError:  # pragma: no cover - importlib.metadata always exists on 3.11+
        return "unknown"
    try:
        return installed_version("cascadeur-mcp-plus")
    except PackageNotFoundError:
        return "unknown"
