"""Character rig structure, control-point kinds and object name/alias resolution (T12).

Agents should not need to know internal names such as ``mixamorig:LeftHand_MainPoint``.
This module describes a scene's characters and controls, gives the main control points short
aliases (``left_hand``, ``pelvis``, ...), and resolves an object argument by exact name, alias
or partial name.

The matching and classification helpers at the top of the module are pure Python: they do not
import ``csc`` and are unit tested outside Cascadeur (see tests/test_rig_aliases.py). The
scene-facing helpers at the bottom import cascadeur_mcp_plus.common lazily and must run on
Cascadeur's GUI thread.

Naming styles covered (all seen in Cascadeur's bundled samples or standard rigs):

* Cascadeur standard: ``pelvis_MainPoint``, ``hand_MainPoint_l``, ``calf_LimbDir_l``
* Mixamo / namespaced: ``mixamorig:LeftHand_MainPoint``, ``_1:LeftHand_MainPoint``
* UE4 Mannequin: ``spine_02_MainPoint``, ``lowerarm_MainPoint_l``, ``ball_MainPoint_r``
* Roblox R15: ``LowerTorso_MainPoint``, ``LeftHand_MainPoint``

Aliases take priority over an exact object name: in Cascy the joint and the control point are
named ``pelvis`` and ``pelvis_MainPoint``, and ``resolve(["pelvis"])`` returns the main control
point. Pass the full object name (or an ObjectId) to target a differently named object.
"""

from __future__ import annotations

import difflib
import re

KINDS = ("main_point", "direction", "box", "joint", "other")

ALIASES = (
    "pelvis",
    "chest",
    "head",
    "neck",
    "left_hand",
    "right_hand",
    "left_elbow",
    "right_elbow",
    "left_shoulder",
    "right_shoulder",
    "left_foot",
    "right_foot",
    "left_knee",
    "right_knee",
    "left_toe",
    "right_toe",
    "center_of_mass",
)

CONTROLLER_TYPES = ("Point", "Box", "Limb Direction", "Joint", "Center of Mass")

_SIDED_PARTS = ("hand", "elbow", "shoulder", "foot", "knee", "toe")
_UNSIDED_PARTS = ("pelvis", "chest", "head", "neck")
_SIDE_WORDS = {"left": "l", "right": "r", "l": "l", "r": "r", "lt": "l", "rt": "r"}
_FINGER_WORDS = {"thumb", "index", "middle", "ring", "pinky", "finger", "fingers"}
_MARKER_WORDS = {
    "main": "main_point",
    "direction": "direction",
    "additional": "other",
    "self": "other",
    "limb": "direction",
}


# ---------------------------------------------------------------------------
# Name tokenisation and alias matching (pure: no Cascadeur import)
# ---------------------------------------------------------------------------


def _find_marker(tokens):
    """Return (index, marker kind) of the first control-point marker in tokens."""
    for index, token in enumerate(tokens):
        if token in ("mainpoint", "directionpoint", "additionalpoint"):
            return index, _MARKER_WORDS[token.replace("point", "")]
        if token in ("main", "direction", "additional") and index + 1 < len(tokens) and tokens[index + 1] == "point":
            return index, _MARKER_WORDS[token]
        if token == "self" and any(item == "point" for item in tokens[index + 1 : index + 3]):
            return index, "other"
        if token == "limb" and index + 1 < len(tokens) and tokens[index + 1] in ("dir", "direction"):
            return index, "direction"
    return len(tokens), None


def _pop_side(tokens):
    """Remove a leading or trailing side word; return (tokens, "l" | "r" | None)."""
    if tokens and tokens[0] in _SIDE_WORDS:
        return tokens[1:], _SIDE_WORDS[tokens[0]]
    if tokens and tokens[-1] in _SIDE_WORDS:
        return tokens[:-1], _SIDE_WORDS[tokens[-1]]
    return tokens, None


