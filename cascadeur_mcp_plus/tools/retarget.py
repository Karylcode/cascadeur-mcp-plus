"""Retarget animation between two scene tabs with Cascadeur's own Copy/Paste actions (T28).

Cascadeur 2026.2.3 has no retargeting Python API. The only entry points are the Edit menu actions
"View.Retargeting_Copy" and "View.Retargeting_Paste": Copy reads the current scene's selected
timeline frames, Paste writes the global clipboard into the current scene. There are no arguments,
no bone mapping and no result value, so this module is a coordinator around two actions.

The tool runs as an HTTP handler. Every scene operation is a separate GUI job, with a short pause
between switching tabs and acting on them; the log is polled on the HTTP thread, never with a
nested event loop. Source and target must already be open (except a source .casc path, which is
opened once in a temporary tab and closed afterwards through scene_files). Both need exactly one
RigInfo; AnimationInfo (Rig mode) is not required. Pro/Studio is required by Cascadeur itself.

Cascadeur reports real failures (no frames, different model types, unsupported layout, missing
license) as "Retargeting: Error: ..." in its log rather than raising or returning a value, so the
action's log slice is checked before the tool reports success. The actions create their own native
undo command(s); this module deliberately does not wrap them in common.edit (T11 found that nesting
a modify around a native command is unsupported), so the returned result does not claim an MCP step.
"""

from __future__ import annotations

import json
import os
import re
import time
import traceback

from ..errors import ToolError

COPY_ACTION = "View.Retargeting_Copy"
PASTE_ACTION = "View.Retargeting_Paste"
COPY_MARKER = "Retargeting: Copied"
PASTE_MARKER = "Retargeting: Pasted"
ERROR_MARKER = "Retargeting: Error:"
LICENSE_MARKER = "Retargeting is not available in the basic and indie versions"

RETARGET_ERROR_MARKERS = (
    "no frames selected",
    "unexpected frames count",
    "session failed",
    "no rig info",
    "different model types",
    "layout not found",
    "retargeting.quadruped.fingers is not supported",
    LICENSE_MARKER,
)

UNSUPPORTED_SUFFIXES = (".fbx", ".glb", ".gltf", ".bvh", ".usd", ".usda", ".usdc", ".usdz")
GUI_TIMEOUT = 60.0
SCENE_SETTLE = 0.15  # the same pause scene_files' switch_scene gets from its settle flag
LOG_TIMEOUT = 2.0
LOG_POLL = 0.05
LOG_LINES = 200
_SAFE_DIALOG_LABELS = ("ok", "close", "cancel", "no", "don't save", "dont save", "discard")


class _ArgumentError(ToolError):
    """A bad argument or an unavailable scene; hint tells the caller what to do next."""

    def __init__(self, message: str, hint: str | None = None, **extra) -> None:
        super().__init__(message)
        self.hint = hint
        self.extra = extra


# ---------------------------------------------------------------------------
# Pure-ish helpers (scene objects are only accessed through small, fakeable methods)
# ---------------------------------------------------------------------------


def _common():
    from .. import common

    return common


def _dispatch():
    from .. import dispatch

    return dispatch


def _scene_files():
    from . import scene_files

    return scene_files


def _view_name(view) -> str:
    try:
        return str(view.name())
    except Exception:
        return ""


def _view_path(view) -> str:
    try:
        return str(view.get_path_name() or "")
    except Exception:
        return ""


def _same_path(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right))


def _same_scene(left, right) -> bool:
    if left is None or right is None:
        return False
    if left is right:
        return True
    try:
        if bool(left == right):
            return True
    except Exception:
        pass
    left_path, right_path = _view_path(left), _view_path(right)
    return bool(left_path) and _same_path(left_path, right_path)


def _path_query(query: str) -> bool:
    return os.path.isabs(query) or "/" in query or "\\" in query


def _scene_argument(value, role: str) -> str:
    """Validate a source/target scene name or .casc path, rejecting animation/model files."""
    if not isinstance(value, str) or not value.strip():
        raise _ArgumentError(
            f"{role} must be a non-empty scene name or .casc path.",
            hint=f"Pass the {role}'s tab name from list_open_scenes, or an absolute .casc path.",
        )
    query = value.strip()
    suffix = os.path.splitext(query)[1].lower()
    if suffix in UNSUPPORTED_SUFFIXES:
        hint = (
            "Only .casc scenes can be retargeted. Import an FBX animation into a same-skeleton source scene "
            "(T11 import_animation) and then retarget that scene."
            if suffix == ".fbx"
            else "retarget_animation copies between open .casc scene tabs; import or export other formats separately."
        )
        raise _ArgumentError(f"retarget_animation does not accept {suffix!r} files ({value!r}).", hint=hint)
    if _path_query(query):
        if not os.path.isabs(query):
            raise _ArgumentError(
                f"The {role} path must be absolute (got {value!r}).",
                hint=f"Pass the exact tab name, or an absolute path such as C:/Users/me/{role}.casc.",
            )
        if suffix != ".casc":
            raise _ArgumentError(
                f"The {role} path must end with '.casc' (got {value!r}).",
                hint="Pass a Cascadeur .casc scene, not an animation or model file.",
            )
    return query


