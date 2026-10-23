"""Run proxy.py for the serve subcommand, from a source checkout or an installed wheel."""

from __future__ import annotations

import importlib.util
import os
import sys

from . import ROOT


def proxy_path() -> str:
    """proxy.py sits next to the packages in both a checkout and an installed wheel."""
    return os.path.join(ROOT, "proxy.py")


def load_proxy():
    """Import proxy.py by path so a same-named module cannot shadow it."""
    path = proxy_path()
    if not os.path.isfile(path):
        raise FileNotFoundError(f"proxy.py is not next to the package (expected {path}); reinstall cascadeur-mcp-plus")
    spec = importlib.util.spec_from_file_location("proxy", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["proxy"] = module
    spec.loader.exec_module(module)
    return module


def run() -> int:
    try:
        module = load_proxy()
    except (OSError, ImportError) as exc:
        sys.stderr.write(f"cascadeur-mcp-plus serve: {exc}\n")
        return 1
    module.main()
    return 0