def _tokenize(name):
    """Lowercase tokens of an object name, keeping markers and the side word."""
    text = str(name or "")
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    if "|" in text:
        text = text.rsplit("|", 1)[-1]
    text = re.sub(r"[.]([lrLR])$", lambda match: " " + match.group(1), text)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", text)
    text = re.sub(r"(?<=[0-9])(?=[A-Za-z])", " ", text)
    text = re.sub(r"(?i)(upper|lower)(arm|leg|torso)", lambda match: f"{match.group(1)} {match.group(2)}", text)
    text = re.sub(r"(?i)toebase", "toe base", text)
    return [item for item in re.split(r"[^A-Za-z0-9]+", text.lower()) if item]


def _marker_kind(name):
    """The control-point marker in a name (main_point, direction, other) or None."""
    _index, marker = _find_marker(_tokenize(name))
    return marker


def normalize_name(name):
    """Split an object name into (body-part tokens, side).

    The namespace prefix (``mixamorig:``, ``_1:``, ``Armature|``), CamelCase, digits,
    separators, control-point markers (``MainPoint``, ``DirectionPoint``, ``LimbDir``, ...)
    and the side word (``left_``, ``_r``, ``.L``) are removed, so ``mixamorig:LeftHand_MainPoint``
    and ``hand_MainPoint_l`` both become (("hand",), "l").
    """
    tokens = _tokenize(name)
    tokens, side = _pop_side(tokens)
    index, _marker = _find_marker(tokens)
    tokens = tokens[:index]
    tokens, inner_side = _pop_side(tokens)
    return tuple(tokens), side or inner_side


def _number(tokens):
    for token in reversed(tokens):
        if token.isdigit():
            return int(token)
    return 0


def _sequence(tokens, *words):
    size = len(words)
    return any(tuple(tokens[index : index + size]) == words for index in range(len(tokens) - size + 1))


def _part_scores(tokens):
    """Score which body part a name refers to; empty when no required alias applies."""
    if not tokens or _FINGER_WORDS.intersection(tokens):
        return {}
    scores = {}
    if "hand" in tokens or "wrist" in tokens:
        scores["hand"] = 100 if "hand" in tokens else 90
    if "forearm" in tokens or _sequence(tokens, "fore", "arm"):
        scores["elbow"] = 100
    elif "elbow" in tokens:
        scores["elbow"] = 95
    elif _sequence(tokens, "lower", "arm"):
        scores["elbow"] = 90
    if "clavicle" in tokens:
        scores["shoulder"] = 100
    elif "collar" in tokens:
        scores["shoulder"] = 95
    elif "shoulder" in tokens:
        scores["shoulder"] = 90
    elif "forearm" not in tokens and "fore" not in tokens and not _sequence(tokens, "lower", "arm"):
        if "arm" in tokens:
            scores["shoulder"] = 50
        elif _sequence(tokens, "upper", "arm"):
            scores["shoulder"] = 55
    if "foot" in tokens:
        scores["foot"] = 100
    elif "ankle" in tokens:
        scores["foot"] = 90
    if "calf" in tokens:
        scores["knee"] = 100
    elif "knee" in tokens:
        scores["knee"] = 95
    elif _sequence(tokens, "lower", "leg") or "shin" in tokens:
        scores["knee"] = 90
    elif "leg" in tokens and not ({"up", "upper", "thigh"} & set(tokens)):
        scores["knee"] = 40
    if "toe" in tokens:
        scores["toe"] = 100
    elif "ball" in tokens:
        scores["toe"] = 90
    if "pelvis" in tokens or "hip" in tokens or "hips" in tokens:
        scores["pelvis"] = 100
    elif _sequence(tokens, "lower", "torso"):
        scores["pelvis"] = 100
    elif "torso" in tokens:
        scores["pelvis"] = 40
        scores["chest"] = 40
    if "chest" in tokens:
        scores["chest"] = 100
    elif _sequence(tokens, "upper", "torso"):
        scores["chest"] = 95
    elif "spine" in tokens:
        scores["chest"] = 60 + min(_number(tokens), 9)
    if "head" in tokens:
        scores["head"] = 100
    elif "skull" in tokens:
        scores["head"] = 90
    if "neck" in tokens:
        scores["neck"] = 100
    return scores


