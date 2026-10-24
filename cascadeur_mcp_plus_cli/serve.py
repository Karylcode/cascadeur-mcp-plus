"""Run the stdio proxy for the serve subcommand, from a checkout or an installed wheel."""

from __future__ import annotations

import os

from . import proxy


def proxy_path() -> str:
    """The proxy module: cascadeur_mcp_plus_cli/proxy.py (never a top-level "proxy" module)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy.py")


def run() -> int:
    proxy.main()
    return 0