def _frame_number(value, name: str):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise _ArgumentError(
            f"{name} must be a whole frame number (got {value!r}).",
            hint="Omit start/end to use the source's whole animation range, or pass integer frames.",
        )
    return int(value)


def resolve_frame_range(first, last, start=None, end=None) -> tuple[int, int]:
    """Validate a source range against its animation boundary and fill in the defaults."""
    first, last = int(first), int(last)
    if last < first:
        raise _ArgumentError(
            f"The source has no animation range (first_frame={first}, last_frame={last}).",
            hint="Use an animated source scene such as Backflip_animation.casc.",
        )
    start = first if start is None else _frame_number(start, "start")
    end = last if end is None else _frame_number(end, "end")
    if start > end:
        raise _ArgumentError(
            f"start ({start}) must not be after end ({end}).",
            hint="Frames are inclusive; pass start <= end.",
        )
    if start < first or end > last:
        raise _ArgumentError(
            f"Frames {start}..{end} are outside the source animation range {first}..{last}.",
            hint=f"Pass start and end between {first} and {last}, or omit both to copy the whole animation.",
        )
    return start, end


def _scene_matches(view, query: str) -> bool:
    if _path_query(query):
        return _same_path(_view_path(view), query)
    return _view_name(view) == query


def _open_scenes_text(scenes) -> str:
    names = [repr(_view_name(view)) for view in scenes]
    return ", ".join(names) if names else "none"


def find_open_scene(scenes, query: str, role: str):
    """Return the one open view matching query, or None for a not-yet-open source path."""
    matches = [view for view in scenes if _scene_matches(view, query)]
    if len(matches) > 1:
        raise _ArgumentError(
            f"{len(matches)} open scene tabs match {query!r}.",
            hint="Rename one tab in Cascadeur, or pass a unique absolute path.",
            matches=[{"name": _view_name(view), "path": _view_path(view)} for view in matches],
        )
    if matches:
        return matches[0]
    if role == "source" and _path_query(query):
        return None
    raise _ArgumentError(
        f"No open {role} scene matches {query!r}.",
        hint=(
            f"Open the target scene first: its pasted keys live in that tab and are not saved to disk by this tool. "
            f"Open scenes: {_open_scenes_text(scenes)}."
            if role == "target"
            else f"Open scenes: {_open_scenes_text(scenes)}. Pass a tab name or an absolute .casc path."
        ),
    )


def _require_rig(view, role: str) -> dict:
    try:
        scene = view.domain_scene()
        behaviour_viewer = scene.model_viewer().behaviour_viewer()
        rig_info = (
            len(behaviour_viewer.get_behaviours("RigInfo"))
            if behaviour_viewer.is_valid_behaviour_type("RigInfo")
            else 0
        )
        animation_info = (
            len(behaviour_viewer.get_behaviours("AnimationInfo"))
            if behaviour_viewer.is_valid_behaviour_type("AnimationInfo")
            else 0
        )
    except Exception as exc:
        raise _ArgumentError(
            f"Could not read the {role} scene's behaviours: {exc}",
            hint="Check that the scene is open and Cascadeur is responsive; read get_log.",
        ) from exc
    if rig_info != 1:
        raise _ArgumentError(
            f"The {role} scene {_view_name(view)!r} has {rig_info} RigInfo behaviours; retargeting needs exactly one.",
            hint=(
                "Retargeting works on rigged animation scenes, not in Rig mode: open a rigged sample such as "
                "Cascy.casc or UE4_Mannequin.casc (RigInfo=1)."
                if animation_info == 1
                else "Give the character a rig first (T12's list_characters shows rigs; T29 Quick Rigging can create one)."
            ),
            source=_view_name(view),
            rig_info=rig_info,
            animation_info=animation_info,
        )
    return {"rig_info": rig_info, "animation_info": animation_info}


def _boundary(view) -> tuple[int, int]:
    try:
        boundary = view.animation_boundary()
        return int(boundary.first_frame), int(boundary.last_frame)
    except Exception as exc:
        raise _ArgumentError(
            f"Could not read {_view_name(view)!r}'s animation boundary: {exc}",
            hint="Check that the scene is a valid animated scene and read get_log.",
        ) from exc


def _last_key(scene):
    try:
        layers_viewer = scene.layers_viewer()
        positions = []
        for layer_id in layers_viewer.all_layer_ids():
            if layer_id.is_null():
                continue
            try:
                positions.append(int(layers_viewer.layer(layer_id).last_key_pos()))
            except Exception:
                continue
    except Exception:
        return None
    return max(positions) if positions else None