def part_scores(name):
    """Return {body part: confidence} for a name, for diagnostics and tests."""
    tokens, _side = normalize_name(name)
    return _part_scores(tokens)


def alias_map(names):
    """Return {object name: alias} for a character's main control points.

    Each alias goes to the single best matching object. A tie (two objects equally likely,
    for example Spine and Spine1 with no better chest candidate) leaves the alias unassigned
    instead of guessing.
    """
    scored = {}
    seen = set()
    for raw in names:
        name = str(raw)
        if not name or name in seen:
            continue
        seen.add(name)
        if _marker_kind(name) != "main_point":
            # Additional, direction and self points share the main point's body name (for
            # example foot_AdditionalPoint_l); they must not compete for its alias.
            continue
        tokens, side = normalize_name(name)
        scores = _part_scores(tokens)
        if not scores:
            continue
        top = max(scores.values())
        parts = [part for part, score in scores.items() if score == top]
        if len(parts) != 1:
            continue
        part = parts[0]
        if part in _SIDED_PARTS:
            if side not in ("l", "r"):
                continue
            alias = ("left_" if side == "l" else "right_") + part
        else:
            alias = part
        scored[name] = (alias, top)

    result = {}
    for alias in sorted({entry[0] for entry in scored.values()}):
        pairs = [(score, name) for name, (candidate, score) in scored.items() if candidate == alias]
        top = max(score for score, _name in pairs)
        winners = [name for score, name in pairs if score == top]
        if len(winners) == 1:
            result[winners[0]] = alias
    return result


def alias_query(query):
    """Return the alias a user argument names, or None (``left_hand``, ``pelvis``, ...).

    An explicit object name (``hand_MainPoint_l``, ``hand_Box_l``) is not an alias query: the
    marker and Box words are left to the exact/normalised name matching below.
    """
    raw = _tokenize(query)
    if "box" in raw or "point" in raw or _find_marker(raw)[1] is not None:
        return None
    tokens, side = normalize_name(query)
    joined = "".join(tokens)
    if joined in ("centerofmass", "centerofgravity", "com"):
        return "center_of_mass"
    if len(tokens) == 1:
        part = tokens[0]
        if part in _UNSIDED_PARTS and side is None:
            return part
        if part in _SIDED_PARTS and side in ("l", "r"):
            return ("left_" if side == "l" else "right_") + part
    return None


def name_kind(name, type_name=""):
    """Fallback control-point kind from the object name and its cascadeur type name."""
    type_name = str(type_name or "")
    if type_name == "Joint":
        return "joint"
    if type_name == "Box":
        return "box"
    if type_name == "Limb Direction":
        return "direction"
    text = str(name or "")
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    if "|" in text:
        text = text.rsplit("|", 1)[-1]
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", text)
    text = re.sub(r"(?<=[0-9])(?=[A-Za-z])", " ", text)
    tokens = [item for item in re.split(r"[^A-Za-z0-9]+", text.lower()) if item]
    _index, marker = _find_marker(tokens)
    if marker:
        return marker
    return "other"


def _key_matches(name, query_tokens, query_side, query_marker=None, query_box=False):
    raw = _tokenize(name)
    if query_marker is not None and _find_marker(raw)[1] != query_marker:
        return False
    if query_box and "box" not in raw:
        return False
    tokens, side = normalize_name(name)
    if tokens != query_tokens:
        return False
    return query_side is None or side == query_side


