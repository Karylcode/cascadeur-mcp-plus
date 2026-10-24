"""Version and compatibility status (T54).

get_status reports the MCP Plus and Cascadeur versions, the tested Cascadeur version, every tool
this build had to disable with what is missing, and the behaviours that were measured on the
tested version. The server audits the tools once at startup; force=true runs the audit again, which
is how a temporary monkeypatch shows up as an unavailable tool and how restoring it clears it.
"""

from __future__ import annotations

from .. import compat
from ._schema import schema


def get_status(force: bool = False) -> dict:
    """Return the compatibility report, auditing the tools again when force is true."""
    if force or compat.report() is None:
        try:
            compat.refresh()
        except Exception as exc:  # noqa: BLE001 - report a broken audit instead of hiding it
            report = compat.status()
            report["ok"] = False
            report["error"] = f"The compatibility audit failed: {type(exc).__name__}: {exc}"
            report["hint"] = "Check get_log for the traceback; the tools still run, but unavailability is unknown."
            return report
    return compat.status()


TOOLS = [
    {
        "name": "get_status",
        "description": (
            "Report MCP Plus and Cascadeur versions, the tested Cascadeur version, and every tool that is "
            "unavailable in this build with the API or action key it is missing. Use it first after a Cascadeur "
            "update, or when a tool reports that it is unavailable: it also lists the behaviours measured on the "
            "tested version (video export window, AutoPosing lock, layer API, motion capture) that need "
            "re-checking on a different one. force=true reruns the audit, which also detects or clears a "
            "temporary monkeypatch. Returns versions, counts, unavailable tools, the action-key sources and the "
            "version-dependent behaviours."
        ),
        "inputSchema": schema(
            {
                "force": {
                    "type": "boolean",
                    "description": "Run the audit again instead of using the report from startup (default false).",
                }
            }
        ),
        "requires": [],
        "fn": get_status,
    },
]