def scene_metrics(view) -> dict:
    """Animation size, keyed length, boundary and joint count for one open scene."""
    scene = view.domain_scene()
    model_viewer = scene.model_viewer()
    behaviour_viewer = model_viewer.behaviour_viewer()
    data_viewer = model_viewer.data_viewer()
    first, last = _boundary(view)
    try:
        size = int(data_viewer.get_animation_size())
    except Exception:
        size = None
    try:
        joints = len(behaviour_viewer.get_behaviours("Joint"))
    except Exception:
        joints = None
    return {
        "first_frame": first,
        "last_frame": last,
        "animation_size": size,
        "last_key": _last_key(scene),
        "joints": joints,
    }


def _select_frames(view, start: int, end: int) -> int:
    try:
        scene = view.domain_scene()
        selector = scene.get_layers_selector()
        layer_ids = list(selector.all_included_layer_ids())
        if not layer_ids:
            raise RuntimeError("the scene has no included animation layers")
        changed = selector.set_full_selection_by_parts(layer_ids, int(start), int(end))
    except Exception as exc:
        raise _ArgumentError(
            f"Could not select frames {start}..{end} in {_view_name(view)!r}: {exc}",
            hint="Check that the scene has animation layers and that a scene tab is active; read get_log.",
        ) from exc
    if changed is False:
        raise _ArgumentError(
            f"Cascadeur rejected the timeline selection {start}..{end} in {_view_name(view)!r}.",
            hint="Check that the scene has animation layers and try a range inside its animation.",
        )
    return len(layer_ids)


def _set_last_frame(view, end: int) -> tuple[int, int]:
    """Extend the target boundary when it ends before the pasted range."""
    try:
        boundary = view.animation_boundary()
        before = int(boundary.last_frame)
        if before < end:
            boundary.last_frame = int(end)
        after = int(boundary.last_frame)
    except Exception as exc:
        raise _ArgumentError(
            f"Could not extend {_view_name(view)!r}'s animation boundary to frame {end}: {exc}",
            hint="Check the scene's timeline and read get_log.",
        ) from exc
    if after < end:
        raise _ArgumentError(
            f"{_view_name(view)!r}'s last frame stayed at {after} after asking for {end}.",
            hint="Set the target scene's animation boundary by hand, then run retarget_animation again.",
        )
    return before, after


def _joint_ids(model_viewer, behaviour_viewer):
    result = []
    seen = set()
    for behaviour in behaviour_viewer.get_behaviours("Joint"):
        try:
            owner = behaviour_viewer.get_behaviour_owner(behaviour)
            if owner.is_null():
                continue
            name = model_viewer.get_object_name(owner)
        except Exception:
            continue
        if not name or owner in seen:
            continue
        seen.add(owner)
        result.append((owner, str(name)))
    return result


_FINGER_TOKENS = {"thumb", "index", "middle", "ring", "pinky", "finger", "fingers"}
_SIDE_TOKENS = {"left": "l", "right": "r", "l": "l", "r": "r", "lt": "l", "rt": "r"}


def _joint_tokens(name):
    """Lowercase tokens of a joint name, split on namespaces and CamelCase."""
    text = str(name or "")
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    if "|" in text:
        text = text.rsplit("|", 1)[-1]
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", text)
    text = re.sub(r"(?<=[0-9])(?=[A-Za-z])", " ", text)
    return [item for item in re.split(r"[^A-Za-z0-9]+", text.lower()) if item]


def _joint_side(tokens):
    if tokens and tokens[0] in _SIDE_TOKENS:
        return _SIDE_TOKENS[tokens[0]], tokens[1:]
    if tokens and tokens[-1] in _SIDE_TOKENS:
        return _SIDE_TOKENS[tokens[-1]], tokens[:-1]
    return None, tokens


def _best_joint(joints, part: str, side: str | None):
    candidates = []
    for object_id, name in joints:
        tokens = _joint_tokens(name)
        joint_side, body = _joint_side(tokens)
        if side is not None and joint_side != side:
            continue
        if _FINGER_TOKENS.intersection(body):
            continue
        if part == "pelvis":
            matches = "pelvis" in body or "hip" in body or "hips" in body
        elif part == "hand":
            matches = "hand" in body or "wrist" in body
        elif part == "foot":
            matches = ("foot" in body or "ankle" in body) and "toe" not in body
        else:
            matches = False
        if matches:
            candidates.append((0, -len(name), name, object_id))
    if not candidates:
        return None
    # Shortest name (pelvis beats pelvis_jnt), then alphabetical.
    candidates.sort(key=lambda item: (-item[1], item[2]))
    return candidates[0][3], candidates[0][2]


def _probe_joints(view):
    """Three representable joints (pelvis, right hand, left foot) for before/after checks."""
    scene = view.domain_scene()
    model_viewer = scene.model_viewer()
    behaviour_viewer = model_viewer.behaviour_viewer()
    joints = _joint_ids(model_viewer, behaviour_viewer)
    chosen = []
    seen = set()
    for part, side in (("pelvis", None), ("hand", "r"), ("foot", "l")):
        found = _best_joint(joints, part, side)
        if found is not None and found[0] not in seen:
            chosen.append(found)
            seen.add(found[0])
    for object_id, name in joints:
        if len(chosen) >= 3:
            break
        if object_id not in seen:
            chosen.append((object_id, name))
            seen.add(object_id)
    return chosen