def match_controllers(query, controllers):
    """Return the controllers a user argument matches, best match kind first.

    ``controllers`` is a sequence of dicts with at least ``name`` and ``kind`` and optionally
    ``alias``. Matching order: alias, exact name (case-sensitive then insensitive), the same
    normalised name (so ``hand_l`` finds ``hand_MainPoint_l``), then a substring. When one match
    kind finds main control points, only main points are returned: a partial name such as
    ``hand`` lists the left and right main points, not every joint, box and direction point
    with "hand" in its name.
    """
    text = str(query or "").strip()
    if not text:
        return []

    alias = alias_query(text)
    if alias:
        hits = [item for item in controllers if item.get("alias") == alias]
        if hits:
            return hits

    hits = [item for item in controllers if item.get("name") == text]
    if hits:
        return hits
    lowered = text.lower()
    hits = [item for item in controllers if str(item.get("name", "")).lower() == lowered]
    if hits:
        return hits

    tokens, side = normalize_name(text)
    if tokens:
        marker = _marker_kind(text)
        box = "box" in _tokenize(text)
        hits = [item for item in controllers if _key_matches(item.get("name", ""), tokens, side, marker, box)]
        if hits:
            return _prefer_main_points(hits)

    hits = [item for item in controllers if lowered in str(item.get("name", "")).lower()]
    if hits:
        return _prefer_main_points(hits)
    return []


def _prefer_main_points(hits):
    mains = [item for item in hits if item.get("kind") == "main_point"]
    return mains or hits


def suggest_names(query, names, limit=3):
    """Closest names for an error hint (empty list when nothing is close)."""
    return difflib.get_close_matches(str(query), [str(name) for name in names], n=limit, cutoff=0.5)


def candidate(item):
    """The public candidate shape used in ambiguity errors."""
    result = {"name": item.get("name")}
    for key in ("kind", "alias", "type", "character"):
        if item.get(key):
            result[key] = item[key]
    return result


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RigError(Exception):
    """A rig lookup failed in a way the caller can act on (unknown/ambiguous name)."""

    def __init__(self, message, hint=None, candidates=None, **extra):
        super().__init__(message)
        self.hint = hint
        self.candidates = list(candidates or [])
        self.extra = extra

    def result(self):
        """The {"ok": false, ...} tool result for this error."""
        data = {"ok": False, "error": str(self)}
        if self.hint:
            data["hint"] = self.hint
        if self.candidates:
            data["candidates"] = self.candidates
        data.update(self.extra)
        return data


class UnknownName(RigError):
    """No object matches the name or alias."""


class AmbiguousName(RigError):
    """Several objects match; the caller must pick one."""


class UnknownCharacter(RigError):
    """No character matches the character argument."""


class AmbiguousCharacter(RigError):
    """Several characters match the character argument."""


# ---------------------------------------------------------------------------
# Scene helpers (Cascadeur GUI thread; Cascadeur is imported lazily)
# ---------------------------------------------------------------------------


def _dedupe(values):
    result = []
    seen = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _object_range(behaviour_viewer, behaviour_id, name):
    try:
        return [item for item in behaviour_viewer.get_behaviour_objects_range(behaviour_id, name) if not item.is_null()]
    except Exception:
        return []


def _parent_object(behaviour_viewer, object_id):
    try:
        basic = behaviour_viewer.get_behaviour_by_name(object_id, "Basic")
        if basic.is_null():
            return None
        parent = behaviour_viewer.get_behaviour_object(basic, "parent")
        return None if parent.is_null() else parent
    except Exception:
        return None


def _top_joint(model_viewer, behaviour_viewer, joints):
    joint_set = set(joints)
    for joint in joints:
        parent = _parent_object(behaviour_viewer, joint)
        if parent is None or parent not in joint_set:
            name = model_viewer.get_object_name(joint)
            if name:
                return name
    return None


def _character_name(model_viewer, behaviour_viewer, owner, joints):
    parent = _parent_object(behaviour_viewer, owner)
    group = model_viewer.get_object_name(parent) if parent is not None else ""
    root_joint = _top_joint(model_viewer, behaviour_viewer, joints)
    owner_name = model_viewer.get_object_name(owner)
    return group or root_joint or owner_name or "character", root_joint


