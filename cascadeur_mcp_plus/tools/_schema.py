"""Input-schema helpers shared by the tool modules (not a tool module: leading underscore)."""

from __future__ import annotations


def schema(properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties or {}, "required": required or []}


WINDOW = {
    "type": ["integer", "string"],
    "description": "Window index or title substring (default: focused/main window).",
}
REF = {"type": "string", "description": "Element ref from the latest ui_tree or dialogs result."}
QUERY = {"type": "string", "description": "Text, tooltip, actionId or objectName to find an on-screen element."}
SETTLE = {
    "type": "integer",
    "description": "Milliseconds to wait for the UI to update before returning (default 150).",
}