def _sample_pose(common, view, joints, frames):
    """global_position and local_rotation for the probe joints at each frame."""
    scene = view.domain_scene()
    model_viewer = scene.model_viewer()
    behaviour_viewer = model_viewer.behaviour_viewer()
    data_viewer = model_viewer.data_viewer()
    samples = {}
    for object_id, name in joints:
        try:
            transform = behaviour_viewer.get_behaviour_by_name(object_id, "Transform")
            if transform.is_null():
                continue
        except Exception:
            continue
        fields = {}
        for field in ("global_position", "local_rotation"):
            try:
                data_id = behaviour_viewer.get_behaviour_data(transform, field)
                if data_id.is_null():
                    continue
            except Exception:
                continue
            per_frame = {}
            for frame in frames:
                try:
                    per_frame[str(frame)] = common.jsonable(data_viewer.get_data_value(data_id, int(frame)))
                except Exception as exc:
                    per_frame[str(frame)] = f"unavailable: {exc}"
            fields[field] = per_frame
        if fields:
            samples[name] = fields
    return samples


def pose_changes(before, after, joints):
    """Return (changed_joints, all_changed) by comparing sampled transforms exactly."""
    changed = []
    for _object_id, name in joints:
        old = before.get(name)
        new = after.get(name)
        if old is None or new is None:
            continue
        if json.dumps(old, sort_keys=True) != json.dumps(new, sort_keys=True):
            changed.append(name)
    return changed, bool(joints) and len(changed) == len(joints)


def _source_has_animation(metrics: dict, start: int, end: int) -> bool:
    """False when a multi-frame source range only contains a static pose.

    Cascadeur's Copy does not raise for such a scene (measured on Cascy.casc): it logs Copied and
    transfers the pose at the current frame, leaving the target's animation_size at 1. Check the
    source's size/last key before running the actions, otherwise the tool would report a 101-frame
    retarget of a still character.
    """
    if end <= start:
        return True
    size = metrics.get("animation_size")
    last_key = metrics.get("last_key")
    if size is None and last_key is None:
        return True
    if size is not None and int(size) > 1:
        return True
    return last_key is not None and int(last_key) > start


def _target_has_animation(metrics: dict, end: int) -> bool:
    """False when Paste left the target without data up to the requested end frame."""
    if end <= 0:
        return True
    size = metrics.get("animation_size")
    last_key = metrics.get("last_key")
    if size is not None and int(size) <= int(end):
        return False
    if last_key is not None and int(last_key) <= 0:
        return False
    return True


def sample_has_unavailable(samples) -> bool:
    """True when a sampled frame could not be read (outside the target's animation data)."""
    for joint in (samples or {}).values():
        for per_frame in joint.values():
            for value in per_frame.values():
                if isinstance(value, str) and value.startswith("unavailable:"):
                    return True
    return False


def retarget_event(lines, success_marker: str):
    """Return the newest Copy/Paste result from a log slice, or None."""
    for raw in reversed(list(lines or [])):
        text = str(raw)
        lowered = text.lower()
        index = lowered.find(ERROR_MARKER.lower())
        if index >= 0:
            message = text[index + len(ERROR_MARKER) :].strip()
            return {"ok": False, "line": text, "message": message or "Cascadeur reported a retargeting error."}
        if success_marker.lower() in lowered:
            return {"ok": True, "line": text, "message": success_marker}
    for raw in reversed(list(lines or [])):
        text = str(raw)
        lowered = text.lower()
        for marker in RETARGET_ERROR_MARKERS:
            if marker.lower() in lowered:
                return {"ok": False, "line": text, "message": text.strip()}
    return None


def error_hint(message: str, role: str = "source") -> str:
    text = str(message or "").lower()
    if "no frames selected" in text:
        return (
            "The source has no keyed animation in the selected range. Use an animated sample "
            "(Backflip_animation.casc), or check the source timeline and pass start/end around its keys."
        )
    if "no rig info" in text:
        return (
            "Both scenes need RigInfo: retargeting works on rigged scenes, not inside Rig mode. "
            "Open a rigged sample such as UE4_Mannequin.casc."
        )
    if "different model types" in text:
        return "Source and target must be the same model type (for example humanoid to humanoid, not humanoid to quadruped)."
    if "layout not found" in text:
        return "Cascadeur's retargeting model does not support this skeleton; try one of the bundled samples."
    if "quadruped.fingers" in text:
        return "Cascadeur does not retarget quadruped fingers; retarget the body chains only."
    if "session failed" in text or "unexpected frames count" in text:
        return "Cascadeur's retargeting session failed; read get_log, then retry with a smaller or aligned frame range."
    if "basic and indie" in text or "not available" in text:
        return "Retargeting requires a Pro or Studio license in Cascadeur; Basic/Indie cannot use these actions."
    return (
        f"Cascadeur rejected the {role} retargeting action. Check get_log, that both scenes have RigInfo and are "
        "the same model type, and that the selected frame range has keys."
    )