def _character_from_behaviour(model_viewer, behaviour_viewer, behaviour_id, mode):
    owner = behaviour_viewer.get_behaviour_owner(behaviour_id)
    rig_objects = _object_range(behaviour_viewer, behaviour_id, "rig_objects")
    joints = _object_range(behaviour_viewer, behaviour_id, "related_joints")
    related = _object_range(behaviour_viewer, behaviour_id, "related_objects")
    objects = _dedupe(list(rig_objects) + list(joints) + list(related))
    name, root_joint = _character_name(model_viewer, behaviour_viewer, owner, joints)
    meshes = []
    for object_id in _dedupe(list(related) + list(rig_objects)):
        try:
            if model_viewer.get_object_type_name(object_id) == "Mesh Object":
                mesh_name = model_viewer.get_object_name(object_id)
                if mesh_name:
                    meshes.append(mesh_name)
        except Exception:
            pass
    if not meshes:
        # The bundled samples keep the mesh outside RigInfo.related_objects; with a single mesh
        # in the scene there is no ambiguity about which character it belongs to.
        scene_meshes = []
        for object_id in model_viewer.get_objects():
            try:
                if model_viewer.get_object_type_name(object_id) == "Mesh Object":
                    scene_meshes.append(model_viewer.get_object_name(object_id))
            except Exception:
                pass
        if len(scene_meshes) == 1 and scene_meshes[0]:
            meshes.append(scene_meshes[0])
    return {
        "name": name,
        "root_joint": root_joint,
        "info": model_viewer.get_object_name(owner),
        "mode": mode,
        "meshes": meshes,
        "objects": objects,
        "joint_ids": list(joints),
    }


def _unique_character_names(characters):
    used = {}
    for character in characters:
        base = character["name"]
        count = used.get(base, 0) + 1
        used[base] = count
        if count > 1:
            character["name"] = f"{base} #{count}"
    return characters


def _characters(scene, model_viewer=None, behaviour_viewer=None):
    """Return the scene's characters (RigInfo roots, or AnimationInfo when there is no rig)."""
    model_viewer = model_viewer or scene.model_viewer()
    behaviour_viewer = behaviour_viewer or model_viewer.behaviour_viewer()
    characters = []
    for behaviour_id in behaviour_viewer.get_behaviours("RigInfo"):
        characters.append(_character_from_behaviour(model_viewer, behaviour_viewer, behaviour_id, "rig"))
    if not characters:
        for behaviour_id in behaviour_viewer.get_behaviours("AnimationInfo"):
            characters.append(_character_from_behaviour(model_viewer, behaviour_viewer, behaviour_id, "animation"))
    return _unique_character_names(characters)


def characters():
    """Return the scene's characters with controller/joint counts (GUI thread)."""
    from . import common

    _app, _view_scene, scene = common.scenes()
    model_viewer = scene.model_viewer()
    behaviour_viewer = model_viewer.behaviour_viewer()
    result = []
    for character in _characters(scene, model_viewer, behaviour_viewer):
        counts = _count_types(model_viewer, character["objects"] or model_viewer.get_objects())
        result.append(
            {
                "name": character["name"],
                "mode": character["mode"],
                "root_joint": character["root_joint"],
                "info": character["info"],
                "meshes": character["meshes"],
                "controllers": sum(count for type_name, count in counts.items() if type_name != "Joint"),
                "joints": counts.get("Joint", 0),
            }
        )
    return result


def _count_types(model_viewer, object_ids):
    counts = {}
    if not object_ids:
        return counts
    for object_id in object_ids:
        try:
            type_name = model_viewer.get_object_type_name(object_id)
        except Exception:
            continue
        if type_name in CONTROLLER_TYPES:
            counts[type_name] = counts.get(type_name, 0) + 1
    return counts


def _scene_controller_ids(model_viewer, object_ids=None):
    """Controller-type object ids, either from a character or from the whole scene."""
    source = object_ids if object_ids else model_viewer.get_objects()
    result = []
    for object_id in source:
        try:
            type_name = model_viewer.get_object_type_name(object_id)
        except Exception:
            continue
        if type_name in CONTROLLER_TYPES:
            result.append(object_id)
    return result


def _global_position(behaviour_viewer, data_viewer, object_id, frame):
    try:
        import numpy as np

        transform = behaviour_viewer.get_behaviour_by_name(object_id, "Transform")
        if transform.is_null():
            return None
        data_id = behaviour_viewer.get_behaviour_data(transform, "global_position")
        values = np.asarray(data_viewer.get_data_value(data_id, frame), dtype=float).reshape(-1)
        return [float(value) for value in values[:3]]
    except Exception:
        return None


