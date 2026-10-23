"""python -m cascadeur_mcp_plus_cli: same as the cascadeur-mcp-plus console script."""

from __future__ import annotations

import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