# ---------------------------------------------------------------------------
# Log, dialogs and actions (GUI thread only)
# ---------------------------------------------------------------------------


def _log_offset(common):
    try:
        return common._log_size()
    except Exception:
        return None


def _log_since(common, offset, lines: int = LOG_LINES):
    try:
        if offset is None:
            return list(common.log_tail(lines=lines))
        return list(common.log_tail(since=offset, lines=lines))
    except Exception:
        return []


def wait_for_retarget_event(common, offset, success_marker: str, timeout: float = LOG_TIMEOUT):
    """Poll Cascadeur's log on the HTTP thread until the action's marker appears."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    lines = []
    while True:
        lines = _log_since(common, offset)
        event = retarget_event(lines, success_marker)
        if event is not None or time.monotonic() >= deadline:
            return event, lines
        time.sleep(LOG_POLL)


def _actionable_dialogs():
    try:
        from .. import ui

        return [
            dialog
            for dialog in (ui.dialogs().get("dialogs") or [])
            if dialog.get("kind") == "dialog" and dialog.get("buttons")
        ]
    except Exception:
        return []


def _dialog_signature(dialog):
    """Stable fingerprint without refs/window numbers, so pre-existing dialogs stay known."""
    return json.dumps(
        {
            "kind": dialog.get("kind"),
            "type": dialog.get("type"),
            "texts": list(dialog.get("texts") or []),
            "buttons": [button.get("text") for button in dialog.get("buttons") or []],
        },
        sort_keys=True,
        default=str,
    )


def _dialog_signatures(dialogs):
    return {_dialog_signature(dialog) for dialog in dialogs or []}


def _dismiss_new_dialogs(known_signatures):
    """Dismiss new dialogs with a safe button; returns (new_dialogs, clicked_labels)."""
    from .. import ui

    new = [dialog for dialog in _actionable_dialogs() if _dialog_signature(dialog) not in known_signatures]
    clicked = []
    for dialog in new:
        for button in dialog.get("buttons") or []:
            label = str(button.get("text") or "").strip().lower()
            if not label or not any(safe in label for safe in _SAFE_DIALOG_LABELS):
                continue
            try:
                ui.click(ref=button["ref"])
            except Exception:
                continue
            clicked.append(button.get("text"))
            break
    return new, clicked


def _call_action(app, key: str) -> None:
    app.get_action_manager().call_action(key)


def _switch_to(manager, view) -> None:
    try:
        if not _same_scene(manager.current_scene(), view):
            manager.set_current_scene(view)
    except Exception as exc:
        raise _ArgumentError(
            f"Could not switch to {_view_name(view)!r}: {exc}",
            hint="Call list_open_scenes, close any modal dialog, and try again.",
        ) from exc


def _current(manager):
    try:
        return manager.current_scene()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# GUI stages (one small job each; the handler sleeps on the HTTP thread between them)
# ---------------------------------------------------------------------------


def _stage_prepare(source_arg: str, target_arg: str | None, start=None, end=None) -> dict:
    import csc

    app = csc.app.get_application()
    manager = app.get_scene_manager()
    original = manager.current_scene()
    if original is None:
        raise _ArgumentError(
            "There is no current scene to retarget.",
            hint="Open the target scene (and the source scene) before calling retarget_animation.",
        )
    try:
        pro_available = bool(app.is_pro_features_available())
    except Exception as exc:
        raise _ArgumentError(
            f"Could not check Cascadeur's license: {exc}",
            hint="Check the license in Cascadeur's title bar and read get_log.",
        ) from exc
    if not pro_available:
        raise _ArgumentError(
            "Retargeting is not available in the basic and indie versions. Please upgrade your license to use it.",
            hint=error_hint(LICENSE_MARKER),
        )

    scenes = list(manager.scenes())
    target_view = original if target_arg is None else find_open_scene(scenes, target_arg, "target")
    source_view = find_open_scene(scenes, source_arg, "source")
    opened = False
    if source_view is None:
        if not os.path.isfile(source_arg):
            raise _ArgumentError(
                f"Source scene file not found: {source_arg!r}.",
                hint="Check the absolute .casc path; use a tab name for an already-open scene.",
            )
        opened_result = _scene_files().open_scene(source_arg)
        if not opened_result.get("ok"):
            raise _ArgumentError(
                opened_result.get("error", f"Could not open the source scene {source_arg!r}."),
                hint=opened_result.get("hint") or "Open the source with open_scene and try again.",
            )
        opened = True
        try:
            source_view = manager.current_scene()
            if source_view is None or not _scene_matches(source_view, source_arg):
                raise _ArgumentError(
                    f"Could not find the temporary source tab for {source_arg!r} after open_scene.",
                    hint="Open the source scene by hand, then retry.",
                )
            if _same_scene(source_view, target_view):
                raise _ArgumentError(
                    "Source and target are the same scene tab; Interscene retargeting needs two different tabs.",
                    hint="Open the target character in another tab (open_scene) and pass its name as target.",
                )
        except Exception:
            # open_scene succeeded, so this tab is ours and must not leak when the next check fails.
            try:
                if source_view is not None:
                    if not _same_scene(_current(manager), original):
                        manager.set_current_scene(original)
                    _scene_files().close_scene(_view_name(source_view), discard_changes=True)
            except Exception:
                pass
            raise
        # Opening switches tabs. Give the UI a moment before the handler validates/selects.
    elif _same_scene(source_view, target_view):
        raise _ArgumentError(
            "Source and target are the same scene tab; Interscene retargeting needs two different tabs.",
            hint="Open the target character in another tab (open_scene) and pass its name as target.",
        )
    return {
        "app": app,
        "manager": manager,
        "original": original,
        "source": source_view,
        "target": target_view,
        "opened": opened,
        "requested_start": start,
        "requested_end": end,
        "dialogs": _dialog_signatures(_actionable_dialogs()),
    }


def _stage_prepare_source(state: dict) -> dict:
    source, target = state["source"], state["target"]
    source_info = _require_rig(source, "source")
    target_info = _require_rig(target, "target")
    first, last = _boundary(source)
    start, end = resolve_frame_range(first, last, state.get("requested_start"), state.get("requested_end"))
    source_metrics = scene_metrics(source)
    if not _source_has_animation(source_metrics, start, end):
        raise _ArgumentError(
            f"The source scene {_view_name(source)!r} has no keyed animation in {start}..{end} "
            "(Cascadeur's Copy reports no frames selected for a static scene).",
            hint=(
                "Use an animated source such as Backflip_animation.casc, or select a range that contains its keys. "
                "Retargeting a still pose would write only one frame, not an animation."
            ),
            action="copy",
            source=_view_name(source),
            frames={"start": start, "end": end},
            source_metrics=source_metrics,
        )
    target_before = scene_metrics(target)
    _switch_to(state["manager"], source)
    layers = _select_frames(source, start, end)
    return {
        "start": start,
        "end": end,
        "source_first": first,
        "source_last": last,
        "source_metrics": source_metrics,
        "source_info": source_info,
        "target_info": target_info,
        "target_before": target_before,
        "source_layers": layers,
    }


def _stage_action(state: dict, action: str, success_marker: str) -> dict:
    common = _common()
    offset = _log_offset(common)
    try:
        _call_action(state["app"], action)
    except Exception as exc:
        raise _ArgumentError(
            f"Cascadeur could not run {action!r}: {exc}",
            hint="Check that no modal dialog is blocking Cascadeur and read get_log.",
            action=action,
        ) from exc
    new_dialogs, clicked = _dismiss_new_dialogs(state.get("dialogs") or set())
    return {"offset": offset, "dialogs": new_dialogs, "clicked": clicked, "marker": success_marker}


def _stage_prepare_target(state: dict) -> dict:
    common = _common()
    target = state["target"]
    _switch_to(state["manager"], target)
    before_last, after_last = _set_last_frame(target, state["end"])
    layers = _select_frames(target, state["start"], state["end"])
    joints = _probe_joints(target)
    frames = list(dict.fromkeys([state["start"], state["start"] + (state["end"] - state["start"]) // 2, state["end"]]))
    before = _sample_pose(common, target, joints, frames)
    return {
        "last_frame_before": before_last,
        "last_frame_after": after_last,
        "target_layers": layers,
        "frames": frames,
        "joints": joints,
        "pose_before": before,
    }


def _stage_finish(state: dict) -> dict:
    common = _common()
    target = state["target"]
    after = scene_metrics(target)
    if not _target_has_animation(after, state["end"]):
        raise _ArgumentError(
            f"Cascadeur logged 'Retargeting: Pasted', but {_view_name(target)!r} still has animation_size "
            f"{after.get('animation_size')} and last_key {after.get('last_key')} for frames "
            f"{state['start']}..{state['end']}; no multi-frame animation was written.",
            hint=(
                "The source may only contain a still pose, or the target timeline rejected the paste. Check get_log "
                "and the target timeline; the source can be checked with get_scene_info."
            ),
            action="paste",
            target=_view_name(target),
            frames={"start": state["start"], "end": state["end"]},
            after=after,
        )
    pose_after = _sample_pose(common, target, state["joints"], state["frames"])
    if sample_has_unavailable(pose_after):
        raise _ArgumentError(
            f"Cascadeur logged 'Retargeting: Pasted', but the target's joint data could not be read at "
            f"frames {state['frames']}; the animation does not cover the requested range.",
            hint="Check the target timeline; set its last frame and retry the paste if it was too short.",
            action="paste",
            target=_view_name(target),
            frames=state["frames"],
        )
    changed, all_changed = pose_changes(state["pose_before"], pose_after, state["joints"])
    source = state["source"]
    target_before = state["target_before"]
    source_block = {
        "scene": _view_name(source),
        "path": _view_path(source) or None,
        "frames": {"start": state["start"], "end": state["end"], "count": state["end"] - state["start"] + 1},
        "boundary": {"first": state["source_first"], "last": state["source_last"]},
        "layers": state["source_layers"],
        "joints": state["source_metrics"].get("joints"),
        **state["source_info"],
    }
    target_block = {
        "scene": _view_name(target),
        "path": _view_path(target) or None,
        "frames": {"start": state["start"], "end": state["end"], "count": state["end"] - state["start"] + 1},
        "boundary": {"first": after["first_frame"], "last": after["last_frame"]},
        "before": {
            "first_frame": target_before["first_frame"],
            "last_frame": target_before["last_frame"],
            "animation_size": target_before["animation_size"],
            "last_key": target_before["last_key"],
        },
        "last_frame_after": state["last_frame_after"],
        "animation_size": after["animation_size"],
        "last_key": after["last_key"],
        "layers": state["target_layers"],
        **state["target_info"],
        "joints": after["joints"],
        "pose": {
            "frames": state["frames"],
            "joints": [name for _object_id, name in state["joints"]],
            "changed_joints": changed,
            "all_changed": all_changed,
            "samples": pose_after,
        },
    }
    result = common.ok(source=source_block, target=target_block)
    if not changed:
        result["hint"] = (
            "Cascadeur logged 'Retargeting: Pasted' but the sampled joints did not change. Render a frame with "
            "render_image to check the result, or retry with a source range that has keys."
        )
    return result


def _stage_finalize(state: dict, success: bool) -> dict:
    """Close a temp source (or restore the user's tab after a failure), then touch nothing else."""
    manager = state["manager"]
    source = state["source"]
    target = state["target"]
    original = state["original"]
    opened = state["opened"]
    name = _view_name(source)
    if opened:
        fallback = target if success else (original if original is not None else target)
        try:
            if not _same_scene(_current(manager), fallback):
                manager.set_current_scene(fallback)
        except Exception:
            pass
        close_result = _scene_files().close_scene(name, discard_changes=True)
        # From here on no SceneManager access: the tab's QML objects are being destroyed.
        return {"opened": True, "close": close_result, "closed": bool(close_result.get("ok"))}
    if not success and original is not None:
        try:
            if not _same_scene(_current(manager), original):
                manager.set_current_scene(original)
                return {"opened": False, "restored": True}
        except Exception as exc:
            return {"opened": False, "restored": False, "error": str(exc)}
    return {"opened": False, "restored": False}


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


def retarget_animation(source: str, target: str | None = None, start=None, end=None) -> dict:
    """Copy animation from one open scene tab and paste it into another (Interscene retargeting)."""
    common = _common()
    started = time.monotonic()
    try:
        source_arg = _scene_argument(source, "source")
        target_arg = None if target is None else _scene_argument(target, "target")
        start = _frame_number(start, "start")
        end = _frame_number(end, "end")
        if start is not None and end is not None and start > end:
            raise _ArgumentError(
                f"start ({start}) must not be after end ({end}).",
                hint="Frames are inclusive; pass start <= end.",
            )
    except ToolError as exc:
        result = common.fail(str(exc), hint=getattr(exc, "hint", None))
        result["seconds"] = round(time.monotonic() - started, 3)
        return result

    dispatch = _dispatch()
    state = None
    result = None
    try:
        state = dispatch.call_on_gui(
            _stage_prepare,
            {"source_arg": source_arg, "target_arg": target_arg, "start": start, "end": end},
            GUI_TIMEOUT,
        )
        time.sleep(SCENE_SETTLE)
        state.update(dispatch.call_on_gui(_stage_prepare_source, {"state": state}, GUI_TIMEOUT))
        time.sleep(SCENE_SETTLE)
        copy_stage = dispatch.call_on_gui(
            _stage_action,
            {"state": state, "action": COPY_ACTION, "success_marker": COPY_MARKER},
            GUI_TIMEOUT,
        )
        copy_event, copy_log = wait_for_retarget_event(common, copy_stage["offset"], COPY_MARKER)
        if copy_event is None:
            raise _ArgumentError(
                "Cascadeur did not log 'Retargeting: Copied' for the copy action.",
                hint="Check get_log; the Copy action may not have run or the source range has no keys.",
                action="copy",
                log=copy_log[-60:],
                dialogs=copy_stage.get("dialogs"),
            )
        if not copy_event["ok"]:
            raise _ArgumentError(
                f"Cascadeur could not copy the source animation: {copy_event['message']}",
                hint=error_hint(copy_event["message"]),
                action="copy",
                marker=copy_event["message"],
                log=copy_log[-60:],
                dialogs=copy_stage.get("dialogs"),
            )
        if copy_stage.get("dialogs"):
            raise _ArgumentError(
                "Cascadeur opened a dialog during retargeting; it was dismissed.",
                hint="Read the dialog text in the result and get_log, then fix the scene or license.",
                action="copy",
                dialogs=copy_stage["dialogs"],
                closed_buttons=copy_stage.get("clicked"),
            )
        time.sleep(SCENE_SETTLE)
        state.update(dispatch.call_on_gui(_stage_prepare_target, {"state": state}, GUI_TIMEOUT))
        time.sleep(SCENE_SETTLE)
        paste_stage = dispatch.call_on_gui(
            _stage_action,
            {"state": state, "action": PASTE_ACTION, "success_marker": PASTE_MARKER},
            GUI_TIMEOUT,
        )
        paste_event, paste_log = wait_for_retarget_event(common, paste_stage["offset"], PASTE_MARKER)
        if paste_event is None:
            raise _ArgumentError(
                "Cascadeur did not log 'Retargeting: Pasted' for the paste action.",
                hint="Check get_log; the target frame range may have no room or the copy clipboard is empty.",
                action="paste",
                log=paste_log[-60:],
                dialogs=paste_stage.get("dialogs"),
            )
        if not paste_event["ok"]:
            raise _ArgumentError(
                f"Cascadeur could not paste the animation: {paste_event['message']}",
                hint=error_hint(paste_event["message"], role="target"),
                action="paste",
                marker=paste_event["message"],
                log=paste_log[-60:],
                dialogs=paste_stage.get("dialogs"),
            )
        if paste_stage.get("dialogs"):
            raise _ArgumentError(
                "Cascadeur opened a dialog during retargeting; it was dismissed.",
                hint="Read the dialog text in the result and get_log, then fix the scene or license.",
                action="paste",
                dialogs=paste_stage["dialogs"],
                closed_buttons=paste_stage.get("clicked"),
            )
        result = dispatch.call_on_gui(_stage_finish, {"state": state}, GUI_TIMEOUT)
    except ToolError as exc:
        # dispatch.call_on_gui only forwards a GUI job's message, so a stage's hint/extra are gone;
        # derive a useful hint from the message instead of returning a bare error.
        hint = getattr(exc, "hint", None) or error_hint(str(exc))
        result = common.fail(str(exc), hint=hint, **getattr(exc, "extra", {}))
    except Exception as exc:
        result = common.fail(
            f"Retargeting failed: {type(exc).__name__}: {exc}",
            hint="Check get_log, both open scenes and the frame range; then retry.",
            traceback=traceback.format_exc(),
            log=common.log_tail(lines=60),
        )

    success = bool(result and result.get("ok"))
    if state is not None and (state.get("opened") or not success):
        try:
            final = dispatch.call_on_gui(_stage_finalize, {"state": state, "success": success}, GUI_TIMEOUT)
        except Exception as exc:
            final = {"opened": state.get("opened"), "closed": False, "error": str(exc)}
        if not success:
            if final.get("error"):
                result["finalize_error"] = final["error"]
            if final.get("opened") and not final.get("closed"):
                result["finalize_error"] = (
                    final.get("close", {}).get("error") or "the temporary source tab did not close"
                )
        elif final.get("opened") and not final.get("closed"):
            close_result = final.get("close") or {}
            result = common.fail(
                "Retargeting succeeded, but the temporary source scene tab could not be closed: "
                f"{close_result.get('error', 'unknown close error')}",
                hint="Close the leftover source tab with close_scene(discard_changes=true), then check the target.",
                retargeted=True,
                close=close_result,
                source=result.get("source"),
                target=result.get("target"),
            )
    result["seconds"] = round(time.monotonic() - started, 3)
    return result


TOOLS = [
    {
        "name": "retarget_animation",
        "description": (
            "Retarget animation between two different character rigs using Cascadeur's Edit > Retargeting Copy/Paste "
            "actions. Use it when a take was made for another skeleton and import_animation cannot map its joints. "
            "source and target are open scene tab names or absolute .casc paths; target defaults to the current tab. "
            "A source path that is not open is opened in a temporary tab and closed afterwards; the target must already "
            "be open because the pasted keys stay in that scene. start and end are inclusive source frames (default: "
            "the source's animation boundary). Both scenes need exactly one RigInfo (AnimationInfo/Rig mode is not "
            "required), must be different tabs, and need a Pro/Studio license; source and target model types must "
            "match. .fbx/.glb/.bvh files are rejected: import the animation into a same-skeleton source scene first. "
            "Returns the source and target scene/frame ranges, target animation_size and last_key before and after, "
            "RigInfo/AnimationInfo counts, and whether sampled pelvis/hand/foot transforms changed. Frames are whole "
            "frame numbers. Cascadeur's native Copy/Paste record their own undo command(s), not an 'MCP: ' step."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Source scene tab name or absolute .casc path (an unopened path opens a temporary tab).",
                },
                "target": {
                    "type": "string",
                    "description": (
                        "Target scene tab name or absolute .casc path; it must already be open. Omit to use the "
                        "current scene."
                    ),
                },
                "start": {
                    "type": "integer",
                    "description": "First source frame to copy (inclusive; default: the source's first frame).",
                },
                "end": {
                    "type": "integer",
                    "description": "Last source frame to copy (inclusive; default: the source's last frame).",
                },
            },
            "required": ["source"],
        },
        "handler": retarget_animation,
    },
]