def _autoposing_locked(_behaviour_viewer, _data_viewer, _object_id):
    """Return the per-point AutoPosing lock, when this Cascadeur version exposes it.

    Cascadeur 2026.2.3 has the "Lock selected controllers" / "Adjust AutoPosing lock state"
    actions, but no Python query for the lock: AutoPosingTool's editor only exposes activate,
    add, deactivate and update, and the lock lives in the tool's private data. ToolFixation's
    tool_fixed setting is a different (physics/tool fixation) flag - it stays false on points
    whose AutoPosing data records lock frames - so reporting it as autoposing_locked would be
    wrong. None means "not available in this version".
    """
    return None


def _controllers_of(model_viewer, behaviour_viewer, data_viewer, character, frame):
    objects = _scene_controller_ids(model_viewer, character["objects"])

    kind_by_id = {}
    names = {}
    for object_id in objects:
        type_name = model_viewer.get_object_type_name(object_id)
        try:
            object_name = model_viewer.get_object_name(object_id)
        except Exception:
            object_name = ""
        names[object_id] = object_name
        if type_name == "Joint":
            kind = "joint"
        elif type_name == "Box":
            kind = "box"
        elif type_name == "Limb Direction":
            kind = "direction"
        elif type_name == "Center of Mass":
            kind = "other"
        elif type_name == "Point":
            # Names are authoritative here: Cascadeur's StaticPointsTypes.get_main_points()
            # returns every point that can be associated with the centre of mass (all 43 Point
            # objects in Cascy: main, additional, direction and self points), while the
            # interface's main points are the 21 *_MainPoint controllers.
            kind = name_kind(object_name, type_name)
        else:
            kind = "other"
        kind_by_id[object_id] = kind

    alias_by_id = {}
    main_names = [names[object_id] for object_id in objects if kind_by_id[object_id] == "main_point"]
    for object_name, alias in alias_map(main_names).items():
        for object_id, name in names.items():
            if name == object_name and kind_by_id[object_id] == "main_point":
                alias_by_id[object_id] = alias
                break
    centers = [object_id for object_id in objects if _object_type(model_viewer, object_id) == "Center of Mass"]
    if len(centers) == 1:
        alias_by_id[centers[0]] = "center_of_mass"

    result = []
    for object_id in objects:
        type_name = _object_type(model_viewer, object_id)
        result.append(
            {
                "id": object_id,
                "name": names[object_id],
                "alias": alias_by_id.get(object_id),
                "kind": kind_by_id[object_id],
                "type": type_name,
                "position": _global_position(behaviour_viewer, data_viewer, object_id, frame),
                "autoposing_locked": _autoposing_locked(behaviour_viewer, data_viewer, object_id),
                "character": character.get("name"),
            }
        )
    return result


def _object_type(model_viewer, object_id):
    try:
        return model_viewer.get_object_type_name(object_id)
    except Exception:
        return ""


def _match_characters(query, characters):
    text = str(query or "").strip().lower()
    if not characters:
        raise UnknownCharacter(
            f"No character matches {query!r} because the scene has no RigInfo or AnimationInfo.",
            hint="Open a rigged scene (for example Cascadeur's Cascy.casc sample) and try again.",
        )

    def names_of(character):
        return [value for value in (character.get("name"), character.get("root_joint"), character.get("info")) if value]

    def meshes_of(character):
        return list(character.get("meshes") or [])

    exact = [
        character
        for character in characters
        if text in {value.lower() for value in names_of(character)}
        or text in {value.lower() for value in meshes_of(character)}
    ]
    if len(exact) == 1:
        return exact
    if len(exact) > 1:
        raise AmbiguousCharacter(
            f"Character {query!r} matches {len(exact)} characters.",
            hint="Pass a more specific character name (list_characters shows them).",
            candidates=[{"name": character["name"], "root_joint": character.get("root_joint")} for character in exact],
        )

    partial = [
        character
        for character in characters
        if any(text in value.lower() for value in names_of(character) + meshes_of(character))
    ]
    if len(partial) == 1:
        return partial
    if len(partial) > 1:
        raise AmbiguousCharacter(
            f"Character {query!r} matches {len(partial)} characters.",
            hint="Pass a more specific character name (list_characters shows them).",
            candidates=[
                {"name": character["name"], "root_joint": character.get("root_joint")} for character in partial
            ],
        )

    options = [character["name"] for character in characters]
    raise UnknownCharacter(
        f"Character not found: {query!r}.",
        hint="Pass one of the character names from list_characters (a root joint or mesh name also works).",
        candidates=[{"name": name} for name in options],
    )


