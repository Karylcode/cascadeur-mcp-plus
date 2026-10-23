"""Character structure and control-point aliases (T12).

list_characters explains which characters the scene has; list_controllers lists each control
point with a stable alias, kind, world position and AutoPosing lock state. The matching logic
lives in cascadeur_mcp_plus/rig.py so later tickets can resolve object arguments the same way
(rig.resolve) and so it can be unit tested without Cascadeur.
"""

from __future__ import annotations

import traceback

from .. import common, rig
from ._schema import schema


def _unexpected(exc):
    return common.fail(
        f"Could not read the rig: {type(exc).__name__}: {exc}",
        hint="Open a rigged scene in Cascadeur and try again; get_log has the traceback.",
        traceback=traceback.format_exc(),
    )


def _kinds_filter(kinds):
    if kinds is None:
        return None
    if isinstance(kinds, str):
        kinds = [kinds]
    if not isinstance(kinds, (list, tuple)):
        raise ValueError("kinds must be a list of kind names.")
    wanted = []
    for raw in kinds:
        key = str(raw).strip().lower()
        if not key:
            continue
        if key not in rig.KINDS:
            raise ValueError(f"Unknown kind {raw!r}.")
        wanted.append(key)
    return set(wanted) or None


def list_characters() -> dict:
    """List the scene's characters (RigInfo/AnimationInfo roots)."""
    try:
        characters = rig.characters()
    except rig.RigError as exc:
        return exc.result()
    except Exception as exc:
        return _unexpected(exc)
    result = common.ok(count=len(characters), characters=common.jsonable(characters))
    if not characters:
        result["hint"] = (
            "No character has a RigInfo or AnimationInfo behaviour. Open a rigged scene "
            "(for example Cascadeur's Cascy.casc sample) or enter Rig mode with enter_rig_mode."
        )
    return result


def list_controllers(character: str | None = None, kinds=None) -> dict:
    """List control points with aliases, kinds, positions and AutoPosing lock state."""
    try:
        wanted = _kinds_filter(kinds)
    except ValueError as exc:
        return common.fail(
            str(exc),
            hint=f"Use any of: {', '.join(rig.KINDS)}.",
        )
    try:
        entries = rig.controllers(character=character)
    except rig.RigError as exc:
        return exc.result()
    except Exception as exc:
        return _unexpected(exc)

    if wanted:
        entries = [entry for entry in entries if entry["kind"] in wanted]
    controllers = [
        {
            "name": entry["name"],
            "alias": entry["alias"],
            "kind": entry["kind"],
            "type": entry["type"],
            "position": entry["position"],
            "autoposing_locked": entry["autoposing_locked"],
            "character": entry["character"],
        }
        for entry in entries
    ]
    result = common.ok(
        count=len(controllers),
        controllers=common.jsonable(controllers),
        characters=common.jsonable(sorted({entry["character"] for entry in entries if entry["character"]})),
    )
    if not controllers:
        result["hint"] = (
            "No controller matched. Call list_characters to see the scene's characters and try again "
            "without kinds, or with kinds such as main_point."
        )
    return result


TOOLS = [
    {
        "name": "list_characters",
        "description": "List the scene's characters (roots that have a RigInfo or AnimationInfo behaviour) with their "
        "root joint, mesh name, control-point count and joint count. Use it first when you need to know which character "
        "or rig the scene contains; pass a returned name (or root joint or mesh name) as the character argument of other "
        "tools. Read-only.",
        "inputSchema": schema(),
        "fn": list_characters,
    },
    {
        "name": "list_controllers",
        "description": "List a character's control points (main points, direction controllers, boxes, joints and other rig "
        "objects) with their alias, kind, world position and AutoPosing lock state. Use it to find which control point to "
        "move instead of guessing internal names such as mixamorig:LeftHand_MainPoint; aliases such as left_hand work as "
        "object arguments in every later tool. character is a name from list_characters (root joint and mesh names also "
        "work); kinds filters main_point, direction, box, joint or other. Positions are world coordinates in cm at the "
        "current frame; autoposing_locked is null when Cascadeur does not expose the lock.",
        "inputSchema": schema(
            {
                "character": {
                    "type": "string",
                    "description": "Character name from list_characters (root joint or mesh name also work).",
                },
                "kinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(rig.KINDS)},
                    "description": "Only return these kinds; omit for all of them.",
                },
            }
        ),
        "fn": list_controllers,
    },
]
