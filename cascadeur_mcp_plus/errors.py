"""Exceptions shared by the server, the dispatcher and the tool modules."""

from __future__ import annotations


class ToolError(Exception):
    """A tool call failed for a reason the caller can act on (bad argument, missing object, ...)."""