def controllers(character=None):
    """Return every controller of the scene or of one character (GUI thread).

    Each item has id (ObjectId), name, alias, kind, type, position (cm), autoposing_locked and
    character. Raises RigError subclasses for unknown or ambiguous character names.
    """
    from . import common

    _app, _view_scene, scene = common.scenes()
    model_viewer = scene.model_viewer()
    behaviour_viewer = model_viewer.behaviour_viewer()
    data_viewer = model_viewer.data_viewer()
    frame = scene.get_current_frame()
    characters_in_scene = _characters(scene, model_viewer, behaviour_viewer)

    if character is not None:
        selected = _match_characters(character, characters_in_scene)
    else:
        selected = characters_in_scene

    entries = []
    if selected:
        for item in selected:
            entries.extend(_controllers_of(model_viewer, behaviour_viewer, data_viewer, item, frame))
    else:
        if character is not None:
            _match_characters(character, characters_in_scene)  # raises with a helpful message
        pseudo = {"name": None, "objects": [], "root_joint": None, "meshes": []}
        entries.extend(_controllers_of(model_viewer, behaviour_viewer, data_viewer, pseudo, frame))

    unique = []
    seen = set()
    for entry in entries:
        if entry["id"] not in seen:
            seen.add(entry["id"])
            unique.append(entry)
    return unique


def resolve(names, character=None):
    """Resolve object names, aliases or partial names to ObjectIds (GUI thread).

    names is one string or a sequence. The result keeps the request order and removes
    duplicates. An alias (left_hand, pelvis, ...) wins over an identically named joint.
    Raises AmbiguousName with the candidate list when a name matches several objects, and
    UnknownName with close names when nothing matches.
    """
    if isinstance(names, str):
        names = [names]
    wanted = [str(name) for name in names]
    available = controllers(character=character)
    by_name = [entry["name"] for entry in available]

    from . import common

    _app, _view_scene, scene = common.scenes()
    model_viewer = scene.model_viewer()

    result = []
    for name in wanted:
        matches = match_controllers(name, available)
        if len(matches) == 1:
            result.append(matches[0]["id"])
            continue
        if len(matches) > 1:
            shown = matches[:20]
            raise AmbiguousName(
                f"{name!r} matches {len(matches)} control points; pick one by its exact name.",
                hint="Use one of the candidate names, or pass character= to narrow the lookup.",
                candidates=[candidate(item) for item in shown],
                matched=len(matches),
            )

        # Any other scene object (mesh, camera, rigid body, ...) still resolves by exact name.
        try:
            extra = [item for item in model_viewer.get_objects(name) if not item.is_null()]
        except Exception:
            extra = []
        if len(extra) == 1:
            result.append(extra[0])
            continue
        if len(extra) > 1:
            candidates = [
                {
                    "name": model_viewer.get_object_name(item),
                    "type": model_viewer.get_object_type_name(item),
                }
                for item in extra[:20]
            ]
            raise AmbiguousName(
                f"{name!r} matches {len(extra)} scene objects; pick one by its exact name.",
                hint="Pass a unique object name or an alias from list_controllers.",
                candidates=candidates,
                matched=len(extra),
            )

        closest = suggest_names(name, by_name)
        suggestion = ", ".join(repr(item) for item in closest) if closest else "none"
        raise UnknownName(
            f"Object or alias not found: {name!r} (closest: {suggestion}).",
            hint="Call list_controllers to see the control point names and aliases.",
            closest=closest,
        )
    return _dedupe(result)
