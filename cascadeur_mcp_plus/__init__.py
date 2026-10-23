"""Cascadeur MCP Plus: an extended MCP server that runs inside Cascadeur.

Adds scene inspection, screenshots and full Qt Quick UI control (inspect, click,
type, shortcuts, dialogs, actions) on top of arbitrary Python execution.
"""

from .server import DEFAULT_PORT, VERSION, start, status, stop

__all__ = ["DEFAULT_PORT", "VERSION", "start", "status", "stop"]
