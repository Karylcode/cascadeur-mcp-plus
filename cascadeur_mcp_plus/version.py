"""Version and default port, shared by the in-app server and the stdio proxy (no dependencies)."""

VERSION = "0.5.0"

# Uncommon on purpose: the obvious neighbours of Cascadeur's own 8765 (e.g. 8766) are often
# taken by other local tools.
DEFAULT_PORT = 48766
